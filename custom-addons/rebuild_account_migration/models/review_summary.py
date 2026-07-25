from odoo import api, fields, models, tools
from odoo.exceptions import AccessError, UserError
from odoo.tools import date_utils
from odoo.tools.safe_eval import safe_eval


class RebuildAccountReviewSummary(models.Model):
    _name = "rebuild.account.review.summary"
    _description = "USL Accounting Reconstruction Review Summary"
    _auto = False
    _order = "source_company_id, company_id"
    _rec_name = "name"

    name = fields.Char(readonly=True)
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
    journal_count = fields.Integer(readonly=True)
    cash_journal_count = fields.Integer(readonly=True)
    bank_balance = fields.Monetary(
        string="Bank and Cash Balance",
        currency_field="currency_id",
        readonly=True,
    )
    bank_transaction_count = fields.Integer(readonly=True)
    unmatched_bank_transaction_count = fields.Integer(readonly=True)
    bank_review_count = fields.Integer(
        string="Pending Review",
        readonly=True,
    )
    draft_customer_document_count = fields.Integer(readonly=True)
    draft_vendor_document_count = fields.Integer(readonly=True)
    draft_expense_count = fields.Integer(
        string="Expenses to Process",
        readonly=True,
    )
    incomplete_document_count = fields.Integer(readonly=True)
    missing_vendor_attachment_count = fields.Integer(
        string="Vendor Documents Missing Evidence",
        readonly=True,
    )
    missing_expense_attachment_count = fields.Integer(
        string="Expenses Missing Receipts",
        readonly=True,
    )
    stale_draft_document_count = fields.Integer(
        string="Stale Draft Documents",
        readonly=True,
    )
    stale_draft_expense_count = fields.Integer(
        string="Stale Expense Work",
        readonly=True,
    )
    open_receivable_count = fields.Integer(readonly=True)
    open_receivable_amount = fields.Monetary(
        currency_field="currency_id",
        readonly=True,
    )
    open_payable_count = fields.Integer(readonly=True)
    open_payable_amount = fields.Monetary(
        currency_field="currency_id",
        readonly=True,
    )
    latest_closing_period_id = fields.Many2one(
        "rebuild.account.closing.period",
        readonly=True,
    )
    latest_closing_date_to = fields.Date(readonly=True)
    latest_closing_state = fields.Selection(
        [
            ("open", "Open"),
            ("preparing", "Preparing"),
            ("blocked", "Blocked"),
            ("internal_review", "Internal Review"),
            ("accountant_review", "Accountant Review"),
            ("ready", "Ready to Close"),
            ("closed", "Closed and Locked"),
            ("archived", "Archived"),
        ],
        readonly=True,
    )
    latest_closing_readiness = fields.Selection(
        [
            ("not_run", "Not Run"),
            ("ready", "Ready"),
            ("warning", "Warnings"),
            ("blocked", "Blocked"),
        ],
        readonly=True,
    )
    latest_closing_blocking_count = fields.Integer(readonly=True)
    latest_closing_warning_count = fields.Integer(readonly=True)
    unusual_balance_count = fields.Integer(
        string="Unusual Account Balances",
        readonly=True,
    )
    unusual_balance_amount = fields.Monetary(
        string="Unusual Balance Review Amount",
        currency_field="currency_id",
        readonly=True,
    )
    next_declaration_id = fields.Many2one(
        "rebuild.account.declaration",
        readonly=True,
    )
    next_declaration_deadline = fields.Date(readonly=True)
    next_declaration_status = fields.Selection(
        [
            ("to_prepare", "To Prepare"),
            ("data_missing", "Data Missing"),
            ("internal_review", "Ready for Internal Review"),
            ("accountant_review", "Ready for Accountant Review"),
            ("accountant_reviewed", "Accountant Reviewed"),
            ("ready_to_file", "Ready to File Externally"),
            ("filed", "Filed Externally"),
            ("paid", "Paid / Refunded"),
            ("archived", "Archived"),
            ("blocked", "Blocked"),
            ("not_applicable", "Not Applicable"),
        ],
        readonly=True,
    )
    overdue_declaration_count = fields.Integer(readonly=True)
    upcoming_declaration_count = fields.Integer(
        string="Declarations Due in 45 Days",
        readonly=True,
    )
    valentin_action_count = fields.Integer(
        string="Assigned to Accounting Manager",
        readonly=True,
    )
    accountant_action_count = fields.Integer(
        string="Assigned to Accountant Reviewer",
        readonly=True,
    )
    hygiene_attention_count = fields.Integer(
        string="Items Requiring Attention",
        readonly=True,
    )
    hygiene_issue_count = fields.Integer(
        string="Open Actionable Issues",
        compute="_compute_hygiene_issue_count",
    )
    hygiene_status = fields.Selection(
        [
            ("ready", "Ready"),
            ("attention", "Attention Required"),
            ("blocked", "Blocked"),
        ],
        readonly=True,
    )
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

    def _compute_hygiene_issue_count(self):
        Issue = self.env["rebuild.account.hygiene.issue"]
        for summary in self:
            summary.hygiene_issue_count = Issue.search_count([
                ("company_id", "=", summary.company_id.id),
                ("status", "=", "open"),
            ])

    @api.model
    def action_open_accounting_home(self):
        if not self.has_access("read"):
            return self.env["ir.actions.actions"]._for_xml_id(
                "account.open_account_journal_dashboard_kanban",
            )
        home = self.search(
            [("company_id", "=", self.env.company.id)],
            limit=1,
        )
        if not home:
            return self.env["ir.actions.actions"]._for_xml_id(
                "account.open_account_journal_dashboard_kanban",
            )
        return {
            "type": "ir.actions.act_window",
            "name": "Accounting Home",
            "res_model": self._name,
            "res_id": home.id,
            "view_mode": "form",
            "view_id": self.env.ref(
                "rebuild_account_migration.view_rebuild_accounting_home_form",
            ).id,
            "views": [
                (
                    self.env.ref(
                        "rebuild_account_migration.view_rebuild_accounting_home_form",
                    ).id,
                    "form",
                ),
            ],
            "target": "current",
            "context": {"create": False, "delete": False},
        }

    def _standard_company_action(self, xmlid, domain=None):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(xmlid)
        if domain is not None:
            action["domain"] = [
                ("company_id", "=", self.company_id.id),
                *domain,
            ]
        return action

    def action_open_journal_dashboard(self):
        self.ensure_one()
        return self.env["ir.actions.actions"]._for_xml_id(
            "account.open_account_journal_dashboard_kanban",
        )

    def action_open_bank_transactions(self):
        return self._standard_company_action(
            "account_statement_base.account_bank_statement_line_action",
            [],
        )

    def action_open_bank_review(self):
        action = self._standard_company_action(
            "account_statement_base.account_bank_statement_line_action",
            [("checked", "=", False)],
        )
        action["name"] = "Pending Review"
        context = action.get("context") or {}
        if isinstance(context, str):
            context = safe_eval(context)
        action["context"] = {
            **context,
            "search_default_to_review": 1,
            "create": False,
        }
        return action

    def action_open_bank_matching(self):
        return self._standard_company_action(
            "rebuild_account_migration.action_rebuild_account_reconcile_bank_transactions",
            [("is_reconciled", "=", False)],
        )

    def action_open_customer_documents(self):
        action = self._standard_company_action(
            "account.action_move_out_invoice_type",
            [
                ("move_type", "in", ["out_invoice", "out_refund", "out_receipt"]),
                ("state", "=", "draft"),
            ],
        )
        context = action.get("context") or {}
        if isinstance(context, str):
            context = safe_eval(context)
        action["context"] = {
            **context,
            "search_default_draft": 1,
        }
        return action

    def action_open_vendor_documents(self):
        action = self._standard_company_action(
            "account.action_move_in_invoice_type",
            [
                ("move_type", "in", ["in_invoice", "in_refund", "in_receipt"]),
                ("state", "=", "draft"),
            ],
        )
        context = action.get("context") or {}
        if isinstance(context, str):
            context = safe_eval(context)
        action["context"] = {
            **context,
            "search_default_draft": 1,
        }
        return action

    def action_open_expenses(self):
        action = self._standard_company_action(
            "hr_expense.hr_expense_actions_all",
            [],
        )
        action.update({
            "view_mode": "list,form,graph,pivot",
            "views": [(False, "list"), (False, "form"), (False, "graph"), (False, "pivot")],
        })
        return action

    def action_open_missing_vendor_attachments(self):
        return self._standard_company_action(
            "account.action_move_in_invoice_type",
            [
                (
                    "move_type",
                    "in",
                    ["in_invoice", "in_refund", "in_receipt"],
                ),
                ("state", "!=", "cancel"),
                ("message_main_attachment_id", "=", False),
                ("rebuild_source_id", "=", False),
            ],
        )

    def action_open_missing_expense_attachments(self):
        return self._standard_company_action(
            "hr_expense.hr_expense_actions_all",
            [
                ("state", "!=", "refused"),
                ("message_main_attachment_id", "=", False),
                ("rebuild_source_id", "=", False),
            ],
        )

    def action_open_stale_draft_documents(self):
        cutoff = date_utils.subtract(fields.Date.context_today(self), days=30)
        return self._standard_company_action(
            "account.action_move_journal_line",
            [
                ("state", "=", "draft"),
                (
                    "move_type",
                    "in",
                    [
                        "out_invoice",
                        "out_refund",
                        "out_receipt",
                        "in_invoice",
                        "in_refund",
                        "in_receipt",
                    ],
                ),
                ("date", "<", cutoff),
            ],
        )

    def action_open_stale_expenses(self):
        cutoff = date_utils.subtract(fields.Date.context_today(self), days=30)
        return self._standard_company_action(
            "hr_expense.hr_expense_actions_all",
            [
                ("state", "in", ["draft", "submitted", "approved"]),
                ("date", "<", cutoff),
            ],
        )

    def action_open_latest_closing_controls(self):
        self.ensure_one()
        if not self.latest_closing_period_id:
            message = "No closing controls are available for this company."
            raise UserError(message)
        return {
            "type": "ir.actions.act_window",
            "name": "Current Accounting Controls",
            "res_model": "rebuild.account.closing.control",
            "view_mode": "list,form",
            "views": [(False, "list"), (False, "form")],
            "domain": [
                (
                    "closing_period_id",
                    "=",
                    self.latest_closing_period_id.id,
                ),
            ],
            "context": {
                "create": False,
                "delete": False,
                "search_default_group_category": 1,
            },
        }

    def action_open_unusual_balances(self):
        self.ensure_one()
        control = self.latest_closing_period_id.control_line_ids.filtered(
            lambda line: line.code == "unusual_balances",
        )[:1]
        if not control:
            message = (
                "No unusual-balance control is available. Ask an Accounting "
                "Manager to refresh the current controls."
            )
            raise UserError(message)
        return control.action_open_records()

    def action_refresh_hygiene(self):
        self.ensure_one()
        if not self.env.user.has_group("account.group_account_manager"):
            message = "Only an Accounting Manager can refresh accounting controls."
            raise AccessError(message)
        if self.latest_closing_period_id:
            self.latest_closing_period_id.action_refresh_controls()
        else:
            self.env["rebuild.account.hygiene.issue"].sync_for_company(
                self.company_id,
            )
        return {
            "type": "ir.actions.client",
            "tag": "reload",
        }

    def action_open_hygiene_issues(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Accounting Hygiene",
            "res_model": "rebuild.account.hygiene.issue",
            "view_mode": "list,form",
            "views": [(False, "list"), (False, "form")],
            "domain": [("company_id", "=", self.company_id.id)],
            "context": {
                "create": False,
                "delete": False,
                "search_default_open": 1,
            },
        }

    def action_open_open_receivables(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Open Receivables",
            "res_model": "account.move.line",
            "view_mode": "list,pivot,graph",
            "views": [(False, "list"), (False, "pivot"), (False, "graph")],
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("parent_state", "=", "posted"),
                ("account_id.account_type", "=", "asset_receivable"),
                ("reconciled", "=", False),
                ("amount_residual", "!=", 0),
            ],
            "context": {
                "create": False,
                "delete": False,
                "search_default_group_by_partner": 1,
            },
        }

    def action_open_open_payables(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Open Payables",
            "res_model": "account.move.line",
            "view_mode": "list,pivot,graph",
            "views": [(False, "list"), (False, "pivot"), (False, "graph")],
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("parent_state", "=", "posted"),
                ("account_id.account_type", "=", "liability_payable"),
                ("reconciled", "=", False),
                ("amount_residual", "!=", 0),
            ],
            "context": {
                "create": False,
                "delete": False,
                "search_default_group_by_partner": 1,
            },
        }

    def action_open_latest_closing(self):
        self.ensure_one()
        if not self.latest_closing_period_id:
            raise UserError(
                "No closing workspace is available for this company.",
            )
        return {
            "type": "ir.actions.act_window",
            "name": "Current Closing Workspace",
            "res_model": "rebuild.account.closing.period",
            "res_id": self.latest_closing_period_id.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
            "context": {"create": False, "delete": False},
        }

    def action_open_next_declaration(self):
        self.ensure_one()
        if not self.next_declaration_id:
            raise UserError(
                "No pending declaration is available for this company.",
            )
        return {
            "type": "ir.actions.act_window",
            "name": "Next Declaration",
            "res_model": "rebuild.account.declaration",
            "res_id": self.next_declaration_id.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
            "context": {"create": False, "delete": False},
        }

    def action_open_closing_workspaces(self):
        return self._standard_company_action(
            "rebuild_account_migration.action_rebuild_account_closing_period",
            [],
        )

    def action_open_declarations(self):
        return self._standard_company_action(
            "rebuild_account_migration.action_rebuild_account_declaration",
            [],
        )

    def action_open_valentin_actions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Accounting Manager Decisions",
            "res_model": "rebuild.account.review.decision",
            "view_mode": "list,form,pivot",
            "views": [(False, "list"), (False, "form"), (False, "pivot")],
            "domain": [
                "|",
                ("company_id", "=", False),
                ("company_id", "=", self.company_id.id),
                ("state", "=", "draft"),
                ("required_authority", "in", ["valentin", "joint"]),
            ],
            "context": {"delete": False},
        }

    def action_open_accountant_actions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Accountant Review Decisions",
            "res_model": "rebuild.account.review.decision",
            "view_mode": "list,form,pivot",
            "views": [(False, "list"), (False, "form"), (False, "pivot")],
            "domain": [
                "|",
                ("company_id", "=", False),
                ("company_id", "=", self.company_id.id),
                ("state", "=", "draft"),
                ("required_authority", "in", ["accountant", "joint"]),
            ],
            "context": {"delete": False},
        }

    def action_open_accounting_settings(self):
        self.ensure_one()
        return self.env["ir.actions.actions"]._for_xml_id(
            "account.action_account_config",
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
            "views": [(False, "form")],
            "context": {"create": False, "delete": False},
        }

    def action_open_open_discrepancies(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Open Accounting Discrepancies",
            "res_model": "rebuild.account.discrepancy",
            "view_mode": "list,form,pivot",
            "views": [(False, "list"), (False, "form"), (False, "pivot")],
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
            "views": [(False, "list"), (False, "form"), (False, "pivot")],
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
            "views": [(False, "list"), (False, "form"), (False, "pivot")],
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
            "views": [(False, "list"), (False, "form"), (False, "pivot")],
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
            "views": [(False, "list"), (False, "form"), (False, "pivot")],
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
            "views": [(False, "list"), (False, "form"), (False, "pivot")],
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
        today = fields.Date.context_today(self)
        fiscal_from, fiscal_to = date_utils.get_fiscal_year(
            today,
            day=self.company_id.fiscalyear_last_day,
            month=int(self.company_id.fiscalyear_last_month),
        )
        return {
            "type": "ir.actions.act_window",
            "name": "Accounting Report Workbench",
            "res_model": "rebuild.account.report.export.wizard",
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
            "context": {
                "default_company_id": self.company_id.id,
                "default_company_ids": [self.company_id.id],
                "default_report_type": "trial_balance",
                "default_data_scope": "native",
                "default_period_preset": "year_to_date",
                "default_period_anchor_date": today,
                "default_date_from": fiscal_from,
                "default_date_to": min(today, fiscal_to),
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
                       'Overview' AS name,
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
                       COALESCE(journals.journal_count, 0) AS journal_count,
                       COALESCE(journals.cash_journal_count, 0) AS cash_journal_count,
                       COALESCE(operational.bank_balance, 0.00) AS bank_balance,
                       COALESCE(bank_activity.bank_transaction_count, 0) AS bank_transaction_count,
                       COALESCE(bank_activity.unmatched_bank_transaction_count, 0) AS unmatched_bank_transaction_count,
                       COALESCE(bank_activity.bank_review_count, 0) AS bank_review_count,
                       COALESCE(documents.draft_customer_document_count, 0) AS draft_customer_document_count,
                       COALESCE(documents.draft_vendor_document_count, 0) AS draft_vendor_document_count,
                       COALESCE(expenses.draft_expense_count, 0) AS draft_expense_count,
                       COALESCE(documents.incomplete_document_count, 0) AS incomplete_document_count,
                       COALESCE(documents.missing_vendor_attachment_count, 0) AS missing_vendor_attachment_count,
                       COALESCE(expenses.missing_expense_attachment_count, 0) AS missing_expense_attachment_count,
                       COALESCE(documents.stale_draft_document_count, 0) AS stale_draft_document_count,
                       COALESCE(expenses.stale_draft_expense_count, 0) AS stale_draft_expense_count,
                       COALESCE(operational.open_receivable_count, 0) AS open_receivable_count,
                       COALESCE(operational.open_receivable_amount, 0.00) AS open_receivable_amount,
                       COALESCE(operational.open_payable_count, 0) AS open_payable_count,
                       COALESCE(operational.open_payable_amount, 0.00) AS open_payable_amount,
                       latest_closing.id AS latest_closing_period_id,
                       latest_closing.date_to AS latest_closing_date_to,
                       latest_closing.state AS latest_closing_state,
                       latest_closing.readiness_status AS latest_closing_readiness,
                       COALESCE(latest_closing.blocking_count, 0) AS latest_closing_blocking_count,
                       COALESCE(latest_closing.warning_count, 0) AS latest_closing_warning_count,
                       COALESCE(latest_closing.unusual_balance_count, 0) AS unusual_balance_count,
                       COALESCE(latest_closing.unusual_balance_amount, 0.00) AS unusual_balance_amount,
                       next_declaration.id AS next_declaration_id,
                       next_declaration.deadline_date AS next_declaration_deadline,
                       next_declaration.status AS next_declaration_status,
                       COALESCE(declaration_counts.overdue_declaration_count, 0) AS overdue_declaration_count,
                       COALESCE(declaration_counts.upcoming_declaration_count, 0) AS upcoming_declaration_count,
                       COALESCE(decisions.valentin_action_count, 0) AS valentin_action_count,
                       COALESCE(decisions.accountant_action_count, 0) AS accountant_action_count,
                       (
                           COALESCE(bank_activity.unmatched_bank_transaction_count, 0)
                         + COALESCE(documents.incomplete_document_count, 0)
                         + COALESCE(documents.missing_vendor_attachment_count, 0)
                         + COALESCE(expenses.missing_expense_attachment_count, 0)
                         + COALESCE(documents.stale_draft_document_count, 0)
                         + COALESCE(expenses.stale_draft_expense_count, 0)
                         + GREATEST(
                               COALESCE(latest_closing.warning_count, 0)
                             - CASE
                                   WHEN COALESCE(
                                       latest_closing.unusual_balance_count,
                                       0
                                   ) > 0
                                   THEN 1
                                   ELSE 0
                               END,
                               0
                           )
                         + COALESCE(latest_closing.unusual_balance_count, 0)
                         + COALESCE(declaration_counts.overdue_declaration_count, 0)
                         + COALESCE(discrepancies.open_p1_count, 0)
                         + COALESCE(decisions.pending_review_decision_count, 0)
                       )::integer AS hygiene_attention_count,
                       CASE
                           WHEN COALESCE(discrepancies.open_p0_count, 0) > 0
                             OR COALESCE(latest_closing.blocking_count, 0) > 0
                           THEN 'blocked'
                           WHEN COALESCE(bank_activity.unmatched_bank_transaction_count, 0) > 0
                             OR COALESCE(documents.incomplete_document_count, 0) > 0
                             OR COALESCE(documents.missing_vendor_attachment_count, 0) > 0
                             OR COALESCE(expenses.missing_expense_attachment_count, 0) > 0
                             OR COALESCE(documents.stale_draft_document_count, 0) > 0
                             OR COALESCE(expenses.stale_draft_expense_count, 0) > 0
                             OR COALESCE(latest_closing.warning_count, 0) > 0
                             OR COALESCE(latest_closing.unusual_balance_count, 0) > 0
                             OR COALESCE(declaration_counts.overdue_declaration_count, 0) > 0
                             OR COALESCE(discrepancies.open_p1_count, 0) > 0
                             OR COALESCE(decisions.pending_review_decision_count, 0) > 0
                           THEN 'attention'
                           ELSE 'ready'
                       END AS hygiene_status,
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
                       WHERE EXISTS (
                           SELECT 1
                             FROM rebuild_account_import_run_res_company_rel run_company
                            WHERE run_company.rebuild_account_import_run_id = run.id
                              AND run_company.res_company_id = company.id
                       )
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
                      SELECT count(*) FILTER (
                                 WHERE journal.active IS TRUE
                             )::integer AS journal_count,
                             count(*) FILTER (
                                 WHERE journal.active IS TRUE
                                   AND journal.type IN ('bank', 'cash')
                             )::integer AS cash_journal_count
                        FROM account_journal journal
                       WHERE journal.company_id = company.id
                  ) journals ON TRUE
                  LEFT JOIN LATERAL (
                      SELECT round(COALESCE(sum(line.balance) FILTER (
                                 WHERE account.account_type IN (
                                     'asset_cash',
                                     'liability_credit_card'
                                 )
                             ), 0)::numeric, 2) AS bank_balance,
                             count(*) FILTER (
                                 WHERE account.account_type = 'asset_receivable'
                                   AND line.reconciled IS FALSE
                                   AND abs(line.amount_residual) > 0.004
                             )::integer AS open_receivable_count,
                             round(COALESCE(sum(abs(line.amount_residual)) FILTER (
                                 WHERE account.account_type = 'asset_receivable'
                                   AND line.reconciled IS FALSE
                                   AND abs(line.amount_residual) > 0.004
                             ), 0)::numeric, 2) AS open_receivable_amount,
                             count(*) FILTER (
                                 WHERE account.account_type = 'liability_payable'
                                   AND line.reconciled IS FALSE
                                   AND abs(line.amount_residual) > 0.004
                             )::integer AS open_payable_count,
                             round(COALESCE(sum(abs(line.amount_residual)) FILTER (
                                 WHERE account.account_type = 'liability_payable'
                                   AND line.reconciled IS FALSE
                                   AND abs(line.amount_residual) > 0.004
                             ), 0)::numeric, 2) AS open_payable_amount
                        FROM account_move_line line
                        JOIN account_move move ON move.id = line.move_id
                        JOIN account_account account ON account.id = line.account_id
                       WHERE line.company_id = company.id
                         AND move.state = 'posted'
                  ) operational ON TRUE
                  LEFT JOIN LATERAL (
                      SELECT count(*)::integer AS bank_transaction_count,
                             count(*) FILTER (
                                 WHERE statement_line.is_reconciled IS NOT TRUE
                             )::integer AS unmatched_bank_transaction_count,
                             count(*) FILTER (
                                 WHERE move.checked IS FALSE
                             )::integer AS bank_review_count
                        FROM account_bank_statement_line statement_line
                        JOIN account_move move ON move.id = statement_line.move_id
                       WHERE statement_line.company_id = company.id
                  ) bank_activity ON TRUE
                  LEFT JOIN LATERAL (
                      SELECT count(*) FILTER (
                                 WHERE move.state = 'draft'
                                   AND move.move_type IN (
                                       'out_invoice',
                                       'out_refund',
                                       'out_receipt'
                                   )
                             )::integer AS draft_customer_document_count,
                             count(*) FILTER (
                                 WHERE move.state = 'draft'
                                   AND move.move_type IN (
                                       'in_invoice',
                                       'in_refund',
                                       'in_receipt'
                                   )
                             )::integer AS draft_vendor_document_count,
                             count(*) FILTER (
                                 WHERE move.state = 'draft'
                                   AND move.move_type IN (
                                       'out_invoice',
                                       'out_refund',
                                       'out_receipt',
                                       'in_invoice',
                                       'in_refund',
                                       'in_receipt'
                                   )
                                   AND (
                                       move.partner_id IS NULL
                                       OR move.invoice_date IS NULL
                                       OR NOT EXISTS (
                                           SELECT 1
                                             FROM account_move_line detail
                                            WHERE detail.move_id = move.id
                                              AND detail.display_type = 'product'
                                       )
                                   )
                             )::integer AS incomplete_document_count,
                             count(*) FILTER (
                                 WHERE move.state != 'cancel'
                                   AND move.move_type IN (
                                       'in_invoice',
                                       'in_refund',
                                       'in_receipt'
                                   )
                                   AND move.message_main_attachment_id IS NULL
                                   AND move.rebuild_source_id IS NULL
                             )::integer AS missing_vendor_attachment_count,
                             count(*) FILTER (
                                 WHERE move.state = 'draft'
                                   AND move.move_type IN (
                                       'out_invoice',
                                       'out_refund',
                                       'out_receipt',
                                       'in_invoice',
                                       'in_refund',
                                       'in_receipt'
                                   )
                                   AND move.date < CURRENT_DATE - INTERVAL '30 days'
                             )::integer AS stale_draft_document_count
                        FROM account_move move
                       WHERE move.company_id = company.id
                  ) documents ON TRUE
                  LEFT JOIN LATERAL (
                      SELECT count(*) FILTER (
                                 WHERE expense.state IN (
                                     'draft',
                                     'submitted',
                                     'approved'
                                 )
                             )::integer AS draft_expense_count,
                             count(*) FILTER (
                                 WHERE expense.state != 'refused'
                                   AND expense.message_main_attachment_id IS NULL
                                   AND expense.rebuild_source_id IS NULL
                             )::integer AS missing_expense_attachment_count,
                             count(*) FILTER (
                                 WHERE expense.state IN (
                                     'draft',
                                     'submitted',
                                     'approved'
                                 )
                                   AND expense.date < CURRENT_DATE - INTERVAL '30 days'
                             )::integer AS stale_draft_expense_count
                        FROM hr_expense expense
                       WHERE expense.company_id = company.id
                  ) expenses ON TRUE
                  LEFT JOIN LATERAL (
                      SELECT closing.id,
                             closing.date_to,
                             closing.state,
                             closing.readiness_status,
                             count(control.id) FILTER (
                                 WHERE control.status = 'block'
                             )::integer AS blocking_count,
                             count(control.id) FILTER (
                                 WHERE control.status = 'warning'
                             )::integer AS warning_count,
                             COALESCE(
                                 max(control.record_count) FILTER (
                                     WHERE control.code = 'unusual_balances'
                                 ),
                                 0
                             )::integer AS unusual_balance_count,
                             COALESCE(
                                 max(control.amount) FILTER (
                                     WHERE control.code = 'unusual_balances'
                                 ),
                                 0.00
                             ) AS unusual_balance_amount
                        FROM rebuild_account_closing_period closing
                        LEFT JOIN rebuild_account_closing_control control
                          ON control.closing_period_id = closing.id
                       WHERE closing.company_id = company.id
                         AND closing.state != 'archived'
                       GROUP BY closing.id
                       ORDER BY
                             (closing.date_to <= CURRENT_DATE) DESC,
                             CASE
                                 WHEN closing.date_to <= CURRENT_DATE
                                 THEN closing.date_to
                             END DESC NULLS LAST,
                             CASE
                                 WHEN closing.date_to > CURRENT_DATE
                                 THEN closing.date_to
                             END ASC NULLS LAST,
                             CASE closing.period_type
                                 WHEN 'month' THEN 1
                                 WHEN 'quarter' THEN 2
                                 ELSE 3
                             END
                       LIMIT 1
                  ) latest_closing ON TRUE
                  LEFT JOIN LATERAL (
                      SELECT declaration.id,
                             declaration.deadline_date,
                             declaration.status
                        FROM rebuild_account_declaration declaration
                       WHERE declaration.company_id = company.id
                         AND declaration.applicability != 'not_applicable'
                         AND declaration.status NOT IN (
                             'filed',
                             'paid',
                             'archived',
                             'not_applicable'
                         )
                       ORDER BY
                             (declaration.deadline_date < CURRENT_DATE),
                             CASE
                                 WHEN declaration.deadline_date >= CURRENT_DATE
                                 THEN declaration.deadline_date
                             END ASC NULLS LAST,
                             CASE
                                 WHEN declaration.deadline_date < CURRENT_DATE
                                 THEN declaration.deadline_date
                             END DESC NULLS LAST,
                             declaration.id
                       LIMIT 1
                  ) next_declaration ON TRUE
                  LEFT JOIN LATERAL (
                      SELECT count(*) FILTER (
                                 WHERE declaration.deadline_date < CURRENT_DATE
                             )::integer AS overdue_declaration_count,
                             count(*) FILTER (
                                 WHERE declaration.deadline_date
                                       BETWEEN CURRENT_DATE
                                           AND CURRENT_DATE + INTERVAL '45 days'
                             )::integer AS upcoming_declaration_count
                        FROM rebuild_account_declaration declaration
                       WHERE declaration.company_id = company.id
                         AND declaration.applicability != 'not_applicable'
                         AND declaration.status NOT IN (
                             'filed',
                             'paid',
                             'archived',
                             'not_applicable'
                         )
                  ) declaration_counts ON TRUE
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
                             count(*) FILTER (WHERE decision.state = 'recorded')::integer AS recorded_review_decision_count,
                             count(*) FILTER (
                                 WHERE decision.state = 'draft'
                                   AND decision.required_authority IN (
                                       'valentin',
                                       'joint'
                                   )
                             )::integer AS valentin_action_count,
                             count(*) FILTER (
                                 WHERE decision.state = 'draft'
                                   AND decision.required_authority IN (
                                       'accountant',
                                       'joint'
                                   )
                             )::integer AS accountant_action_count
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
            )
            """,
        )
