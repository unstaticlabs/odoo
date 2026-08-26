from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .constants import DOCUMENT_CATEGORIES, INTERNAL_OPERATION

SHORT_TRUST_LEVELS = [
    ("standard", "Standard"),
    ("strong_personal", "Strong personal"),
    ("qualified_external", "Qualified external"),
]


class SignShareConfirm(models.TransientModel):
    _name = "usl.sign.share.confirm"
    _description = "Confirm access for invited Odoo users"

    request_id = fields.Many2one(
        "sign.oca.request",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    recipient_names = fields.Char(readonly=True, required=True)
    recipient_count = fields.Integer(readonly=True, required=True)
    message = fields.Text(readonly=True)

    def action_confirm(self):
        self.ensure_one()
        self.request_id._check_owner_access()
        sign_group = self.env.ref("usl_sign.group_sign_user")
        internal_users = self.request_id._internal_signer_users()
        users_needing_access = internal_users.filtered(
            lambda user: sign_group not in user.all_group_ids,
        )
        users_needing_access.sudo().write(
            {"group_ids": [Command.link(sign_group.id)]},
        )
        return self.request_id.with_context(
            usl_sign_share_confirmed=INTERNAL_OPERATION,
        ).action_send(message=self.message)


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
        string="Document name",
        default=_default_request_name,
    )
    record_ref = fields.Reference(
        selection="_record_models",
        string="Linked record",
    )
    document_category = fields.Selection(
        DOCUMENT_CATEGORIES,
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
        string="Relationship to signer",
    )
    risk_level = fields.Selection(
        [("low", "Low"), ("material", "Material"), ("maximum", "Maximum")],
        default="low",
        required=True,
        string="Business risk",
    )
    formal_qes_required = fields.Boolean(string="A formal QES is required")
    policy_id = fields.Many2one("usl.sign.policy", readonly=True)
    recommended_trust = fields.Selection(
        SHORT_TRUST_LEVELS,
        string="Recommended method",
        readonly=True,
    )
    requested_trust = fields.Selection(
        SHORT_TRUST_LEVELS,
        string="Signing method",
        required=True,
        default="standard",
    )
    recommendation_reason = fields.Text(readonly=True)
    recommendation_consequence = fields.Text(readonly=True)
    override_reason = fields.Text()
    journey_availability = fields.Text(string="Before you continue", readonly=True)
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
        "formal_qes_required",
        "signer_ids",
        "requested_trust",
    )
    def _onchange_usl_journey(self):
        self._refresh_usl_journey()

    def _refresh_usl_journey(self):
        for wizard in self:
            previous_recommendation = wizard.recommended_trust
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
                    "Strong signing is not ready for: "
                    + ", ".join(missing.mapped("name"))
                    + ". An Identity Reviewer must send each person a setup link. "
                    "They connect Pocket ID, the reviewer approves the identity, "
                    "and only then can they sign."
                )
            return "Ready. Every signer has an approved signing identity."
        if self.requested_trust == "qualified_external":
            return (
                "The PDF will be locked here, signed by the provider, then checked on return."
            )
        return "Ready. Each signer receives a private link to review and sign."

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


