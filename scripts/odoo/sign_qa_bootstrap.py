import base64
import logging
from io import BytesIO

from odoo.tools.pdf import PdfWriter

_logger = logging.getLogger(__name__)


def _blank_pdf():
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(stream)
    return base64.b64encode(stream.getvalue())


def bootstrap(env):
    if env.cr.dbname != "odoo_dev":
        msg = "USL Sign QA fixtures may only be installed in odoo_dev."
        raise RuntimeError(msg)
    company = env.company
    if not company.email:
        company.email = "sign@example.test"
    company.write(
        {
            "sign_oca_send_sign_request_copy": True,
            "sign_webauthn_rp_id": "odoo.localhost",
            "sign_webauthn_origins": "http://odoo.localhost:20436",
        },
    )

    sign_admin = env.ref("usl_sign.group_sign_admin")
    sign_user = env.ref("usl_sign.group_sign_user")
    qa_profiles = {
        "valentin": sign_admin,
        "roger@unstaticlabs.com": sign_user,
        "prosper": sign_user,
    }
    users = env["res.users"].with_context(no_reset_password=True).search(
        [
            (
                "login",
                "in",
                list(qa_profiles),
            ),
        ],
    )
    for user in users:
        group = qa_profiles[user.login]
        values = {}
        if group not in user.group_ids:
            values["group_ids"] = [(4, group.id)]
        if values:
            user.write(values)

    template_model = env["sign.oca.template"]
    template = template_model.search(
        [("name", "=", "Routine Agreement - QA"), ("company_id", "=", company.id)],
        order="version desc, id desc",
        limit=1,
    )
    if not template:
        template = template_model.create(
            {
                "name": "Routine Agreement - QA",
                "description": "Synthetic routine agreement for local requester and signer QA.",
                "filename": "routine-agreement-qa.pdf",
                "data": _blank_pdf(),
                "company_id": company.id,
                "default_document_category": "routine_agreement",
            },
        )
        customer = env.ref("sign_oca.sign_role_customer")
        field_layout = [
            (env.ref("sign_oca.sign_field_name"), 12, 70, 28, 5),
            (env.ref("usl_sign.field_date"), 58, 70, 18, 5),
            (env.ref("sign_oca.sign_field_signature"), 12, 80, 34, 8),
        ]
        for field, x, y, width, height in field_layout:
            env["sign.oca.template.item"].create(
                {
                    "template_id": template.id,
                    "field_id": field.id,
                    "role_id": customer.id,
                    "required": True,
                    "page": 1,
                    "position_x": x,
                    "position_y": y,
                    "width": width,
                    "height": height,
                },
            )
        template.get_info()
        template.action_mark_ready()

    env.cr.commit()
    _logger.info(
        "USL Sign QA ready: company=%r, template=%r, requester=valentin, signers=roger/prosper",
        company.name,
        template.display_name,
    )


bootstrap(globals()["env"])
