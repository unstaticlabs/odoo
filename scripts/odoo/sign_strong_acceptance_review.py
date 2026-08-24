"""Review the Pocket identity and prepare a real Strong signing session."""

import json
import os
from io import BytesIO

from odoo.tools.pdf import PdfWriter
from odoo.addons.usl_sign.services import field_value


enrollment_id = int(os.environ.get("USL_SIGN_ACCEPTANCE_ENROLLMENT_ID", "0"))
if enrollment_id <= 0:
    raise RuntimeError("A Strong acceptance enrolment ID is required")
enrollment = env["usl.sign.enrollment"].browse(enrollment_id).exists()
if not enrollment or enrollment.state != "pending_review":
    raise RuntimeError("Pocket ID did not leave the enrolment pending identity review")
reviewer = env["res.users"].search([("login", "=", "valentin")], limit=1)
if not reviewer or not reviewer.has_group("usl_sign.group_sign_admin"):
    raise RuntimeError("The isolated Valentin Sign administrator is missing")
enrollment.with_user(reviewer).action_confirm_identity()

stream = BytesIO()
writer = PdfWriter()
writer.add_blank_page(width=595, height=842)
writer.write(stream)
pdf = stream.getvalue()
role = env.ref("sign_oca.sign_role_customer")
field = env.ref("sign_oca.sign_field_signature")
policy = env.ref("usl_sign.policy_material_recurring_strong")
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
        "height": 8,
        "value": False,
        "default_value": field.default_value,
        "placeholder": "",
    },
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
                    "sequence": 10,
                },
            ),
        ],
    },
)
sign_request.action_mark_ready()
if sign_request.recommended_trust != "strong_personal":
    raise RuntimeError("The material recurring-signer policy did not recommend Strong")
sign_request.action_send()
signer = sign_request.signer_ids
access_token = signer._issue_access_token()
session_token = signer._exchange_access_token(access_token)
base_url = env["ir.config_parameter"].sudo().get_str("web.base.url").rstrip("/")
env.cr.commit()
payload = {
    "enrollment_id": enrollment.id,
    "request_id": sign_request.id,
    "signer_id": signer.id,
    "signing_url": f"{base_url}/sign/session/{signer.id}/{session_token}",
}
print("USL_SIGN_STRONG_ACCEPTANCE=" + json.dumps(payload, sort_keys=True))