class SignRequestMethod(models.TransientModel):
    _name = "usl.sign.request.method"
    _description = "Choose a signing method"

    request_id = fields.Many2one("sign.oca.request", required=True, readonly=True)
    company_id = fields.Many2one(related="request_id.company_id", readonly=True)
    document_category = fields.Selection(DOCUMENT_CATEGORIES, required=True)
    signer_type = fields.Selection(
        [
            ("internal", "Internal user"),
            ("recurring", "Known recurring signer"),
            ("occasional", "Occasional external signer"),
        ],
        required=True,
        string="Relationship to signer",
    )
    risk_level = fields.Selection(
        [("low", "Low"), ("material", "Material"), ("maximum", "Maximum")],
        required=True,
        string="Business risk",
    )
    formal_qes_required = fields.Boolean()
    requested_trust = fields.Selection(
        SHORT_TRUST_LEVELS,
        string="Signing method",
        required=True,
    )
    recommended_trust = fields.Selection(
        SHORT_TRUST_LEVELS,
        string="Recommended method",
        readonly=True,
    )
    recommendation_reason = fields.Text(readonly=True)
    override_reason = fields.Text()
    selected_method_summary = fields.Char(
        compute="_compute_method_presentation",
        readonly=True,
    )
    selected_method_readiness = fields.Char(
        compute="_compute_method_presentation",
        readonly=True,
    )
    can_override_trust = fields.Boolean(
        compute="_compute_can_override_trust",
        readonly=True,
    )
    external_provider_id = fields.Many2one(
        "usl.sign.external.provider",
        domain="[('active', '=', True), '|', ('company_id', '=', False), ('company_id', '=', company_id)]",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            request = self.env["sign.oca.request"].browse(values.get("request_id"))
            if request:
                values.setdefault("document_category", request.document_category)
                values.setdefault("signer_type", request.signer_type)
                values.setdefault("risk_level", request.risk_level)
                values.setdefault("formal_qes_required", request.formal_qes_required)
                values.setdefault("requested_trust", request.requested_trust)
                values.setdefault("recommended_trust", request.recommended_trust)
                values.setdefault("recommendation_reason", request.recommendation_reason)
                values.setdefault("override_reason", request.override_reason)
                values.setdefault("external_provider_id", request.external_provider_id.id)
        return super().create(vals_list)

    @api.depends_context("uid")
    def _compute_can_override_trust(self):
        allowed = self.env.user.has_group("usl_sign.group_sign_trust_override")
        for wizard in self:
            wizard.can_override_trust = allowed

    @api.depends("requested_trust", "external_provider_id", "request_id.signer_ids")
    def _compute_method_presentation(self):
        for wizard in self:
            if wizard.requested_trust == "strong_personal":
                wizard.selected_method_summary = _(
                    "Each known signer confirms with Pocket ID before a personal "
                    "digital signature is added.",
                )
                partners = wizard.request_id.signer_ids.mapped("partner_id")
                enrolled_partner_ids = set(
                    self.env["usl.sign.enrollment"]
                    .sudo()
                    .search(
                        [
                            ("company_id", "=", wizard.company_id.id),
                            ("partner_id", "in", partners.ids),
                            ("state", "=", "active"),
                        ],
                    )
                    .mapped("partner_id")
                    .ids,
                )
                missing = partners.filtered(
                    lambda partner: partner.id not in enrolled_partner_ids,
                )
                if not partners:
                    wizard.selected_method_readiness = _(
                        "Add the signers before checking identity readiness.",
                    )
                elif missing:
                    wizard.selected_method_readiness = _(
                        "Not ready for %(names)s. An Identity Reviewer must send a "
                        "personal setup link; each signer connects Pocket ID and is "
                        "approved before signing.",
                        names=", ".join(missing.mapped("display_name")),
                    )
                else:
                    wizard.selected_method_readiness = _(
                        "Every signer has an approved signing identity.",
                    )
            elif wizard.requested_trust == "qualified_external":
                wizard.selected_method_summary = _(
                    "The frozen PDF is signed through an external qualified provider, "
                    "then returned and checked here.",
                )
                wizard.selected_method_readiness = (
                    _(
                        "Provider selected: %(provider)s.",
                        provider=wizard.external_provider_id.display_name,
                    )
                    if wizard.external_provider_id
                    else _("Choose a qualified provider below.")
                )
            else:
                wizard.selected_method_summary = _(
                    "Signers use private links. Their consent and actions are recorded, "
                    "and the final PDF is independently checked.",
                )
                wizard.selected_method_readiness = _(
                    "Ready once every signer and required field is set.",
                )

    @api.onchange(
        "document_category",
        "signer_type",
        "risk_level",
        "formal_qes_required",
    )
    def _onchange_policy_inputs(self):
        for wizard in self:
            previous = wizard.recommended_trust
            policy = self.env["usl.sign.policy"].recommend(
                wizard.company_id,
                category=wizard.document_category,
                signer_type=wizard.signer_type,
                risk_level=wizard.risk_level,
                formal_qes=wizard.formal_qes_required,
            )
            recommended = (
                policy.recommendation
                if policy
                else "qualified_external"
                if wizard.formal_qes_required
                else "standard"
            )
            wizard.recommended_trust = recommended
            wizard.recommendation_reason = (
                policy.reason
                if policy
                else "No reviewed company rule matches this document. The safest available default is shown."
            )
            if (
                wizard.formal_qes_required
                or not wizard.requested_trust
                or wizard.requested_trust == previous
            ):
                wizard.requested_trust = recommended

    def action_apply(self):
        self.ensure_one()
        request = self.request_id
        request._check_owner_access()
        if request.state != "draft":
            msg = "The signing method is fixed after the request is prepared."
            raise ValidationError(msg)
        self._onchange_policy_inputs()
        if self.formal_qes_required and self.requested_trust != "qualified_external":
            msg = "This document requires a qualified external signature."
            raise ValidationError(msg)
        if self.requested_trust != self.recommended_trust:
            if not self.override_reason:
                msg = "Explain why you are choosing a different signing method."
                raise ValidationError(msg)
            if not self.env.user.has_group("usl_sign.group_sign_trust_override"):
                msg = "You do not have permission to override the recommended method."
                raise AccessError(msg)
        if self.requested_trust == "qualified_external" and not self.external_provider_id:
            msg = "Choose a qualified provider before continuing."
            raise ValidationError(msg)
        request.write(
            {
                "document_category": self.document_category,
                "signer_type": self.signer_type,
                "risk_level": self.risk_level,
                "formal_qes_required": self.formal_qes_required,
                "requested_trust": self.requested_trust,
                "override_reason": self.override_reason,
                "external_provider_id": self.external_provider_id.id
                if self.requested_trust == "qualified_external"
                else False,
            },
        )
        request.action_compute_recommendation(apply_timing_defaults=True)
        return {"type": "ir.actions.act_window_close"}
