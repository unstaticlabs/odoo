from odoo import fields, models


class RebuildAccountDiscrepancy(models.Model):
    _name = "rebuild.account.discrepancy"
    _description = "USL Accounting Parity Discrepancy"
    _order = "severity, id"

    name = fields.Char(required=True)
    import_run_id = fields.Many2one(
        "rebuild.account.import.run",
        index=True,
        ondelete="cascade",
    )
    severity = fields.Selection(
        [
            ("P0", "P0"),
            ("P1", "P1"),
            ("P2", "P2"),
            ("P3", "P3"),
        ],
        required=True,
        default="P2",
        index=True,
    )
    classification = fields.Selection(
        [
            ("presentation_difference", "Presentation Difference"),
            ("accepted_improvement", "Accepted Improvement"),
            ("source_anomaly", "Source Anomaly"),
            ("import_defect", "Import Defect"),
            ("report_definition_defect", "Report Definition Defect"),
            ("missing_capability", "Missing Capability"),
            ("external_value_difference", "External Value Difference"),
            ("period_or_scope_difference", "Period or Scope Difference"),
            ("attachment_difference", "Attachment Difference"),
            ("legal_or_accounting_uncertainty", "Legal or Accounting Uncertainty"),
            ("deliberate_non_parity", "Deliberate Non-Parity"),
            ("unclassified", "Unclassified"),
        ],
        required=True,
        default="unclassified",
        index=True,
    )
    status = fields.Selection(
        [
            ("open", "Open"),
            ("investigating", "Investigating"),
            ("resolved", "Resolved"),
            ("accepted", "Accepted"),
        ],
        required=True,
        default="open",
        index=True,
    )
    company_id = fields.Many2one("res.company", index=True)
    period_key = fields.Char(index=True)
    source_model = fields.Char(index=True)
    source_id = fields.Integer(index=True)
    target_model = fields.Char(index=True)
    target_id = fields.Integer(index=True)
    source_value = fields.Text()
    target_value = fields.Text()
    difference = fields.Text()
    accounting_impact = fields.Text()
    legal_or_tax_impact = fields.Text()
    evidence = fields.Text()
    likely_cause = fields.Text()
    recommendation = fields.Text()
    owner = fields.Char()
    decision = fields.Text()
    approver = fields.Char()

    def action_record_review_decision(self):
        self.ensure_one()
        gate = (
            "tax_external_value"
            if self.classification == "external_value_difference"
            else "discrepancy_acceptance"
        )
        return {
            "type": "ir.actions.act_window",
            "name": "Record Accounting Review Decision",
            "res_model": "rebuild.account.assurance.decision",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_name": f"Discrepancy review - {self.name}",
                "default_gate": gate,
                "default_conclusion": "pending",
                "default_required_authority": "accountant",
                "default_company_id": self.company_id.id,
                "default_period_key": self.period_key,
                "default_discrepancy_id": self.id,
                "default_import_run_id": self.import_run_id.id,
                "default_source_value": self.source_value,
                "default_target_value": self.target_value,
                "default_difference": self.difference,
                "default_evidence_summary": self.evidence,
                "default_remaining_risk": self.legal_or_tax_impact or self.accounting_impact,
                "default_next_action": self.recommendation,
            },
        }
