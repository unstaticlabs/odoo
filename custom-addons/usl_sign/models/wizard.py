from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .constants import TRUST_LEVELS


class SignTemplateGenerate(models.TransientModel):
    _inherit = "sign.oca.template.generate"

    def _default_request_name(self):
        return self.env.context.get("default_request_name") or self.env[
            "sign.oca.template"
        ].browse(self.env.context.get("default_template_id")).name

    def _default_document_category(self):
        template = self.env["sign.oca.template"].browse(
            self.env.context.get("default_template_id"),
        )
        return template.default_document_category or "routine_agreement"

    company_id = fields.Many2one(
        "res.company",
        related="template_id.company_id",
        readonly=True,
    )
    request_name = fields.Char(
        string="Request name",
        default=_default_request_name,
    )
    record_ref = fields.Reference(
        selection="_record_models",
        string="Linked record",
    )
    document_category = fields.Selection(
        [
            ("internal_decision", "Internal decision"),
            ("routine_agreement", "Routine agreement"),
            ("employment", "Employment document"),
            ("intellectual_property", "Intellectual property"),
            ("commercial", "Commercial agreement"),
            ("finance_guarantee", "Financing or guarantee"),
            ("mandate", "Mandate"),
            ("other", "Other"),
        ],
        default=_default_document_category,
        required=True,
    )
    signer_type = fields.Selection(
        [
            ("internal", "Internal user"),
            ("recurring", "Known recurring signer"),
            ("occasional", "Occasional external signer"),
        ],
        default="occasional",
        required=True,
    )
    risk_level = fields.Selection(
        [("low", "Low"), ("material", "Material"), ("maximum", "Maximum")],
        default="low",
        required=True,
    )
    requires_signed_pdf = fields.Boolean(default=True)
    formal_qes_required = fields.Boolean(string="A formal QES is required")
    policy_id = fields.Many2one("usl.sign.policy", readonly=True)
    recommended_trust = fields.Selection(TRUST_LEVELS, readonly=True)
    requested_trust = fields.Selection(TRUST_LEVELS, required=True, default="standard")
    recommendation_reason = fields.Text(readonly=True)
    recommendation_consequence = fields.Text(readonly=True)
    override_reason = fields.Text()
    approval_recommended = fields.Boolean(readonly=True)
    journey_availability = fields.Text(readonly=True)
    external_provider_id = fields.Many2one(
        "usl.sign.external.provider",
        domain="[('active', '=', True), '|', ('company_id', '=', False), ('company_id', '=', company_id)]",
    )

    @api.model
    def _record_models(self):
        return self.env["sign.oca.request"]._sign_business_record_models()

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if values.get("request_name") or not values.get("template_id"):
                continue
            values["request_name"] = self.env["sign.oca.template"].browse(
                values["template_id"],
            ).name
        return super().create(vals_list)

    @api.onchange(
        "template_id",
        "document_category",
        "signer_type",
        "risk_level",
        "requires_signed_pdf",
        "formal_qes_required",
        "signer_ids",
        "requested_trust",
    )
    def _onchange_usl_journey(self):
        self._refresh_usl_journey()

    def _refresh_usl_journey(self):
        for wizard in self:
            previous_recommendation = wizard.recommended_trust
            wizard.approval_recommended = bool(
                wizard.document_category == "internal_decision"
                and not wizard.requires_signed_pdf,
            )
            policy = self.env["usl.sign.policy"].recommend(
                wizard.company_id or self.env.company,
                category=wizard.document_category,
                signer_type=wizard.signer_type,
                risk_level=wizard.risk_level,
                formal_qes=wizard.formal_qes_required,
            )
            recommendation = (
                policy.recommendation
                if policy
                else "qualified_external"
                if wizard.formal_qes_required
                else "standard"
            )
            wizard.policy_id = policy
            wizard.recommended_trust = recommendation
            wizard.recommendation_reason = (
                policy.reason
                if policy
                else "No reviewed policy matches these inputs; the conservative default is shown."
            )
            wizard.recommendation_consequence = (
                policy.consequence
                if policy
                else "A Sign administrator should review the policy before this request is sent."
            )
            if (
                wizard.formal_qes_required
                or not wizard.requested_trust
                or wizard.requested_trust == previous_recommendation
            ):
                wizard.requested_trust = recommendation
            wizard.journey_availability = wizard._journey_availability_note()

    def _journey_availability_note(self):
        self.ensure_one()
        if self.approval_recommended:
            return (
                "An attributable business decision is proportionate; request a decision "
                "instead of creating a signed PDF."
            )
        if self.requested_trust == "strong_personal":
            partners = self.signer_ids.mapped("partner_id")
            enrollment_model = self.env["usl.sign.enrollment"].sudo()
            missing = partners.filtered(
                lambda partner: not enrollment_model.search_count(
                    [
                        ("partner_id", "=", partner.id),
                        ("company_id", "=", self.company_id.id),
                        ("state", "=", "active"),
                    ],
                    limit=1,
                ),
            )
            if missing:
                return (
                    "Strong personal signing is not immediately available for: "
                    + ", ".join(missing.mapped("name"))
                    + ". Create the request for review, then enrol them or choose another justified journey."
                )
            return "Every selected signer has a reviewed signing identity."
        if self.requested_trust == "qualified_external":
            return (
                "Odoo will freeze the exact document and wait for a provider-neutral "
                "external signing journey and independent validation."
            )
        return (
            "The invitation channel, explicit consent and reinforced evidence are used; "
            "the signer does not receive a personal cryptographic certificate."
        )

    def _generate_vals(self):
        self.ensure_one()
        if not self.request_name:
            msg = "Give this request a clear name."
            raise ValidationError(msg)
        self._refresh_usl_journey()
        values = super()._generate_vals()
        signer_commands = values.get("signer_ids", [])
        for index, command in enumerate(signer_commands, start=1):
            command[2]["sequence"] = index * 10
        template = self.template_id
        values.update(
            {
                "name": self.request_name,
                "record_ref": (
                    f"{self.record_ref._name},{self.record_ref.id}"
                    if self.record_ref
                    else False
                ),
                "company_id": template.company_id.id,
                "data": template.with_context(bin_size=False).data,
                "filename": template.filename or f"{template.name}.pdf",
                "policy_id": self.policy_id.id,
                "template_version": template.version,
                "document_category": self.document_category,
                "signer_type": self.signer_type,
                "risk_level": self.risk_level,
                "requires_signed_pdf": self.requires_signed_pdf,
                "formal_qes_required": self.formal_qes_required,
                "requested_trust": self.requested_trust,
                "override_reason": self.override_reason,
                "external_provider_id": self.external_provider_id.id,
                "signing_order": template.signing_order,
                "reminder_days": template.reminder_days,
                "max_reminders": template.max_reminders,
                "responsible_message": self.message,
                "document_ids": [
                    (
                        0,
                        0,
                        {
                            "sequence": document.sequence,
                            "is_annex": document.is_annex,
                            "name": document.name,
                            "filename": document.filename,
                            "data": document.with_context(bin_size=False).data,
                            "mimetype": "application/pdf",
                        },
                    )
                    for document in template.document_ids
                ],
            },
        )
        return values

    def generate(self):
        self.ensure_one()
        self._refresh_usl_journey()
        if self.approval_recommended:
            msg = "This decision does not need a signed PDF. Request a business decision instead."
            raise ValidationError(
                msg,
            )
        if self.formal_qes_required and self.requested_trust != "qualified_external":
            msg = "A formal QES requirement cannot be overridden."
            raise ValidationError(msg)
        if self.requested_trust != self.recommended_trust:
            if not self.override_reason:
                msg = "Record why the recommended journey is overridden."
                raise ValidationError(msg)
            if not self.env.user.has_group("usl_sign.group_sign_trust_override"):
                msg = "Trust-level override access is required."
                raise AccessError(msg)
        request = self._generate()
        return {
            "type": "ir.actions.act_window",
            "name": request.name,
            "res_model": "sign.oca.request",
            "res_id": request.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_request_approval(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("usl_sign.sign_start_action")
        action["context"] = {
            "default_request_type": "decision",
            "default_name": self.template_id.name,
            "default_company_id": self.company_id.id,
        }
        return action
