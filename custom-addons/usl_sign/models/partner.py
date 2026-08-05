from odoo import fields, models
from odoo.exceptions import UserError


class ResPartner(models.Model):
    _inherit = "res.partner"

    signature_request_count = fields.Integer(
        compute="_compute_signature_request_summary"
    )
    signature_current_state = fields.Selection(
        [
            ("none", "No request"),
            ("draft", "Draft"),
            ("in_progress", "In progress"),
            ("completed", "Completed"),
            ("attention", "Action required"),
        ],
        compute="_compute_signature_request_summary",
    )

    def _signature_request_domain(self):
        self.ensure_one()
        return [("record_ref", "=", f"res.partner,{self.id}")]

    def _compute_signature_request_summary(self):
        request_model = self.env["sign.oca.request"]
        for partner in self:
            requests = request_model.search(partner._signature_request_domain())
            partner.signature_request_count = len(requests)
            latest = requests.sorted(
                lambda item: (item.create_date, item.id), reverse=True
            )[:1]
            if not latest:
                partner.signature_current_state = "none"
            elif latest.state in {"sent", "viewed", "partial"}:
                partner.signature_current_state = "in_progress"
            elif latest.state in {"action_required", "declined", "expired"}:
                partner.signature_current_state = "attention"
            elif latest.state == "completed":
                partner.signature_current_state = "completed"
            else:
                partner.signature_current_state = "draft"

    def action_view_signature_requests(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "usl_sign.sign_request_action"
        )
        action["domain"] = self._signature_request_domain()
        action["context"] = {"default_record_ref": f"res.partner,{self.id}"}
        return action

    def action_request_signature(self):
        self.ensure_one()
        templates = self.env["sign.oca.template"].search_count(
            [("model", "=", "res.partner"), ("active", "=", True)], limit=1
        )
        if not templates:
            raise UserError(
                self.env._(
                    "No active Contact template is available. Ask a template manager to create one first."
                )
            )
        action = self.env["ir.actions.actions"]._for_xml_id(
            "sign_oca.sign_oca_template_generate_multi_act_window"
        )
        action["context"] = {"model": "res.partner", "active_ids": self.ids}
        return action
