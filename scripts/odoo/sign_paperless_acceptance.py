# ruff: noqa: F821, T201 -- Odoo shell injects env; stdout is the test result.
"""Exercise a real Standard Sign completion and Paperless recovery journey."""

import hashlib
import json
import time
import uuid
from io import BytesIO

from psycopg2.errors import SerializationFailure

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
        if request.archive_status in {"archived", "failed"}:
            return request.archive_status
        try:
            request._reconcile_archive()
            env.cr.commit()
        except SerializationFailure:
            # The running Odoo worker may finish the same queued operation
            # between our status read and reconciliation write. Retry from a
            # fresh transaction and observe its committed result.
            env.cr.rollback()
        time.sleep(1)
    return request.archive_status


def _complete_standard(label):
    raw_pdf = _pdf()
    roles = env.ref("sign_oca.sign_role_customer") | env.ref(
        "sign_oca.sign_role_employee",
    )
    field = env.ref("sign_oca.sign_field_name")
    policy = env.ref("usl_sign.policy_routine_standard")
    partners = env["res.partner"]
    for sequence in range(1, 3):
        email = f"qa-sign-archive-{sequence}@preproduction.invalid"
        partner = env["res.partner"].search([("email", "=", email)], limit=1)
        partners |= partner or env["res.partner"].create(
            {
                "name": f"QA Sign Archive Signer {sequence}",
                "email": email,
            },
        )
    layout = {
        str(sequence): {
            "id": sequence,
            "field_id": field.id,
            "field_type": field.field_type,
            "required": True,
            "name": field.name,
            "role_id": role.id,
            "page": 1,
            "position_x": 12,
            "position_y": 12 + sequence * 12,
            "width": 30,
            "height": 6,
            "value": False,
            "default_value": field.default_value,
            "placeholder": "",
        }
        for sequence, role in enumerate(roles, start=1)
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
                        "sequence": sequence * 10,
                    },
                )
                for sequence, (partner, role) in enumerate(
                    zip(partners, roles, strict=True),
                    start=1,
                )
            ],
        },
    )
    request.action_mark_ready()
    request._freeze_document()
    request._transition("sent", "request_sent", payload={"qa_acceptance": True})
    for signer in request.signer_ids.sorted(lambda row: (row.sequence, row.id)):
        access_token = signer._issue_access_token()
        session_token = signer._exchange_access_token(access_token)
        items = json.loads(json.dumps(request.frozen_layout))
        for key, item in items.items():
            if int(item["role_id"]) == signer.role_id.id:
                items[key]["value"] = signer.partner_id.name
        reviewed_document_sha256 = hashlib.sha256(field_content(request.data)).hexdigest()
        signer.action_sign(
            items,
            access_token=session_token,
            document_sha256=reviewed_document_sha256,
            consent=True,
            location={"status": "refused"},
            browser_context={
                "language": "en-US",
                "platform": "synthetic-release-acceptance",
                "timezone": "UTC",
            },
        )
    env.cr.commit()
    request.invalidate_recordset()
    return request


def _assert_standard_topology(request):
    final_pdf = field_content(request.final_data)
    validation = request._sign_dss_client().validate(
        final_pdf,
        expected_level="standard",
    )
    cross_validation = request._sign_dss_client().cross_validate(final_pdf)
    if (
        validation.get("status") != "valid"
        or validation.get("signatureCount") != 1
        or cross_validation.get("status") != "valid"
        or cross_validation.get("signature_count") != 1
        or len(request.signer_ids) != 2
        or any(signer.state != "signed" for signer in request.signer_ids)
    ):
        msg = "The Standard acceptance topology is not two attestations and one seal"
        raise RuntimeError(msg)
    signing_summary = next(
        artifact
        for artifact in request._dossier_artifacts_v2()
        if artifact["kind"] == "signing_summary"
    )
    summary = json.loads(signing_summary["content"])
    if summary["proof"] != {
        "signer_attestations": 2,
        "personal_pades_signatures": 0,
        "platform_integrity_seals": 1,
        "expected_pdf_signature_count": 1,
    }:
        msg = "The Standard dossier describes the wrong proof semantics"
        raise RuntimeError(msg)
    return {
        "signer_attestations": 2,
        "pdf_signatures": 1,
        "platform_integrity_seals": 1,
    }


params = env["ir.config_parameter"].sudo()
paperless_url = params.get_str("usl_documents.paperless_url", "")
if not paperless_url or not params.get_str("usl_documents.paperless_token", ""):
    msg = "Paperless is not configured for the Sign acceptance"
    raise RuntimeError(msg)

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
    direct_topology = _assert_standard_topology(direct)
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
        msg = "A checksum-identical dossier was not reused"
        raise RuntimeError(msg)

    params.set_str("usl_documents.paperless_url", "http://127.0.0.1:9")
    recovery = _complete_standard(f"QA Sign archive recovery {run_id}")
    if (
        recovery.state != "evidence_incomplete"
        or recovery.archive_status != "failed"
        or recovery.completed_at
    ):
        msg = "The Paperless outage did not fail closed"
        raise RuntimeError(msg)
    params.set_str("usl_documents.paperless_url", paperless_url)
    env.cr.commit()
    recovery.action_retry_archive()
    env.cr.commit()
    if _poll_until_archived(recovery) != "archived" or recovery.state != "completed":
        raise RuntimeError(
            f"Archive recovery did not complete: {recovery.state}/{recovery.archive_status}",
        )
    recovery_topology = _assert_standard_topology(recovery)

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
                    "topology": direct_topology,
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
                    "topology": recovery_topology,
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
