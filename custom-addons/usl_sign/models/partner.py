from odoo import fields, models
from odoo.exceptions import UserError

from .constants import REQUEST_STATES


class ResPartner(models.Model):
    _inherit = "res.partner"

    signature_request_count = fields.Integer(compute="_compute_signature_summary")
    signature_current_state = fields.Selection(
        [("none", "No request"), *REQUEST_STATES], compute="_compute_signature_summary",
    )
    sign_enrollment_ids = fields.One2many("usl.sign.enrollment", "partner_id")
    sign_enrollment_state = fields.Selection(
        [
            ("not_enrolled", "Not enrolled"),
            ("pending_review", "Identity review required"),
            ("pending_pocket", "Pocket ID connection required"),
            ("active", "Active"),
            ("revoked", "Revoked"),
        ],
        compute="_compute_signature_summary",
    )

    def _signature_request_domain(self):
        self.ensure_one()
        return [
            "|",
            ("record_ref", "=", f"res.partner,{self.id}"),
            ("signer_ids.partner_id", "=", self.id),
        ]

    def _compute_signature_summary(self):
        request_model = self.env["sign.oca.request"]
        for partner in self:
            requests = request_model.search(partner._signature_request_domain())
            partner.signature_request_count = len(requests)
            latest = requests.sorted(
                lambda item: (item.create_date, item.id), reverse=True,
            )[:1]
            partner.signature_current_state = latest.state if latest else "none"
            enrollment = partner.sign_enrollment_ids.sorted(
                lambda item: (item.create_date, item.id), reverse=True,
            )[:1]
            partner.sign_enrollment_state = enrollment.state if enrollment else "not_enrolled"

    def action_view_signature_requests(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "usl_sign.sign_request_action",
        )
        action["domain"] = self._signature_request_domain()
        action["context"] = {"default_record_ref": f"res.partner,{self.id}"}
        return action

    def action_request_signature(self):
        self.ensure_one()
        if not self.env["sign.oca.template"].search_count(
            [("model", "=", "res.partner"), ("active", "=", True)], limit=1,
        ):
            msg = "Create an active Contact signature template first."
            raise UserError(msg)
        action = self.env["ir.actions.actions"]._for_xml_id(
            "sign_oca.sign_oca_template_generate_multi_act_window",
        )
        action["context"] = {"model": "res.partner", "active_ids": self.ids}
        return action

    def action_start_sign_enrollment(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "usl.sign.enrollment",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_partner_id": self.id,
                "default_company_id": self.env.company.id,
            },
        }
