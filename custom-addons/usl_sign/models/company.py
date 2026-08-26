from odoo import api, fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    sign_oca_send_sign_request_copy = fields.Boolean(
        string="Send signers a copy of the final signed document",
        help=(
            "Once the signing application has validated and archived the request, send each "
            "signer the final evidence dossier."
        ),
        default=True,
    )

    sign_default_policy_id = fields.Many2one(
        "usl.sign.policy", string="Default signing policy", ondelete="restrict",
    )
    sign_rfc3161_enabled = fields.Boolean(string="Use independent RFC 3161 timestamping")
    sign_opentimestamps_enabled = fields.Boolean(
        string="Daily Bitcoin existence proof",
        default=True,
        help=(
            "Submit each closed UTC day's signed evidence manifest to "
            "OpenTimestamps. Confirmation is asynchronous and never delays a "
            "signature request's completion."
        ),
    )

    @api.model_create_multi
    def create(self, values_list):
        companies = super().create(values_list)
        for company in companies:
            self.env["usl.sign.service.health"]._ensure_company(company)
        return companies


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    sign_default_policy_id = fields.Many2one(
        related="company_id.sign_default_policy_id", readonly=False,
    )
    sign_rfc3161_enabled = fields.Boolean(
        related="company_id.sign_rfc3161_enabled", readonly=False,
    )
    sign_opentimestamps_enabled = fields.Boolean(
        related="company_id.sign_opentimestamps_enabled", readonly=False,
    )

    def action_open_sign_service_status(self):
        self.ensure_one()
        return self.env["ir.actions.actions"]._for_xml_id(
            "usl_sign.sign_service_status_action",
        )
