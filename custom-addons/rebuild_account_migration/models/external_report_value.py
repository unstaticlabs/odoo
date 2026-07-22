from odoo import fields, models
from odoo.exceptions import UserError


class RebuildAccountExternalReportValue(models.Model):
    _name = "rebuild.account.external.report.value"
    _description = "USL External Report Value"
    _order = "company_id, period_key, form_code, field_code, value_kind, id"

    _unique_external_report_value_source = models.Constraint(
        "UNIQUE (company_id, period_key, form_code, field_code, value_kind, source_key)",
        "An external report value with this source already exists for the same company, period and field.",
    )

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, index=True)
    source_company_id = fields.Integer(index=True)
    currency_id = fields.Many2one("res.currency", required=True)
    period_key = fields.Char(required=True, index=True)
    form_code = fields.Char(required=True, index=True)
    form_name = fields.Char(index=True)
    field_code = fields.Char(required=True, index=True)
    field_label = fields.Char()
    value_kind = fields.Selection(
        [
            ("benchmark_acceptance_anchor", "Benchmark Acceptance Anchor"),
            ("source_external_value", "Source External Value"),
            ("accountant_supplied", "Accountant Supplied"),
            ("manual_adjustment", "Manual Adjustment"),
            ("carryover", "Carryover"),
        ],
        required=True,
        default="benchmark_acceptance_anchor",
        index=True,
    )
    amount = fields.Monetary(currency_field="currency_id")
    value_text = fields.Char()
    source_key = fields.Char(required=True, index=True)
    source_document = fields.Char()
    source_reference = fields.Char()
    source_url = fields.Char()
    review_status = fields.Selection(
        [
            ("pending_review", "Pending Review"),
            ("accepted", "Accepted"),
            ("accepted_with_difference", "Accepted With Difference"),
            ("rejected", "Rejected"),
            ("superseded", "Superseded"),
        ],
        required=True,
        default="pending_review",
        index=True,
    )
    import_run_id = fields.Many2one("rebuild.account.import.run", index=True, ondelete="set null")
    discrepancy_id = fields.Many2one("rebuild.account.discrepancy", index=True, ondelete="set null")
    evidence = fields.Text()
    decision = fields.Text()
    reviewer_name = fields.Char()
    reviewed_at = fields.Datetime()

    def action_open_tax_package_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "French Tax Package Lines",
            "res_model": "rebuild.account.french.tax.package.line",
            "view_mode": "list,pivot",
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("period_key", "=", self.period_key),
                ("field_code", "=", self.field_code),
            ],
            "context": {"create": False, "delete": False},
        }

    def action_open_discrepancy(self):
        self.ensure_one()
        if not self.discrepancy_id:
            raise UserError("This external value is not linked to a discrepancy.")
        return {
            "type": "ir.actions.act_window",
            "name": "Accounting Discrepancy",
            "res_model": "rebuild.account.discrepancy",
            "res_id": self.discrepancy_id.id,
            "view_mode": "form",
            "context": {"create": False, "delete": False},
        }

    def action_record_review_decision(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Record External Value Review",
            "res_model": "rebuild.account.review.decision",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_name": f"External value review - {self.name}",
                "default_gate": "tax_external_value",
                "default_conclusion": "pending",
                "default_required_authority": "accountant",
                "default_company_id": self.company_id.id,
                "default_period_key": self.period_key,
                "default_external_value_id": self.id,
                "default_discrepancy_id": self.discrepancy_id.id,
                "default_import_run_id": self.import_run_id.id,
                "default_evidence_key": self.source_key,
                "default_source_value": f"{self.amount:.2f}",
                "default_evidence_summary": self.evidence,
                "default_remaining_risk": (
                    "External declaration value is not accepted until accountant review records its treatment."
                ),
                "default_next_action": (
                    "Compare this external value with the ledger-derived tax-package line and record whether "
                    "it is accepted, rejected or superseded by a corrected declaration value."
                ),
            },
        }
