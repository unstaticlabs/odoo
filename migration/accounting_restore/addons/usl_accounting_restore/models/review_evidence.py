from odoo import fields, models
from odoo.exceptions import UserError


class AssuranceDecision(models.Model):
    _inherit = "rebuild.account.assurance.decision"

    gate = fields.Selection(
        selection_add=[
            ("report_parity", "Report Parity"),
            ("discrepancy_acceptance", "Discrepancy Acceptance"),
            ("scope_exclusion", "Scope Exclusion"),
            ("milestone_closure", "Milestone Closure"),
        ],
        ondelete={
            "report_parity": "set default",
            "discrepancy_acceptance": "set default",
            "scope_exclusion": "set default",
            "milestone_closure": "set default",
        },
    )
    source_report_id = fields.Many2one(
        "rebuild.account.source.report",
        index=True,
        ondelete="set null",
    )
    discrepancy_id = fields.Many2one(
        "rebuild.account.discrepancy",
        index=True,
        ondelete="set null",
    )
    import_run_id = fields.Many2one(
        "rebuild.account.import.run",
        index=True,
        ondelete="set null",
    )
    source_value = fields.Text()
    target_value = fields.Text()
    difference = fields.Text()

    def _default_name_from_values(self, vals):
        if vals.get("source_report_id"):
            report = self.env["rebuild.account.source.report"].browse(
                vals["source_report_id"],
            )
            return f"Report review - {report.display_name}"
        if vals.get("discrepancy_id"):
            discrepancy = self.env["rebuild.account.discrepancy"].browse(
                vals["discrepancy_id"],
            )
            return f"Discrepancy review - {discrepancy.display_name}"
        return super()._default_name_from_values(vals)

    def _apply_recorded_decision(self):
        result = super()._apply_recorded_decision()
        for decision in self:
            if decision.source_report_id:
                decision._apply_source_report_decision()
            if decision.discrepancy_id:
                decision._apply_discrepancy_decision()
        return result

    def _apply_source_report_decision(self):
        accepted = {
            "accepted",
            "accepted_with_difference",
            "not_applicable",
        }
        values = {
            "latest_evidence_status": (
                f"recorded_review_decision:{self.conclusion}"
            ),
            "parity_gap": (
                self.remaining_risk or self.decision_summary
                if self.conclusion not in accepted
                else self.remaining_risk
            ),
        }
        if self.conclusion in accepted:
            values["parity_level"] = "level_4_accepted"
        self.source_report_id.sudo().write(values)

    def _apply_discrepancy_decision(self):
        status = {
            "accepted": "accepted",
            "accepted_with_difference": "accepted",
            "not_applicable": "accepted",
            "requires_change": "investigating",
            "rejected": "open",
        }.get(self.conclusion)
        if status:
            self.discrepancy_id.sudo().write({
                "status": status,
                "decision": self.decision_summary,
                "approver": (
                    self.reviewer_name
                    or self.reviewer_user_id.name
                    or self.env.user.name
                ),
            })

    def action_open_source_report(self):
        self.ensure_one()
        if not self.source_report_id:
            raise UserError("This review is not linked to a source report.")
        return {
            "type": "ir.actions.act_window",
            "name": "Source Accounting Report",
            "res_model": "rebuild.account.source.report",
            "res_id": self.source_report_id.id,
            "view_mode": "form",
        }

    def action_open_discrepancy(self):
        self.ensure_one()
        if not self.discrepancy_id:
            raise UserError("This review is not linked to a discrepancy.")
        return {
            "type": "ir.actions.act_window",
            "name": "Accounting Discrepancy",
            "res_model": "rebuild.account.discrepancy",
            "res_id": self.discrepancy_id.id,
            "view_mode": "form",
        }


class ExternalReportValue(models.Model):
    _inherit = "rebuild.account.external.report.value"

    import_run_id = fields.Many2one(
        "rebuild.account.import.run",
        index=True,
        ondelete="set null",
    )
    discrepancy_id = fields.Many2one(
        "rebuild.account.discrepancy",
        index=True,
        ondelete="set null",
    )

    def action_record_review_decision(self):
        self.ensure_one()
        action = super().action_record_review_decision()
        action["context"] = {
            **action.get("context", {}),
            "default_import_run_id": self.import_run_id.id,
            "default_discrepancy_id": self.discrepancy_id.id,
            "default_source_value": f"{self.amount:.2f}",
        }
        return action
