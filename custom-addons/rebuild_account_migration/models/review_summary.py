from odoo import fields, models, tools
from odoo.exceptions import UserError


class RebuildAccountReviewSummary(models.Model):
    _name = "rebuild.account.review.summary"
    _description = "USL Accounting Reconstruction Review Summary"
    _auto = False
    _order = "source_company_id, company_id"

    company_id = fields.Many2one("res.company", readonly=True)
    source_company_id = fields.Integer(readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    latest_import_run_id = fields.Many2one("rebuild.account.import.run", readonly=True)
    latest_import_status = fields.Selection(
        related="latest_import_run_id.status",
        readonly=True,
    )
    source_snapshot_id = fields.Char(readonly=True)
    source_dump_sha256 = fields.Char(readonly=True)
    target_database = fields.Char(readonly=True)
    started_at = fields.Datetime(readonly=True)
    finished_at = fields.Datetime(readonly=True)
    posted_move_count = fields.Integer(readonly=True)
    move_line_count = fields.Integer(readonly=True)
    debit = fields.Monetary(currency_field="currency_id", readonly=True)
    credit = fields.Monetary(currency_field="currency_id", readonly=True)
    balance = fields.Monetary(currency_field="currency_id", readonly=True)
    source_report_count = fields.Integer(readonly=True)
    mandatory_report_count = fields.Integer(readonly=True)
    partial_report_equivalent_count = fields.Integer(readonly=True)
    level_3_report_count = fields.Integer(
        string="Level 3+ Reports",
        help="Active source reports with at least semantic-partial parity evidence.",
        readonly=True,
    )
    level_4_report_count = fields.Integer(
        string="Level 4+ Reports",
        help="Active source reports with evidence-partial or accepted parity evidence.",
        readonly=True,
    )
    open_discrepancy_count = fields.Integer(readonly=True)
    open_p0_count = fields.Integer(readonly=True)
    open_p1_count = fields.Integer(readonly=True)
    review_record_count = fields.Integer(readonly=True)
    review_decision_count = fields.Integer(readonly=True)
    pending_review_decision_count = fields.Integer(readonly=True)
    recorded_review_decision_count = fields.Integer(readonly=True)
    external_report_value_count = fields.Integer(readonly=True)
    pending_external_report_value_count = fields.Integer(readonly=True)
    document_regeneration_case_count = fields.Integer(readonly=True)
    document_regeneration_candidate_count = fields.Integer(readonly=True)
    document_regeneration_review_only_count = fields.Integer(readonly=True)
    document_regeneration_blocked_count = fields.Integer(readonly=True)
    readiness_status = fields.Selection(
        [
            ("blocked", "Blocked"),
            ("review_required", "Review Required"),
            ("technical_evidence_available", "Technical Evidence Available"),
        ],
        readonly=True,
    )

    def action_open_latest_import_run(self):
        self.ensure_one()
        if not self.latest_import_run_id:
            raise UserError("No accounting import run is linked to this review summary.")
        return {
            "type": "ir.actions.act_window",
            "name": "Latest Accounting Import Run",
            "res_model": "rebuild.account.import.run",
            "res_id": self.latest_import_run_id.id,
            "view_mode": "form",
            "context": {"create": False, "delete": False},
        }

    def action_open_open_discrepancies(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Open Accounting Discrepancies",
            "res_model": "rebuild.account.discrepancy",
            "view_mode": "list,form,pivot",
            "domain": [
                "|",
                ("company_id", "=", False),
                ("company_id", "=", self.company_id.id),
                ("status", "in", ["open", "investigating"]),
            ],
            "context": {"create": False, "delete": False},
        }

    def action_open_review_decisions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Accounting Review Decisions",
            "res_model": "rebuild.account.review.decision",
            "view_mode": "list,form,pivot",
            "domain": [
                "|",
                ("company_id", "=", False),
                ("company_id", "=", self.company_id.id),
                ("state", "!=", "superseded"),
            ],
            "context": {
                "default_company_id": self.company_id.id,
                "default_period_key": "USL benchmark 2024-01-10 to 2025-09-30",
                "delete": False,
            },
        }

    def action_open_external_report_values(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "External Report Values",
            "res_model": "rebuild.account.external.report.value",
            "view_mode": "list,form,pivot",
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("active", "=", True),
            ],
            "context": {
                "default_company_id": self.company_id.id,
                "default_period_key": "USL benchmark 2024-01-10 to 2025-09-30",
                "delete": False,
            },
        }

    def action_open_document_regeneration_cases(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Document Regeneration Cases",
            "res_model": "rebuild.account.document.regeneration.case",
            "view_mode": "list,form,pivot",
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("active", "=", True),
            ],
            "context": {
                "search_default_group_status": 1,
                "delete": False,
            },
        }

    def action_open_source_reports(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Source Accounting Report Catalogue",
            "res_model": "rebuild.account.source.report",
            "view_mode": "list,form,pivot",
            "domain": [("active", "=", True)],
            "context": {"create": False, "delete": False},
        }

    def action_open_imported_journal_items(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Imported Posted Journal Items",
            "res_model": "account.move.line",
            "view_mode": "list,form,pivot",
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("rebuild_source_model", "=", "account.move.line"),
                ("move_id.rebuild_source_model", "=", "account.move"),
                ("move_id.state", "=", "posted"),
            ],
            "context": {"create": False, "delete": False},
        }

    def action_open_report_export_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Imported Accounting Report Export",
            "res_model": "rebuild.account.report.export.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_company_id": self.company_id.id,
                "default_report_type": "trial_balance",
                "default_date_from": "2024-01-10",
                "default_date_to": "2025-09-30",
                "default_export_format": "xlsx",
                "default_target_move": "posted",
            },
        }

    def action_open_user_guide(self):
        return {
            "type": "ir.actions.act_url",
            "name": "USL Odoo User Guide",
            "url": "/usl/user-docs",
            "target": "self",
        }

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT company.id AS id,
                       company.id AS company_id,
                       company.rebuild_source_id AS source_company_id,
                       company.currency_id,
                       latest_run.id AS latest_import_run_id,
                       latest_run.source_snapshot_id,
                       latest_run.source_dump_sha256,
                       latest_run.target_database,
                       latest_run.started_at,
                       latest_run.finished_at,
                       COALESCE(ledger.posted_move_count, 0) AS posted_move_count,
                       COALESCE(ledger.move_line_count, 0) AS move_line_count,
                       COALESCE(ledger.debit, 0.00) AS debit,
                       COALESCE(ledger.credit, 0.00) AS credit,
                       COALESCE(ledger.balance, 0.00) AS balance,
                       COALESCE(reports.source_report_count, 0) AS source_report_count,
                       COALESCE(reports.mandatory_report_count, 0) AS mandatory_report_count,
                       COALESCE(reports.partial_report_equivalent_count, 0) AS partial_report_equivalent_count,
                       COALESCE(reports.level_3_report_count, 0) AS level_3_report_count,
                       COALESCE(reports.level_4_report_count, 0) AS level_4_report_count,
                       COALESCE(discrepancies.open_discrepancy_count, 0) AS open_discrepancy_count,
                       COALESCE(discrepancies.open_p0_count, 0) AS open_p0_count,
                       COALESCE(discrepancies.open_p1_count, 0) AS open_p1_count,
                       COALESCE(reviews.review_record_count, 0) AS review_record_count,
                       COALESCE(decisions.review_decision_count, 0) AS review_decision_count,
                       COALESCE(decisions.pending_review_decision_count, 0) AS pending_review_decision_count,
                       COALESCE(decisions.recorded_review_decision_count, 0) AS recorded_review_decision_count,
                       COALESCE(external_values.external_report_value_count, 0) AS external_report_value_count,
                       COALESCE(external_values.pending_external_report_value_count, 0) AS pending_external_report_value_count,
                       COALESCE(document_cases.document_regeneration_case_count, 0) AS document_regeneration_case_count,
                       COALESCE(document_cases.document_regeneration_candidate_count, 0) AS document_regeneration_candidate_count,
                       COALESCE(document_cases.document_regeneration_review_only_count, 0) AS document_regeneration_review_only_count,
                       COALESCE(document_cases.document_regeneration_blocked_count, 0) AS document_regeneration_blocked_count,
                       CASE
                           WHEN COALESCE(discrepancies.open_p0_count, 0) > 0
                             OR abs(COALESCE(ledger.balance, 0.00)) > 0.004
                           THEN 'blocked'
                           WHEN COALESCE(discrepancies.open_p1_count, 0) > 0
                             OR COALESCE(reviews.review_record_count, 0) > 0
                             OR COALESCE(decisions.pending_review_decision_count, 0) > 0
                             OR COALESCE(external_values.pending_external_report_value_count, 0) > 0
                             OR COALESCE(document_cases.document_regeneration_case_count, 0) > 0
                           THEN 'review_required'
                           ELSE 'technical_evidence_available'
                       END AS readiness_status
                  FROM res_company company
                  LEFT JOIN LATERAL (
                      SELECT run.*
                        FROM rebuild_account_import_run run
                       ORDER BY run.started_at DESC NULLS LAST, run.id DESC
                       LIMIT 1
                  ) latest_run ON TRUE
                  LEFT JOIN LATERAL (
                      SELECT count(DISTINCT move.id)::integer AS posted_move_count,
                             count(line.id)::integer AS move_line_count,
                             round(COALESCE(sum(line.debit), 0)::numeric, 2) AS debit,
                             round(COALESCE(sum(line.credit), 0)::numeric, 2) AS credit,
                             round(COALESCE(sum(line.balance), 0)::numeric, 2) AS balance
                        FROM account_move_line line
                        JOIN account_move move ON move.id = line.move_id
                       WHERE line.company_id = company.id
                         AND line.rebuild_source_model = 'account.move.line'
                         AND move.rebuild_source_model = 'account.move'
                         AND move.state = 'posted'
                  ) ledger ON TRUE
                  LEFT JOIN LATERAL (
                      SELECT count(*)::integer AS source_report_count,
                             count(*) FILTER (WHERE report.decision = 'MANDATORY_PARITY')::integer AS mandatory_report_count,
                             count(*) FILTER (WHERE report.target_status = 'partial_target_equivalent')::integer AS partial_report_equivalent_count,
                             count(*) FILTER (WHERE report.parity_level IN ('level_3_semantic_partial', 'level_4_evidence_partial', 'level_4_accepted'))::integer AS level_3_report_count,
                             count(*) FILTER (WHERE report.parity_level IN ('level_4_evidence_partial', 'level_4_accepted'))::integer AS level_4_report_count
                        FROM rebuild_account_source_report report
                       WHERE report.active IS TRUE
                  ) reports ON TRUE
                  LEFT JOIN LATERAL (
                      SELECT count(*) FILTER (WHERE discrepancy.status IN ('open', 'investigating'))::integer AS open_discrepancy_count,
                             count(*) FILTER (WHERE discrepancy.status IN ('open', 'investigating') AND discrepancy.severity = 'P0')::integer AS open_p0_count,
                             count(*) FILTER (WHERE discrepancy.status IN ('open', 'investigating') AND discrepancy.severity = 'P1')::integer AS open_p1_count
                        FROM rebuild_account_discrepancy discrepancy
                       WHERE discrepancy.company_id IS NULL
                          OR discrepancy.company_id = company.id
                  ) discrepancies ON TRUE
                  LEFT JOIN LATERAL (
                      SELECT (
                          (SELECT count(*) FROM rebuild_account_move_review review WHERE review.company_id = company.id)
                        + (SELECT count(*) FROM rebuild_account_document_regeneration_case review WHERE review.company_id = company.id AND review.active IS TRUE)
                        + (SELECT count(*) FROM rebuild_account_move_line_review review WHERE review.company_id = company.id)
                        + (SELECT count(*) FROM rebuild_account_payment_review review WHERE review.company_id = company.id)
                        + (SELECT count(*) FROM rebuild_account_reconciliation_review review WHERE review.company_id = company.id)
                        + (SELECT count(*) FROM rebuild_account_deferred_schedule_line review WHERE review.company_id = company.id)
                      )::integer AS review_record_count
                  ) reviews ON TRUE
                  LEFT JOIN LATERAL (
                      SELECT count(*) FILTER (WHERE decision.state != 'superseded')::integer AS review_decision_count,
                             count(*) FILTER (WHERE decision.state = 'draft')::integer AS pending_review_decision_count,
                             count(*) FILTER (WHERE decision.state = 'recorded')::integer AS recorded_review_decision_count
                        FROM rebuild_account_review_decision decision
                       WHERE decision.company_id IS NULL
                          OR decision.company_id = company.id
                  ) decisions ON TRUE
                  LEFT JOIN LATERAL (
                      SELECT count(*) FILTER (WHERE value.active IS TRUE)::integer AS external_report_value_count,
                             count(*) FILTER (WHERE value.active IS TRUE AND value.review_status = 'pending_review')::integer AS pending_external_report_value_count
                        FROM rebuild_account_external_report_value value
                       WHERE value.company_id = company.id
                  ) external_values ON TRUE
                  LEFT JOIN LATERAL (
                      SELECT count(*) FILTER (WHERE case_record.active IS TRUE)::integer AS document_regeneration_case_count,
                             count(*) FILTER (WHERE case_record.active IS TRUE AND case_record.case_status = 'candidate_ready')::integer AS document_regeneration_candidate_count,
                             count(*) FILTER (WHERE case_record.active IS TRUE AND case_record.generation_status = 'not_applicable')::integer AS document_regeneration_review_only_count,
                             count(*) FILTER (WHERE case_record.active IS TRUE AND case_record.generation_status = 'blocked')::integer AS document_regeneration_blocked_count
                        FROM rebuild_account_document_regeneration_case case_record
                       WHERE case_record.company_id = company.id
                  ) document_cases ON TRUE
                 WHERE company.rebuild_source_id IS NOT NULL
                    OR COALESCE(ledger.move_line_count, 0) > 0
            )
            """,
        )
