import logging
from io import BytesIO

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from odoo import fields

from odoo.addons.usl_sign.services import field_value

_logger = logging.getLogger(__name__)


def _pdf(title, subtitle, sections):
    stream = BytesIO()
    document = canvas.Canvas(stream, pagesize=A4, pageCompression=1)
    width, height = A4
    document.setFillColor(HexColor("#714B67"))
    document.rect(0, height - 82, width, 82, stroke=0, fill=1)
    document.setFillColorRGB(1, 1, 1)
    document.setFont("Helvetica-Bold", 21)
    document.drawString(42, height - 48, title)
    document.setFont("Helvetica", 10)
    document.drawString(42, height - 67, subtitle)
    y = height - 122
    document.setFillColor(HexColor("#20242A"))
    for heading, paragraphs in sections:
        document.setFont("Helvetica-Bold", 12)
        document.drawString(42, y, heading)
        y -= 20
        document.setFont("Helvetica", 9.5)
        for paragraph in paragraphs:
            for line in paragraph:
                document.drawString(50, y, line)
                y -= 14
            y -= 5
        y -= 7
    document.setStrokeColor(HexColor("#D9DDE3"))
    document.line(42, 155, width - 42, 155)
    document.setFillColor(HexColor("#68717C"))
    document.setFont("Helvetica", 8)
    document.drawString(42, 137, "Synthetic local QA document — no legal or commercial effect")
    document.save()
    return field_value(stream.getvalue())


def _template(env, *, name, description, category, policy, roles, layout, pdf, annex=False):
    template = env["sign.oca.template"].search(
        [("name", "=", name), ("company_id", "=", env.company.id)],
        order="version desc, id desc",
        limit=1,
    )
    if template:
        return template
    template = env["sign.oca.template"].create(
        {
            "name": name,
            "description": description,
            "filename": f"{name.lower().replace(' ', '-')}.pdf",
            "data": pdf,
            "company_id": env.company.id,
            "default_document_category": category,
            "policy_id": policy.id,
            "signing_order": len(roles) > 1,
        },
    )
    if annex:
        env["usl.sign.template.document"].create(
            {
                "template_id": template.id,
                "sequence": 20,
                "is_annex": True,
                "name": "Routine Agreement — Scope annex",
                "filename": "routine-agreement-scope-annex.pdf",
                "data": _pdf(
                    "Scope annex",
                    "Routine Agreement · QA",
                    [("Deliverables", [["Synthetic deliverable A", "Synthetic deliverable B"]])],
                ),
            },
        )
    for role, fields_layout in zip(roles, layout, strict=True):
        for field, x, y, field_width, field_height, required in fields_layout:
            env["sign.oca.template.item"].create(
                {
                    "template_id": template.id,
                    "field_id": field.id,
                    "role_id": role.id,
                    "required": required,
                    "page": 1,
                    "position_x": x,
                    "position_y": y,
                    "width": field_width,
                    "height": field_height,
                },
            )
    template.get_info()
    template.action_mark_ready()
    return template


def _request_from_template(
    env,
    *,
    name,
    template,
    signers,
    category,
    signer_type,
    risk,
    trust,
    record_ref,
    provider=False,
    send=False,
):
    request = env["sign.oca.request"].search(
        [("name", "=", name), ("company_id", "=", env.company.id)],
        limit=1,
    )
    if request:
        return request
    wizard = env["sign.oca.template.generate"].with_context(
        default_template_id=template.id,
    ).create(
        {
            "template_id": template.id,
            "request_name": name,
            "record_ref": f"{record_ref._name},{record_ref.id}",
            "document_category": category,
            "signer_type": signer_type,
            "risk_level": risk,
            "formal_qes_required": trust == "qualified_external",
            "requested_trust": trust,
            "external_provider_id": provider.id if provider else False,
            "signer_ids": [
                (0, 0, {"role_id": role.id, "partner_id": partner.id})
                for role, partner in signers
            ],
        },
    )
    wizard._refresh_usl_journey()
    request = wizard._generate()
    request.action_mark_ready()
    if send:
        request.action_send()
    return request


