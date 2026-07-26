from odoo import fields, models
from odoo.exceptions import UserError


class RebuildAccountSourceReport(models.Model):
    _name = "rebuild.account.source.report"
    _description = "USL Source Accounting Report Catalogue"
    _inherit = ["rebuild.source.trace.mixin"]
    _order = "source_report_id"

    name = fields.Char(required=True, index=True)
    source_report_id = fields.Integer(index=True, copy=False)
    source_name = fields.Char(index=True, copy=False)
    localized_name = fields.Char(index=True, copy=False)
    active = fields.Boolean(default=True, index=True)
    sequence = fields.Integer(copy=False)
    country_code = fields.Char(index=True, copy=False)
    source_country_id = fields.Integer(copy=False)
    chart_template = fields.Char(copy=False)
    source_root_report_id = fields.Integer(index=True, copy=False)
    root_report_name = fields.Char(copy=False)
    source_custom_handler_model = fields.Char(index=True, copy=False)

    decision = fields.Selection(
        [
            ("MANDATORY_PARITY", "Mandatory Parity"),
            ("OPERATIONAL_PARITY", "Operational Parity"),
            ("ACCOUNTANT_REQUESTED", "Accountant Requested"),
            ("REPLACED_BY_EQUIVALENT", "Replaced by Equivalent"),
            ("DEFERRED", "Deferred"),
            ("REMOVED_AS_UNUSED", "Removed as Unused"),
        ],
        required=True,
        index=True,
    )
    decision_basis = fields.Char(copy=False)
    target_status = fields.Selection(
        [
            ("partial_target_equivalent", "Partial Target Equivalent"),
            ("missing_target_equivalent", "Missing Target Equivalent"),
            ("decision_pending", "Decision Pending"),
        ],
        required=True,
        default="decision_pending",
        index=True,
    )
    target_action_xmlid = fields.Char(copy=False)
    target_evidence_key = fields.Char(copy=False, index=True)
    target_strategy = fields.Text()
    acceptance_evidence_required = fields.Text()
    parity_level = fields.Selection(
        [
            ("level_0_unmapped", "Level 0 - Unmapped"),
            ("level_1_available", "Level 1 - Available"),
            ("level_2_ledger_controls", "Level 2 - Ledger Controls"),
            ("level_3_semantic_partial", "Level 3 - Semantic Partial"),
            ("level_4_evidence_partial", "Level 4 - Evidence Partial"),
            ("level_4_accepted", "Level 4 - Accepted"),
        ],
        required=True,
        default="level_0_unmapped",
        index=True,
        copy=False,
    )
    parity_gap = fields.Text(copy=False)
    latest_evidence_status = fields.Text(copy=False)
    latest_evidence_json = fields.Json(copy=False)

    line_count = fields.Integer(copy=False)
    column_count = fields.Integer(copy=False)
    expression_count = fields.Integer(copy=False)
    external_value_count = fields.Integer(copy=False)
    imported_line_count = fields.Integer(copy=False)
    imported_column_count = fields.Integer(copy=False)
    imported_expression_count = fields.Integer(copy=False)
    line_code_sample = fields.Text(copy=False)
    expression_engine_summary = fields.Json(copy=False)

    availability_condition = fields.Char(copy=False)
    integer_rounding = fields.Char(copy=False)
    default_opening_date_filter = fields.Char(copy=False)
    currency_translation = fields.Char(copy=False)
    filter_multi_company = fields.Char(copy=False)
    filter_hide_0_lines = fields.Char(copy=False)
    filter_hierarchy = fields.Char(copy=False)
    filter_account_type = fields.Char(copy=False)
    filter_date_range = fields.Boolean(copy=False)
    filter_show_draft = fields.Boolean(copy=False)
    filter_unreconciled = fields.Boolean(copy=False)
    filter_unfold_all = fields.Boolean(copy=False)
    filter_period_comparison = fields.Boolean(copy=False)
    filter_growth_comparison = fields.Boolean(copy=False)
    filter_journals = fields.Boolean(copy=False)
    filter_partner = fields.Boolean(copy=False)
    filter_aml_ir_filters = fields.Boolean(copy=False)
    filter_budgets = fields.Boolean(copy=False)
    filter_analytic_groupby = fields.Boolean(copy=False)
    filter_cash_basis = fields.Boolean(copy=False)
    use_sections = fields.Boolean(copy=False)
    only_tax_exigible = fields.Boolean(copy=False)
    use_fiscal_periods = fields.Boolean(copy=False)
    allow_foreign_vat = fields.Boolean(copy=False)

    note = fields.Text()
    source_line_ids = fields.One2many("rebuild.account.source.report.line", "report_id")
    source_column_ids = fields.One2many("rebuild.account.source.report.column", "report_id")
    source_expression_ids = fields.One2many("rebuild.account.source.report.expression", "report_id")

    def action_open_target_equivalent(self):
        self.ensure_one()
        if not self.target_action_xmlid:
            raise UserError("No target report equivalent has been assigned for this source report.")
        return self.env["ir.actions.actions"]._for_xml_id(self.target_action_xmlid)

    def action_record_review_decision(self):
        self.ensure_one()
        gate = "scope_exclusion" if self.decision == "REMOVED_AS_UNUSED" else "report_parity"
        conclusion = "not_applicable" if self.decision == "REMOVED_AS_UNUSED" else "pending"
        return {
            "type": "ir.actions.act_window",
            "name": "Record Report Review Decision",
            "res_model": "rebuild.account.assurance.decision",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_name": f"Report review - {self.name}",
                "default_gate": gate,
                "default_conclusion": conclusion,
                "default_required_authority": "accountant",
                "default_period_key": "USL benchmark 2024-01-10 to 2025-09-30",
                "default_source_report_id": self.id,
                "default_import_run_id": self.rebuild_import_run_id.id,
                "default_evidence_key": self.target_evidence_key,
                "default_decision_summary": self.decision_basis or self.target_strategy or "Pending report parity review.",
                "default_evidence_summary": self.latest_evidence_status,
                "default_remaining_risk": self.parity_gap or self.acceptance_evidence_required,
                "default_next_action": self.acceptance_evidence_required,
            },
        }

    def action_open_source_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Source Report Lines",
            "res_model": "rebuild.account.source.report.line",
            "view_mode": "list,form,pivot",
            "domain": [("report_id", "=", self.id)],
            "context": {"create": False, "delete": False},
        }

    def action_open_source_columns(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Source Report Columns",
            "res_model": "rebuild.account.source.report.column",
            "view_mode": "list,form,pivot",
            "domain": [("report_id", "=", self.id)],
            "context": {"create": False, "delete": False},
        }

    def action_open_source_expressions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Source Report Expressions",
            "res_model": "rebuild.account.source.report.expression",
            "view_mode": "list,form,pivot",
            "domain": [("report_id", "=", self.id)],
            "context": {"create": False, "delete": False},
        }
