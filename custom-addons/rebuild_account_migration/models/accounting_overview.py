from odoo import fields, models, tools
from odoo.exceptions import UserError


class RebuildAccountOverview(models.Model):
    """Company-scoped operational Accounting cockpit."""

    _name = "rebuild.account.overview"
    _description = "USL Accounting Overview"
    _auto = False
    _order = "company_id"
    _rec_name = "name"

    name = fields.Char(readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
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
    evidence_status = fields.Selection(
        [
            ("ready", "Ready"),
            ("attention", "Attention Required"),
        ],
        compute="_compute_evidence_status",
    )
    review_decision_count = fields.Integer(readonly=True)
    pending_review_decision_count = fields.Integer(readonly=True)
    recorded_review_decision_count = fields.Integer(readonly=True)
    external_report_value_count = fields.Integer(readonly=True)
    pending_external_report_value_count = fields.Integer(readonly=True)
    readiness_status = fields.Selection(
        [
            ("blocked", "Blocked"),
            ("review_required", "Review Required"),
            ("technical_evidence_available", "Review Evidence Available"),
        ],
        readonly=True,
    )

    def _compute_hygiene_issue_count(self):
        Issue = self.env["rebuild.account.hygiene.issue"]
        counts = {
            company.id: count
            for company, count in Issue._read_group(
                [
                    ("company_id", "in", self.company_id.ids),
                    ("status", "=", "open"),
                ],
                ["company_id"],
                ["__count"],
            )
        }
        for summary in self:
            summary.hygiene_issue_count = counts.get(summary.company_id.id, 0)

    def _compute_evidence_status(self):
        for summary in self:
            summary.evidence_status = (
                "attention"
                if summary.missing_vendor_attachment_count
                else "ready"
            )

    def _bank_attention_domain(self):
        if not self:
            raise UserError(self.env._("No selected company is available."))
        company_ids = self.company_id.ids
        company_domain = (
            ("company_id", "=", company_ids[0])
            if len(company_ids) == 1
            else ("company_id", "in", company_ids)
        )
        return [
            company_domain,
            "|",
            ("is_reconciled", "=", False),
            ("move_id.review_state", "in", ("todo", "anomaly")),
        ]

    def _missing_vendor_attachments_domain(self):
        return [
            (
                "move_type",
                "in",
                ["in_invoice", "in_refund", "in_receipt"],
            ),
            ("state", "!=", "cancel"),
            ("message_main_attachment_id", "=", False),
        ]

    def _missing_expense_attachments_domain(self):
        return [
            ("state", "!=", "refused"),
            ("message_main_attachment_id", "=", False),
        ]

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT company.id AS id,
                       'Overview' AS name,
                       company.id AS company_id,
                       company.currency_id,
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
                         + COALESCE(decisions.pending_review_decision_count, 0)
                       )::integer AS hygiene_attention_count,
                       CASE
                           WHEN COALESCE(latest_closing.blocking_count, 0) > 0
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
                             OR COALESCE(decisions.pending_review_decision_count, 0) > 0
                           THEN 'attention'
                           ELSE 'ready'
                       END AS hygiene_status,
                       COALESCE(decisions.review_decision_count, 0) AS review_decision_count,
                       COALESCE(decisions.pending_review_decision_count, 0) AS pending_review_decision_count,
                       COALESCE(decisions.recorded_review_decision_count, 0) AS recorded_review_decision_count,
                       COALESCE(external_values.external_report_value_count, 0) AS external_report_value_count,
                       COALESCE(external_values.pending_external_report_value_count, 0) AS pending_external_report_value_count,
                       CASE
                           WHEN abs(COALESCE(ledger.balance, 0.00)) > 0.004
                           THEN 'blocked'
                           WHEN COALESCE(decisions.pending_review_decision_count, 0) > 0
                             OR COALESCE(external_values.pending_external_report_value_count, 0) > 0
                           THEN 'review_required'
                           ELSE 'technical_evidence_available'
                       END AS readiness_status
                  FROM res_company company
                  LEFT JOIN LATERAL (
                      SELECT count(DISTINCT move.id)::integer AS posted_move_count,
                             count(line.id)::integer AS move_line_count,
                             round(COALESCE(sum(line.debit), 0)::numeric, 2) AS debit,
                             round(COALESCE(sum(line.credit), 0)::numeric, 2) AS credit,
                             round(COALESCE(sum(line.balance), 0)::numeric, 2) AS balance
                        FROM account_move_line line
                        JOIN account_move move ON move.id = line.move_id
                       WHERE line.company_id = company.id
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
                             round(COALESCE(sum(line.amount_residual) FILTER (
                                 WHERE account.account_type = 'asset_receivable'
                                   AND line.reconciled IS FALSE
                                   AND abs(line.amount_residual) > 0.004
                             ), 0)::numeric, 2) AS open_receivable_amount,
                             count(*) FILTER (
                                 WHERE account.account_type = 'liability_payable'
                                   AND line.reconciled IS FALSE
                                   AND abs(line.amount_residual) > 0.004
                             )::integer AS open_payable_count,
                             round(COALESCE(sum(line.amount_residual) FILTER (
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
                                 WHERE move.review_state IN ('todo', 'anomaly')
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
                        FROM rebuild_account_assurance_decision decision
                       WHERE decision.company_id IS NULL
                          OR decision.company_id = company.id
                  ) decisions ON TRUE
                  LEFT JOIN LATERAL (
                      SELECT count(*) FILTER (WHERE value.active IS TRUE)::integer AS external_report_value_count,
                             count(*) FILTER (WHERE value.active IS TRUE AND value.review_status = 'pending_review')::integer AS pending_external_report_value_count
                        FROM rebuild_account_external_report_value value
                       WHERE value.company_id = company.id
                  ) external_values ON TRUE
            )
            """,
        )
