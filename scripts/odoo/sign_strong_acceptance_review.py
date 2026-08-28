# ruff: noqa: F821, T201 -- Odoo shell injects env; stdout is wrapper input.
"""Review the Pocket identity and prepare a real Strong signing session."""

import json
import os
from io import BytesIO

from odoo.tools.pdf import PdfWriter

from odoo.addons.usl_sign.services import field_value

raw_enrollment_ids = os.environ.get("USL_SIGN_ACCEPTANCE_ENROLLMENT_IDS") or os.environ.get(
    "USL_SIGN_ACCEPTANCE_ENROLLMENT_ID",
    "",
)
try:
    enrollment_ids = [
        int(value) for value in raw_enrollment_ids.split(",") if value.strip()
    ]
except ValueError as error:
    msg = "Strong acceptance enrolment IDs must be numeric"
    raise RuntimeError(msg) from error
if len(enrollment_ids) != 2 or any(enrollment_id <= 0 for enrollment_id in enrollment_ids):
    msg = "Exactly two Strong acceptance enrolment IDs are required"
    raise RuntimeError(msg)
enrollments = env["usl.sign.enrollment"].browse(enrollment_ids).exists()
if len(enrollments) != 2 or any(enrollment.state != "pending_review" for enrollment in enrollments):
    msg = "Pocket ID did not leave both enrolments pending identity review"
    raise RuntimeError(msg)
reviewer = env["res.users"].search([("login", "=", "valentin")], limit=1)
if not reviewer or not reviewer.has_group("usl_sign.group_sign_admin"):
    msg = "The isolated Valentin Sign administrator is missing"
    raise RuntimeError(msg)
for enrollment in enrollments:
    enrollment.with_user(reviewer).action_confirm_identity()

stream = BytesIO()
writer = PdfWriter()
writer.add_blank_page(width=595, height=842)
writer.write(stream)
pdf = stream.getvalue()
roles = env.ref("sign_oca.sign_role_customer") | env.ref("sign_oca.sign_role_employee")
field = env.ref("sign_oca.sign_field_signature")
policy = env.ref("usl_sign.policy_material_recurring_strong")
layout = {}
item_id = 0
for sequence, role in enumerate(roles, start=1):
    for placement in range(2):
        item_id += 1
        layout[str(item_id)] = {
            "id": item_id,
            "field_id": field.id,
            "field_type": field.field_type,
            "required": True,
            "name": field.name,
            "role_id": role.id,
            "page": 1,
            "position_x": 12 + placement * 34,
            "position_y": 12 + sequence * 12,
            "width": 30,
            "height": 8,
            "value": False,
            "default_value": field.default_value,
            "placeholder": "",
        }
run_id = os.environ.get("USL_SIGN_ACCEPTANCE_RUN_ID", "qa")
sign_request = env["sign.oca.request"].with_user(reviewer).create(
    {
        "name": f"QA Pocket Strong agreement {run_id}",
        "data": field_value(pdf),
        "filename": f"qa-pocket-strong-{run_id}.pdf",
        "company_id": env.company.id,
        "user_id": reviewer.id,
        "document_category": "commercial",
        "signer_type": "recurring",
        "risk_level": "material",
        "policy_id": policy.id,
        "requested_trust": "strong_personal",
        "signing_order": True,
        "signatory_data": layout,
        "signer_ids": [
            (
                0,
                0,
                {
                    "partner_id": enrollment.partner_id.id,
                    "role_id": role.id,
                    "sequence": sequence * 10,
                },
            )
            for sequence, (enrollment, role) in enumerate(
                zip(enrollments, roles, strict=True),
                start=1,
            )
        ],
    },
)
sign_request.action_mark_ready()
if sign_request.recommended_trust != "strong_personal":
    msg = "The material recurring-signer policy did not recommend Strong"
    raise RuntimeError(msg)
send_action = sign_request.action_send()
if (
    isinstance(send_action, dict)
    and send_action.get("res_model") == "usl.sign.share.confirm"
):
    env["usl.sign.share.confirm"].browse(send_action["res_id"]).action_confirm()
if sign_request.state != "sent":
    raise RuntimeError(f"The Strong request was not sent: {sign_request.state}")
signer = sign_request.signer_ids.sorted(lambda row: (row.sequence, row.id))[:1]
access_token = signer._issue_access_token()
session_token = signer._exchange_access_token(access_token)
base_url = env["ir.config_parameter"].sudo().get_str("web.base.url").rstrip("/")
env.cr.commit()
payload = {
    "enrollment_ids": enrollments.ids,
    "request_id": sign_request.id,
    "signer_id": signer.id,
    "username": "roger",
    "signing_url": f"{base_url}/sign/session/{signer.id}/{session_token}?review=1",
}
print("USL_SIGN_STRONG_ACCEPTANCE=" + json.dumps(payload, sort_keys=True))
