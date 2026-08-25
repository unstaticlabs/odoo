"""Exercise a real Standard Sign completion and Paperless recovery journey."""

import hashlib
import json
import time
import uuid
from io import BytesIO

from odoo.tools.pdf import PdfWriter
from odoo.addons.usl_sign.services import base64_text, field_content, field_value


def _pdf():
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(stream)
    return stream.getvalue()


def _poll_until_archived(request, *, attempts=30):
    for _attempt in range(attempts):
        request.invalidate_recordset()
        request._reconcile_archive()
        env.cr.commit()
        request.invalidate_recordset()
        if request.archive_status in {"archived", "failed"}:
            return request.archive_status
        time.sleep(1)
    return request.archive_status


def _complete_standard(label):
    raw_pdf = _pdf()
    role = env.ref("sign_oca.sign_role_customer")
    field = env.ref("sign_oca.sign_field_name")
    policy = env.ref("usl_sign.policy_routine_standard")
    partner = env["res.partner"].search(
        [("email", "=", "qa-sign-archive@preproduction.invalid")],
        limit=1,
    ) or env["res.partner"].create(
        {
            "name": "QA Sign Archive Signer",
            "email": "qa-sign-archive@preproduction.invalid",
        },
    )
    layout = {
        "1": {
            "id": 1,
            "field_id": field.id,
            "field_type": field.field_type,
            "required": True,
            "name": field.name,
            "role_id": role.id,
            "page": 1,
            "position_x": 12,
            "position_y": 18,
            "width": 30,
            "height": 6,
            "value": False,
            "default_value": field.default_value,
            "placeholder": "",
        },
    }
    request = env["sign.oca.request"].create(
        {
            "name": label,
            "data": field_value(raw_pdf),
            "filename": f"{label}.pdf",
            "company_id": env.company.id,
            "user_id": env.user.id,
            "policy_id": policy.id,
            "signatory_data": layout,
            "signer_ids": [
                (
                    0,
                    0,
                    {
                        "partner_id": partner.id,
                        "role_id": role.id,
                        "sequence": 10,
                    },
                ),
            ],
        },
    )
    request.action_mark_ready()
    request._freeze_document()
    request._transition("sent", "request_sent", payload={"qa_acceptance": True})
    signer = request.signer_ids
    access_token = signer._issue_access_token()
    session_token = signer._exchange_access_token(access_token)
    items = json.loads(json.dumps(request.frozen_layout))
    items["1"]["value"] = "QA Sign Archive Signer"
    reviewed_document_sha256 = hashlib.sha256(field_content(request.data)).hexdigest()
    signer.action_sign(
        items,
        access_token=session_token,
        document_sha256=reviewed_document_sha256,
        consent=True,
    )
    env.cr.commit()
    request.invalidate_recordset()
    return request


params = env["ir.config_parameter"].sudo()
paperless_url = params.get_str("usl_documents.paperless_url", "")
if not paperless_url or not params.get_str("usl_documents.paperless_token", ""):
    raise RuntimeError("Paperless is not configured for the Sign acceptance")

company = env.company
original_company_email = company.email
original_delivery = company.sign_oca_send_sign_request_copy
company.write(
    {
        "email": company.email or "sign-qa@preproduction.invalid",
        "sign_oca_send_sign_request_copy": False,
    },
)
run_id = uuid.uuid4().hex[:10]

try:
    direct = _complete_standard(f"QA Sign archive direct {run_id}")
    if _poll_until_archived(direct) != "archived" or direct.state != "completed":
        raise RuntimeError(
            f"Direct archive did not complete: {direct.state}/{direct.archive_status}",
        )
    duplicate = env["usl.document"].sudo().upload_from_odoo(
        direct.dossier_filename,
        base64_text(field_content(direct.dossier_data)),
        "application/pdf",
        res_model=direct._name,
        res_id=direct.id,
        company_id=direct.company_id.id,
        confidentiality="private",
        source="odoo_generated",
    )
    if (
        duplicate.get("state") != "duplicate"
        or duplicate.get("document_id") != direct.archive_dossier_document_id.id
    ):
        raise RuntimeError("A checksum-identical dossier was not reused")

    params.set_str("usl_documents.paperless_url", "http://127.0.0.1:9")
    recovery = _complete_standard(f"QA Sign archive recovery {run_id}")
    if (
        recovery.state != "evidence_incomplete"
        or recovery.archive_status != "failed"
        or recovery.completed_at
    ):
        raise RuntimeError("The Paperless outage did not fail closed")
    params.set_str("usl_documents.paperless_url", paperless_url)
    env.cr.commit()
    recovery.action_retry_archive()
    env.cr.commit()
    if _poll_until_archived(recovery) != "archived" or recovery.state != "completed":
        raise RuntimeError(
            f"Archive recovery did not complete: {recovery.state}/{recovery.archive_status}",
        )

    direct_dossier = field_content(direct.dossier_data)
    recovery_dossier = field_content(recovery.dossier_data)
    print(
        json.dumps(
            {
                "direct": {
                    "archive_document_id": direct.archive_document_id.id,
                    "archive_dossier_document_id": (
                        direct.archive_dossier_document_id.id
                    ),
                    "archive_status": direct.archive_status,
                    "dossier_sha256": hashlib.sha256(direct_dossier).hexdigest(),
                    "request_id": direct.id,
                    "state": direct.state,
                },
                "duplicate_reused": True,
                "recovery": {
                    "archive_document_id": recovery.archive_document_id.id,
                    "archive_dossier_document_id": (
                        recovery.archive_dossier_document_id.id
                    ),
                    "archive_status": recovery.archive_status,
                    "dossier_sha256": hashlib.sha256(recovery_dossier).hexdigest(),
                    "request_id": recovery.id,
                    "state": recovery.state,
                },
                "run_id": run_id,
            },
            indent=2,
            sort_keys=True,
        ),
    )
finally:
    params.set_str("usl_documents.paperless_url", paperless_url)
    company.write(
        {
            "email": original_company_email,
            "sign_oca_send_sign_request_copy": original_delivery,
        },
    )
    env.cr.commit()