def bootstrap(env):
    if env.cr.dbname != "odoo_dev":
        msg = "USL Sign QA fixtures may only be installed in odoo_dev."
        raise RuntimeError(msg)
    company = env.company
    company.write(
        {
            "name": "Unstatic Labs",
            "email": company.email or "sign@example.test",
            "sign_oca_send_sign_request_copy": True,
            "sign_opentimestamps_enabled": True,
        },
    )
    users = {
        user.login: user
        for user in env["res.users"].with_context(active_test=False).search(
            [("login", "in", ["valentin", "roger@unstaticlabs.com"])],
        )
    }
    requester = users.get("valentin")
    signer = users.get("roger@unstaticlabs.com")
    if not requester or not signer:
        msg = "Pocket ID QA users Valentin and Roger must exist before seeding Sign."
        raise RuntimeError(msg)
    sign_admin = env.ref("usl_sign.group_sign_admin")
    sign_user = env.ref("usl_sign.group_sign_user")
    requester.write({"group_ids": [(4, sign_admin.id)]})
    signer.write({"group_ids": [(4, sign_user.id)]})

    customer = env.ref("sign_oca.sign_role_customer")
    employee = env.ref("sign_oca.sign_role_employee")
    signature = env.ref("sign_oca.sign_field_signature")
    name_field = env.ref("sign_oca.sign_field_name")
    email = env.ref("sign_oca.sign_field_email")
    checkbox = env.ref("sign_oca.sign_field_check")
    date = env.ref("usl_sign.field_date")
    company_field = env.ref("usl_sign.field_company")
    role_field = env.ref("usl_sign.field_role")
    routine_policy = env.ref("usl_sign.policy_routine_standard")
    strong_policy = env.ref("usl_sign.policy_material_recurring_strong")
    qualified_policy = env.ref("usl_sign.policy_maximum_qualified")

    partner_model = env["res.partner"].with_user(requester)
    supplier = partner_model.search(
        [("name", "=", "Northstar Supplies — QA"), ("company_id", "=", company.id)],
        limit=1,
    ) or partner_model.create(
        {
            "name": "Northstar Supplies — QA",
            "email": "northstar@example.test",
            "company_id": company.id,
        },
    )
    creator = partner_model.search(
        [("name", "=", "Avery Creator — QA"), ("company_id", "=", company.id)],
        limit=1,
    ) or partner_model.create(
        {
            "name": "Avery Creator — QA",
            "email": "avery@example.test",
            "company_id": company.id,
        },
    )
    mandate_counterparty = partner_model.search(
        [("name", "=", "Atlas Mandate — QA"), ("company_id", "=", company.id)],
        limit=1,
    ) or partner_model.create(
        {
            "name": "Atlas Mandate — QA",
            "email": "atlas@example.test",
            "company_id": company.id,
        },
    )

    routine = _template(
        env,
        name="Routine Agreement — QA",
        description="Two-party routine agreement with ordered signers and a scope annex.",
        category="routine_agreement",
        policy=routine_policy,
        roles=[customer, employee],
        layout=[
            [
                (name_field, 10, 67, 27, 5, True),
                (email, 10, 73, 32, 5, True),
                (date, 10, 79, 18, 5, True),
                (signature, 10, 86, 32, 8, True),
            ],
            [
                (company_field, 55, 67, 30, 5, True),
                (role_field, 55, 73, 26, 5, True),
                (checkbox, 55, 80, 5, 5, True),
                (signature, 55, 86, 32, 8, True),
            ],
        ],
        pdf=_pdf(
            "Routine Agreement",
            "Requester and collaborator · local QA",
            [
                ("Purpose", [["The parties agree to collaborate on the synthetic QA scope."]]),
                ("Terms", [["1. Limited routine engagement.", "2. No production data or legal effect."]]),
                ("Signatures", [["Each party reviews the full envelope before signing."]]),
            ],
        ),
        annex=True,
    )
    sole_shareholder_document = _template(
        env,
        name="Sole Shareholder Decision — Timestamp QA",
        description="Self-signing document used to review existence and timestamp proof.",
        category="other",
        policy=routine_policy,
        roles=[employee],
        layout=[[(name_field, 12, 76, 30, 5, True), (date, 52, 76, 18, 5, True), (signature, 12, 85, 36, 8, True)]],
        pdf=_pdf(
            "Sole Shareholder Decision",
            "Timestamp-attestation journey · local QA",
            [("Resolution", [["The sole shareholder records this synthetic decision for QA."]])],
        ),
    )
    strong = _template(
        env,
        name="Strong IP Assignment — QA",
        description="Material assignment requiring reviewed identity and a fresh passkey.",
        category="intellectual_property",
        policy=strong_policy,
        roles=[customer],
        layout=[[(name_field, 12, 74, 28, 5, True), (date, 52, 74, 18, 5, True), (signature, 12, 84, 38, 8, True)]],
        pdf=_pdf(
            "Intellectual Property Assignment",
            "Strong personal signature · local QA",
            [("Assignment", [["The assignor transfers the synthetic QA work product."]])],
        ),
    )
    qualified = _template(
        env,
        name="Qualified External Mandate — QA",
        description="Provider-neutral manual QES export and import example.",
        category="mandate",
        policy=qualified_policy,
        roles=[customer],
        layout=[[(name_field, 12, 74, 28, 5, True), (date, 52, 74, 18, 5, True), (signature, 12, 84, 38, 8, True)]],
        pdf=_pdf(
            "Sensitive Mandate",
            "Qualified external signature · local QA",
            [("Mandate", [["This synthetic mandate demonstrates provider-neutral export and import."]])],
        ),
    )
    del sole_shareholder_document

    provider = env["usl.sign.external.provider"].search(
        [("name", "=", "EU qualified provider — QA catalog example")],
        limit=1,
    )
    if not provider:
        provider = env["usl.sign.external.provider"].with_user(requester).create(
            {
                "name": "EU qualified provider — QA catalog example",
                "company_id": company.id,
                "territory": "European Union (synthetic catalog entry)",
                "supported_levels": "QES",
                "mobile_url": "https://example.invalid/qualified-signature",
                "instructions": "<p>Download the frozen PDF, complete the reviewed provider journey on mobile, then import both the signed PDF and provider proof package.</p>",
                "commercial_notes": "QA-only provider-neutral catalog example. Do not purchase or rely on this entry.",
                "recommendation_priority": 10,
                "reviewed_on": fields.Date.today(),
                "reviewed_by_id": requester.id,
            },
        )

    requester_env = env(user=requester.id)
    routine_request = _request_from_template(
        requester_env,
        name="Routine Agreement — ready to send",
        template=routine,
        signers=[(customer, signer.partner_id), (employee, requester.partner_id)],
        category="routine_agreement",
        signer_type="recurring",
        risk="low",
        trust="standard",
        record_ref=supplier,
    )
    strong_request = _request_from_template(
        requester_env,
        name="Strong IP Assignment — enrolment required",
        template=strong,
        signers=[(customer, signer.partner_id)],
        category="intellectual_property",
        signer_type="recurring",
        risk="material",
        trust="strong_personal",
        record_ref=creator,
        send=True,
    )
    qualified_request = _request_from_template(
        requester_env,
        name="Qualified External Mandate — waiting for provider",
        template=qualified,
        signers=[(customer, signer.partner_id)],
        category="mandate",
        signer_type="occasional",
        risk="maximum",
        trust="qualified_external",
        record_ref=mandate_counterparty,
        provider=provider,
        send=True,
    )

    # Real-device identity acceptance may activate the synthetic signer before
    # this idempotent bootstrap is rerun.  Keep the demo request aligned with
    # that current identity state so Roger receives a genuine signing action
    # instead of a stale "waiting for enrolment" example.
    if strong_request.state == "waiting_enrollment" and not strong_request.signer_ids.filtered(
        lambda row: not row._active_enrollment(),
    ):
        strong_request.action_resume_after_enrollment()

    health = env["usl.sign.service.health"]._ensure_company(company)
    health.with_user(requester)._refresh_checks()
    env.cr.commit()
    _logger.info(
        "Sign QA ready: templates=%s routine_request=%s strong_request=%s qualified_request=%s",
        len(env["sign.oca.template"].search([("company_id", "=", company.id)])),
        routine_request.id,
        strong_request.id,
        qualified_request.id,
    )


bootstrap(globals()["env"])
