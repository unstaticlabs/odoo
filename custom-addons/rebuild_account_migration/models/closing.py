import calendar
import hashlib
import json

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import BinaryBytes

from .configurable_definition import ACCOUNTING_DEFINITION_ORIGINS

PROFIT_AND_LOSS_ACCOUNT_TYPES = (
    "income",
    "income_other",
    "expense",
    "expense_other",
    "expense_depreciation",
    "expense_direct_cost",
)

CLOSING_CONTROL_CATEGORIES = [
    ("accounting", "Accounting"),
    ("documents", "Documents"),
    ("reconciliation", "Reconciliation"),
    ("tax", "Tax and Declarations"),
    ("payroll", "Payroll"),
    ("assets", "Assets and Deferrals"),
    ("currency", "Currency"),
    ("analytic", "Analytics"),
    ("issues", "Issues"),
    ("reports", "Reports"),
    ("fec", "FEC"),
    ("locks", "Lock Dates"),
]

CLOSING_CONTROL_OWNERS = [
    ("accounting_manager", "Accounting Manager"),
    ("accountant_reviewer", "Accountant Reviewer"),
    ("finance_operator", "Finance Operator / Agent"),
]

CLOSING_CONTROL_IMPACT_POLICIES = [
    ("evaluator", "Dynamic — Use Evaluator Recommendation"),
    ("informational", "Informational — Does Not Affect Readiness"),
    ("advisory", "Advisory — Creates a Readiness Warning"),
    ("blocking", "Blocking — Prevents Readiness"),
]

CLOSING_CONTROL_DEFINITIONS = (
    ("accounting_completeness", "accounting", "Accounting completeness", "Checks for draft miscellaneous journal entries in the period.", "Unreviewed drafts can change the final ledger after the close.", "accounting_manager"),
    ("document_completeness", "documents", "Document completeness", "Checks for draft invoices and bills and posted documents without primary evidence.", "Incomplete documents weaken balances, tax support and the audit trail.", "accounting_manager"),
    ("bank_reconciliation", "reconciliation", "Bank reconciliation", "Checks for bank transactions that still have an unmatched residual.", "Unmatched cash movements make cash and counterpart balances unreliable.", "accounting_manager"),
    ("partner_open_items", "reconciliation", "Receivable and payable review", "Surfaces customer and supplier balances still open at period end.", "Old or unexplained residuals can misstate receivables, payables and cash collection.", "finance_operator"),
    ("unusual_balances", "accounting", "Unusual account balances", "Finds accounts whose aggregate balance is opposite to their configured natural side.", "Wrong-way balances often reveal classification, sign or reconciliation problems.", "finance_operator"),
    ("tax_declarations", "tax", "Tax and declaration readiness", "Checks applicable declarations and their preparation status for the period.", "Late or unsupported declarations create compliance and payment risk.", "accounting_manager"),
    ("payroll", "payroll", "Payroll accounting", "Checks payroll-related postings and review evidence when payroll accounts are used.", "Missing or unexplained payroll entries can understate liabilities and expenses.", "accounting_manager"),
    ("assets_deferrals", "assets", "Assets and deferrals", "Checks asset, depreciation and deferred-recognition schedules used by the period.", "Incomplete schedules can misstate assets, liabilities and period results.", "accounting_manager"),
    ("currency", "currency", "Foreign-currency review", "Checks foreign-currency activity and the supporting rate coverage.", "Missing rates or valuation review can misstate balances and exchange differences.", "accounting_manager"),
    ("analytic", "analytic", "Analytic completeness", "Checks whether relevant posted expense and revenue lines have analytic allocation.", "Missing allocation weakens management reporting without changing the general ledger.", "finance_operator"),
    ("issues", "issues", "Accounting Hygiene issues", "Checks unresolved Accounting Hygiene exceptions affecting the period.", "Known exceptions should be resolved or explicitly documented before closing.", "accounting_manager"),
    ("reports", "reports", "Closing reports", "Checks availability of the core reports needed to review the period.", "A close cannot be reviewed consistently without reproducible supporting reports.", "accounting_manager"),
    ("fec", "fec", "FEC readiness", "Checks whether the statutory accounting export can be generated for the period.", "An unavailable or invalid FEC is a material French compliance risk.", "accounting_manager"),
    ("lock_dates", "locks", "Lock-date readiness", "Checks the current lock dates and whether they protect the reviewed period.", "Unlocked reviewed periods can be changed inadvertently after sign-off.", "accounting_manager"),
)

HYGIENE_CONTROL_DEFINITIONS = (
    (
        "hygiene_bank_statement",
        "reconciliation",
        "Monthly bank statement evidence",
        "Checks scheduled bank exports, statement evidence, balance agreement and certified continuity.",
        "An incomplete checkpoint weakens confidence that every bank movement for the month is present.",
        "accountant_reviewer",
    ),
    (
        "hygiene_bank_unmatched",
        "reconciliation",
        "Unmatched bank transactions",
        "Finds bank transactions whose accounting counterpart or category is incomplete.",
        "Cash can be correct while counterpart accounts, partners, taxes or open-item statuses remain incomplete.",
        "finance_operator",
    ),
    (
        "hygiene_vendor_evidence",
        "documents",
        "Supplier document evidence",
        "Finds new supplier documents without a main invoice, receipt or supporting file.",
        "Unsupported supplier documents weaken deductibility, VAT support and the audit trail.",
        "finance_operator",
    ),
    (
        "hygiene_stale_documents",
        "documents",
        "Stale draft documents",
        "Finds draft invoices, bills and receipts that have remained unfinished beyond the configured evaluator window.",
        "Old drafts may hide missing liabilities, receivables or corrections from posted-ledger reporting.",
        "finance_operator",
    ),
    (
        "hygiene_expense_evidence",
        "documents",
        "Expense receipt evidence",
        "Finds new expenses without a receipt or supporting file.",
        "Unsupported expenses and deductible VAT are difficult to review or substantiate.",
        "finance_operator",
    ),
    (
        "hygiene_stale_expenses",
        "documents",
        "Stale expense workflows",
        "Finds expense workflows that have not reached accounting within the configured evaluator window.",
        "Unfinished expenses delay reimbursement and omit costs from management reporting.",
        "finance_operator",
    ),
    (
        "hygiene_analytic_allocation",
        "analytic",
        "Missing analytic allocation",
        "Finds posted profit-and-loss journal items without an analytic distribution.",
        "General accounting remains balanced, but activity and profitability reporting can be incomplete.",
        "finance_operator",
    ),
    (
        "hygiene_duplicate_documents",
        "documents",
        "Possible duplicate supplier documents",
        "Finds posted supplier documents sharing the same partner, reference, date and signed total.",
        "A confirmed duplicate overstates expense, VAT and supplier liability.",
        "accounting_manager",
    ),
)

CANONICAL_CLOSING_REPORT_ACTIONS = (
    "action_rebuild_interactive_trial_balance",
    "action_rebuild_interactive_general_ledger",
    "action_rebuild_interactive_journal_report",
    "action_rebuild_interactive_partner_ledger",
    "action_rebuild_interactive_open_items",
    "action_rebuild_interactive_aged_receivable",
    "action_rebuild_interactive_aged_payable",
    "action_rebuild_interactive_balance_sheet",
    "action_rebuild_interactive_profit_loss",
    "action_rebuild_interactive_french_balance_sheet_2024",
    "action_rebuild_interactive_french_profit_loss_2024",
    "action_rebuild_interactive_tax_report",
    "action_rebuild_interactive_fixed_assets",
    "action_rebuild_interactive_depreciation_schedule",
    "action_rebuild_interactive_sig_caf_2024",
    "action_rebuild_interactive_analytic_report",
)


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def _has_current_accepted_closing_snapshot(self):
        if not self:
            return False
        return bool(
            self.env["rebuild.account.closing.snapshot"].sudo().search_count([
                ("source_attachment_id", "in", self.ids),
                ("review_decision_id.state", "=", "recorded"),
                (
                    "review_decision_id.conclusion",
                    "in",
                    ["accepted", "accepted_with_difference"],
                ),
            ]),
        )

    def write(self, vals):
        protected_fields = {
            "name",
            "datas",
            "raw",
            "db_datas",
            "store_fname",
            "checksum",
            "file_size",
            "mimetype",
            "type",
            "url",
            "res_model",
            "res_id",
        }
        if (
            protected_fields & set(vals)
            and self._has_current_accepted_closing_snapshot()
        ):
            raise UserError(
                "An attachment captured by the current accepted closing "
                "decision is locked. Supersede that decision before changing "
                "the package file."
            )
        return super().write(vals)

    def unlink(self):
        if self._has_current_accepted_closing_snapshot():
            raise UserError(
                "An attachment captured by the current accepted closing "
                "decision cannot be deleted."
            )
        return super().unlink()


class AccountAccount(models.Model):
    _inherit = "account.account"

    rebuild_hygiene_balance_policy = fields.Selection(
        [
            ("auto", "Automatic from Account Type"),
            ("debit", "Debit Balance Expected"),
            ("credit", "Credit Balance Expected"),
            ("either", "Debit or Credit Is Expected"),
        ],
        string="Hygiene Balance Policy",
        required=True,
        default="auto",
        help=(
            "Controls whether Accounting Hygiene flags a wrong-way aggregate "
            "balance. Automatic mode uses the account type and common French "
            "contra-account prefixes. This is a review signal, not an "
            "automatic accounting correction."
        ),
    )

    def _rebuild_hygiene_expected_balance_side(self):
        self.ensure_one()
        if self.rebuild_hygiene_balance_policy != "auto":
            return self.rebuild_hygiene_balance_policy

        code = self.code or ""
        if self.account_type in {"off_balance", "equity_unaffected"}:
            return "either"
        if code.startswith(("603", "71", "72")):
            return "either"
        if code.startswith(("28", "29", "39", "49", "59", "609", "619", "629")):
            return "credit"
        if code.startswith("709"):
            return "debit"
        if self.account_type.startswith(("asset", "expense")):
            return "debit"
        if self.account_type.startswith(("liability", "income")) or self.account_type == "equity":
            return "credit"
        return "either"


class RebuildAccountClosingPeriod(models.Model):
    _name = "rebuild.account.closing.period"
    _description = "USL Accounting Closing Workspace"
    _order = "date_to desc, period_type, company_id"

    _unique_closing_period = models.Constraint(
        "UNIQUE (company_id, period_type, date_from, date_to)",
        "This closing workspace already exists for the company and period.",
    )

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)
    period_type = fields.Selection(
        [("month", "Month End"), ("quarter", "Quarter End"), ("annual", "Annual Statutory Close")],
        required=True,
        index=True,
    )
    date_from = fields.Date(required=True, index=True)
    date_to = fields.Date(required=True, index=True)
    fiscalyear_start = fields.Date(required=True, index=True)
    fiscalyear_end = fields.Date(required=True, index=True)
    state = fields.Selection(
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
        required=True,
        default="open",
        index=True,
    )
    review_status = fields.Selection(
        [
            ("not_started", "Not Started"),
            ("internal_ready", "Internal Ready"),
            ("accountant_requested", "Accountant Review Requested"),
            ("accepted", "Accepted by Reviewer"),
            ("accepted_with_difference", "Accepted with Difference"),
            ("rejected", "Changes Required"),
        ],
        required=True,
        default="not_started",
    )
    readiness_status = fields.Selection(
        [("not_run", "Not Run"), ("ready", "Ready"), ("warning", "Warnings"), ("blocked", "Blocked")],
        required=True,
        default="not_run",
        index=True,
    )
    control_line_ids = fields.One2many("rebuild.account.closing.control", "closing_period_id", string="Closing Controls")
    blocking_count = fields.Integer(compute="_compute_control_counts")
    warning_count = fields.Integer(compute="_compute_control_counts")
    information_count = fields.Integer(compute="_compute_control_counts")
    technical_failure_count = fields.Integer(compute="_compute_control_counts")
    passed_count = fields.Integer(compute="_compute_control_counts")
    readiness_summary = fields.Text()
    actions_awaiting_valentin = fields.Text()
    accountant_information = fields.Text()
    closing_notes = fields.Text()
    package_reference = fields.Char()
    package_attachment_ids = fields.Many2many(
        "ir.attachment",
        "rebuild_closing_attachment_rel",
        "closing_period_id",
        "attachment_id",
        string="Closing Package Evidence",
    )
    snapshot_ids = fields.One2many(
        "rebuild.account.closing.snapshot",
        "closing_period_id",
        string="Accepted Closing Snapshots",
        readonly=True,
    )
    snapshot_count = fields.Integer(compute="_compute_snapshot_count")
    previous_lock_dates = fields.Text(readonly=True)
    final_lock_dates = fields.Text(readonly=True)
    last_refreshed_at = fields.Datetime(readonly=True)
    closed_at = fields.Datetime(readonly=True)
    closed_by_id = fields.Many2one("res.users", readonly=True)

    @api.depends("control_line_ids", "control_line_ids.status")
    def _compute_control_counts(self):
        for closing in self:
            closing.blocking_count = len(closing.control_line_ids.filtered(lambda line: line.status == "block"))
            closing.warning_count = len(closing.control_line_ids.filtered(lambda line: line.status == "warning"))
            closing.information_count = len(
                closing.control_line_ids.filtered(lambda line: line.status == "info"),
            )
            closing.technical_failure_count = len(
                closing.control_line_ids.filtered(
                    lambda line: line.status == "technical_error",
                ),
            )
            closing.passed_count = len(closing.control_line_ids.filtered(lambda line: line.status == "pass"))

    @api.depends("snapshot_ids")
    def _compute_snapshot_count(self):
        for closing in self:
            closing.snapshot_count = len(closing.snapshot_ids)

    def write(self, vals):
        if {"package_attachment_ids", "package_reference"} & set(vals):
            locked = self.filtered(
                lambda closing: any(
                    snapshot.review_decision_id.state == "recorded"
                    for snapshot in closing.snapshot_ids
                ),
            )
            if locked:
                raise UserError(
                    "Accepted closing-package evidence is locked. Supersede "
                    "the recorded closing decision before changing the "
                    "package reference or attachments."
                )
        return super().write(vals)

    @api.model
    def sync_for_company(self, company):
        company.ensure_one()
        if not company.rebuild_declaration_profile_active:
            return self.browse()
        today = fields.Date.context_today(self)
        max_move = self.env["account.move"].search([
            ("company_id", "=", company.id),
            ("state", "=", "posted"),
        ], order="date desc", limit=1)
        data_end = min(max_move.date or today, today)
        fiscal_start, fiscal_end = (
            company.rebuild_compute_fiscalyear_dates(data_end)
        )
        closings = self.browse()
        cursor = fiscal_start
        while cursor <= data_end:
            month_end = cursor.replace(day=calendar.monthrange(cursor.year, cursor.month)[1])
            period_end = min(month_end, fiscal_end)
            closings |= self._upsert_period(company, "month", cursor, period_end, fiscal_start, fiscal_end)
            cursor = period_end + relativedelta(days=1)
        quarter_start = fiscal_start
        while quarter_start <= data_end:
            quarter_end = min(quarter_start + relativedelta(months=3, days=-1), fiscal_end)
            closings |= self._upsert_period(company, "quarter", quarter_start, quarter_end, fiscal_start, fiscal_end)
            quarter_start = quarter_end + relativedelta(days=1)
        closings |= self._upsert_period(company, "annual", fiscal_start, fiscal_end, fiscal_start, fiscal_end)
        if company.fiscalyear_lock_date:
            locked_start, locked_end = (
                company.rebuild_compute_fiscalyear_dates(
                    company.fiscalyear_lock_date,
                )
            )
            closings |= self._upsert_period(company, "annual", locked_start, locked_end, locked_start, locked_end)
        closings.action_refresh_controls()
        return closings

    @api.model
    def _upsert_period(self, company, period_type, date_from, date_to, fiscal_start, fiscal_end):
        closing = self.search([
            ("company_id", "=", company.id),
            ("period_type", "=", period_type),
            ("date_from", "=", date_from),
            ("date_to", "=", date_to),
        ], limit=1)
        label = {
            "month": date_from.strftime("%B %Y close"),
            "quarter": f"Quarter close {date_from:%d %b %Y} - {date_to:%d %b %Y}",
            "annual": f"Annual statutory close {date_from:%d %b %Y} - {date_to:%d %b %Y}",
        }[period_type]
        vals = {
            "name": label,
            "company_id": company.id,
            "period_type": period_type,
            "date_from": date_from,
            "date_to": date_to,
            "fiscalyear_start": fiscal_start,
            "fiscalyear_end": fiscal_end,
        }
        if closing:
            closing.write(vals)
        else:
            closing = self.create(vals)
        return closing

    def action_refresh_controls(self):
        for company in self.company_id:
            self.env["rebuild.account.hygiene.issue"].sync_for_company(company)
        for closing in self:
            closing._refresh_controls()
        return True

    def _refresh_controls(self):
        self.ensure_one()
        Definition = self.env["rebuild.account.closing.control.definition"]
        definitions = Definition._ensure_for_company(self.company_id).filtered(
            lambda definition: definition.enabled
            and definition.applies_to_closing
            and definition._is_effective(self.date_to)
            and definition._applies_to_period_type(self.period_type),
        )
        evaluator_registry = self._closing_control_evaluator_registry()
        controls = []
        for definition in definitions:
            evaluator_name = evaluator_registry.get(definition.evaluator_key)
            try:
                control = self._run_closing_control_evaluator(
                    definition,
                    evaluator_name,
                )
                control = definition._apply_result_policy(control)
            except Exception as exc:  # noqa: BLE001 - persist a governed technical result.
                control = self._control_values(
                    definition.code,
                    definition.category,
                    definition.name,
                    "technical_error",
                    1,
                    0.0,
                    (
                        "This control could not be evaluated. No accounting "
                        f"failure is asserted. Technical error: {type(exc).__name__}: {exc}"
                    ),
                    "Ask a Technical Administrator to inspect the configured evaluator.",
                    owner=definition.owner,
                    accountant_visible=definition.accountant_visible,
                )
            controls.append({
                **control,
                "definition_id": definition.id,
                "sequence": definition.sequence,
                "category": definition.category,
                "name": definition.name,
                "owner": definition.owner,
                "accountant_visible": definition.accountant_visible,
                "definition_version": definition.definition_version,
                "definition_snapshot": definition._definition_snapshot(),
            })
        seen = set()
        Control = self.env["rebuild.account.closing.control"]
        for values in controls:
            seen.add(values["code"])
            Control._upsert(self, values["code"], values)
        self.control_line_ids.filtered(lambda line: line.code not in seen).unlink()
        blocking = [control for control in controls if control["status"] == "block"]
        warnings = [control for control in controls if control["status"] == "warning"]
        technical_failures = [
            control for control in controls if control["status"] == "technical_error"
        ]
        information = [control for control in controls if control["status"] == "info"]
        passed_or_not_applicable = [
            control
            for control in controls
            if control["status"] in {"pass", "not_applicable"}
        ]
        readiness = (
            "blocked"
            if blocking or technical_failures
            else "warning"
            if warnings
            else "ready"
        )
        actions = [
            control["next_action"]
            for control in blocking + technical_failures + warnings
            if control.get("owner") == "accounting_manager"
        ]
        accountant = [control["summary"] for control in controls if control.get("accountant_visible")]
        vals = {
            "readiness_status": readiness,
            "readiness_summary": (
                f"{len(blocking)} accounting blocker(s), "
                f"{len(technical_failures)} technical failure(s), "
                f"{len(warnings)} warning(s), {len(information)} information item(s), "
                f"{len(passed_or_not_applicable)} passed/not-applicable control(s)."
            ),
            "actions_awaiting_valentin": "\n".join(actions),
            "accountant_information": "\n".join(accountant),
            "last_refreshed_at": fields.Datetime.now(),
        }
        if self.state in {"open", "preparing", "blocked", "internal_review"}:
            vals["state"] = (
                "blocked" if blocking or technical_failures else "internal_review"
            )
        self.write(vals)

    def _run_closing_control_evaluator(self, definition, evaluator_name):
        if not evaluator_name or not hasattr(self, evaluator_name):
            message = (
                "No installed evaluator is registered for "
                f"{definition.evaluator_key or definition.code}."
            )
            raise UserError(message)
        return getattr(self, evaluator_name)()

    @api.model
    def _closing_control_evaluator_registry(self):
        """Whitelisted evaluator extension point for installed modules."""
        return {
            code: f"_control_{code}"
            for code, _category, _name, _description, _consequence, _owner
            in CLOSING_CONTROL_DEFINITIONS
        }

    def _control_values(self, code, category, name, status, count, amount, summary, next_action, owner="accounting_manager", accountant_visible=True):
        return {
            "code": code,
            "category": category,
            "name": name,
            "status": status,
            "record_count": count,
            "amount": amount,
            "summary": summary,
            "next_action": next_action,
            "owner": owner,
            "accountant_visible": accountant_visible,
        }

    def _control_accounting_completeness(self):
        draft_count = self.env["account.move"].search_count([
            ("company_id", "=", self.company_id.id),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
            ("state", "=", "draft"),
            ("move_type", "=", "entry"),
        ])
        return self._control_values(
            "accounting_completeness", "accounting", "Accounting completeness",
            "block" if draft_count else "pass", draft_count, 0.0,
            f"{draft_count} draft journal entr{'y' if draft_count == 1 else 'ies'} in the close period.",
            "Review, post, cancel or document every draft journal entry before close.",
        )

    def _control_document_completeness(self):
        base = [
            ("company_id", "=", self.company_id.id),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
            ("move_type", "in", ["out_invoice", "out_refund", "in_invoice", "in_refund"]),
        ]
        drafts = self.env["account.move"].search_count([*base, ("state", "=", "draft")])
        missing_evidence = self.env["account.move"].search_count([
            *base,
            ("state", "=", "posted"),
            ("message_main_attachment_id", "=", False),
        ])
        status = "block" if drafts else "warning" if missing_evidence else "pass"
        return self._control_values(
            "document_completeness", "documents", "Document completeness", status,
            drafts + missing_evidence, 0.0,
            (
                f"{drafts} draft business document(s); {missing_evidence} "
                "posted document(s) without a main attachment."
            ),
            "Resolve draft documents and attach or explicitly document missing source evidence.",
        )

    def _control_bank_reconciliation(self):
        lines = self.env["account.bank.statement.line"].search([
            ("company_id", "=", self.company_id.id),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
            ("is_reconciled", "=", False),
        ])
        amount = sum(abs(value) for value in lines.mapped("amount_residual"))
        return self._control_values(
            "bank_reconciliation", "reconciliation", "Bank reconciliation",
            "block" if lines else "pass", len(lines), amount,
            f"{len(lines)} bank statement line(s) remain unreconciled; absolute residual {amount:.2f} {self.currency_id.name}.",
            "Finish OCA Bank Matching or document a deliberate boundary before close.",
        )

    def _control_partner_open_items(self):
        lines = self.env["account.move.line"].search([
            ("company_id", "=", self.company_id.id),
            ("move_id.state", "=", "posted"),
            ("date", "<=", self.date_to),
            ("account_id.account_type", "in", ["asset_receivable", "liability_payable"]),
            ("amount_residual", "!=", 0),
        ])
        amount = sum(abs(value) for value in lines.mapped("amount_residual"))
        return self._control_values(
            "partner_open_items", "reconciliation", "Receivable and payable review",
            "warning" if lines else "pass", len(lines), amount,
            f"{len(lines)} open receivable/payable item(s), absolute residual {amount:.2f} {self.currency_id.name}.",
            "Review ageing and document legitimate open customer and supplier balances.", owner="finance_operator",
        )

    def _unusual_balance_rows(self):
        self.ensure_one()
        MoveLine = self.env["account.move.line"].with_company(self.company_id)
        common_domain = [
            ("company_id", "=", self.company_id.id),
            ("move_id.state", "=", "posted"),
            ("date", "<=", self.date_to),
        ]
        balance_sheet_rows = MoveLine._read_group(
            [
                *common_domain,
                ("account_id.account_type", "not in", PROFIT_AND_LOSS_ACCOUNT_TYPES),
            ],
            ["account_id"],
            ["balance:sum", "__count"],
        )
        profit_and_loss_rows = MoveLine._read_group(
            [
                *common_domain,
                ("date", ">=", self.fiscalyear_start),
                ("account_id.account_type", "in", PROFIT_AND_LOSS_ACCOUNT_TYPES),
            ],
            ["account_id"],
            ["balance:sum", "__count"],
        )
        unusual = []
        for account, balance, line_count in balance_sheet_rows + profit_and_loss_rows:
            if self.currency_id.is_zero(balance):
                continue
            expected_side = account.with_company(
                self.company_id,
            )._rebuild_hygiene_expected_balance_side()
            if (
                (expected_side == "debit" and balance < 0)
                or (expected_side == "credit" and balance > 0)
            ):
                unusual.append((account, balance, line_count, expected_side))
        return sorted(unusual, key=lambda row: abs(row[1]), reverse=True)

    def _control_unusual_balances(self):
        unusual = self._unusual_balance_rows()
        amount = sum(abs(balance) for _account, balance, _line_count, _side in unusual)
        preview = ", ".join(
            account.with_company(self.company_id).code
            for account, _balance, _line_count, _side in unusual[:5]
        )
        summary = (
            f"{len(unusual)} account(s) have an aggregate balance opposite "
            f"their configured natural side; absolute review amount "
            f"{amount:.2f} {self.currency_id.name}."
        )
        if preview:
            summary = f"{summary} Largest signals: {preview}."
        return self._control_values(
            "unusual_balances",
            "accounting",
            "Unusual account balances",
            "warning" if unusual else "pass",
            len(unusual),
            amount,
            summary,
            (
                "Review the account-grouped journal items and document a "
                "legitimate overdraft, advance, contra balance or correction."
            ),
            owner="finance_operator",
        )

    def _control_tax_declarations(self):
        declarations = self.env["rebuild.account.declaration"].search([
            ("company_id", "=", self.company_id.id),
            ("deadline_date", "<=", self.date_to),
            ("status", "not in", ["filed", "paid", "archived", "not_applicable"]),
        ])
        period_declarations = self.env["rebuild.account.declaration"].search([
            ("company_id", "=", self.company_id.id),
            ("fiscalyear_start", "=", self.fiscalyear_start),
            ("fiscalyear_end", "=", self.fiscalyear_end),
            ("validation_status", "=", "blocked"),
        ]) if self.period_type == "annual" else self.env["rebuild.account.declaration"]
        blocked = declarations | period_declarations
        return self._control_values(
            "tax_declarations", "tax", "Tax and declaration readiness",
            "block" if blocked else "pass", len(blocked), sum(blocked.mapped("amount_due")),
            f"{len(blocked)} due or annual declaration obligation(s) are not ready/complete.",
            "Resolve declaration fields, obtain reviewer decisions and record external filing/payment evidence.",
        )

    def _control_payroll(self):
        if "hr.payslip" not in self.env:
            return self._control_values(
                "payroll", "payroll", "Payroll status", "not_applicable", 0, 0.0,
                "Payroll is outside the installed Community accounting stack; an external payroll package must be retained.",
                "Confirm the external payroll and social-declaration package for the period.", owner="finance_operator",
            )
        drafts = self.env["hr.payslip"].search_count([
            ("company_id", "=", self.company_id.id),
            ("date_from", "<=", self.date_to),
            ("date_to", ">=", self.date_from),
            ("state", "not in", ["done", "paid", "cancel"]),
        ])
        return self._control_values(
            "payroll", "payroll", "Payroll status", "block" if drafts else "pass", drafts, 0.0,
            f"{drafts} payroll record(s) remain incomplete.", "Complete payroll and reconcile its accounting entries.",
        )

    def _control_assets_deferrals(self):
        asset_lines = self.env["account.asset.line"].search([
            ("asset_id.company_id", "=", self.company_id.id),
            ("line_date", ">=", self.date_from),
            ("line_date", "<=", self.date_to),
            ("type", "=", "depreciate"),
        ])
        asset_gaps = len(asset_lines.filtered(
            lambda line: not line.init_entry
            and (not line.move_id or line.move_id.state != "posted"),
        ))
        deferral_gaps = self.env["rebuild.account.deferral.line"].search_count([
            ("company_id", "=", self.company_id.id),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
            ("state", "=", "scheduled"),
        ])
        count = asset_gaps + deferral_gaps
        return self._control_values(
            "assets_deferrals", "assets", "Assets and deferrals", "block" if count else "pass", count, 0.0,
            f"{asset_gaps} asset schedule gap(s); {deferral_gaps} deferred schedule review item(s).",
            "Post or resolve asset depreciation and deferred expense/revenue schedule items.",
        )

    def _control_currency(self):
        lines = self.env["account.move.line"].search([
            ("company_id", "=", self.company_id.id),
            ("move_id.state", "=", "posted"),
            ("date", "<=", self.date_to),
            ("currency_id", "!=", False),
            ("currency_id", "!=", self.currency_id.id),
            ("account_id.account_type", "in", ["asset_receivable", "liability_payable"]),
            ("amount_residual_currency", "!=", 0),
        ])
        amount = sum(abs(value) for value in lines.mapped("amount_residual"))
        return self._control_values(
            "currency", "currency", "Foreign-currency control", "warning" if lines else "pass", len(lines), amount,
            f"{len(lines)} open foreign-currency receivable/payable line(s), company-currency residual {amount:.2f}.",
            "Review currency exposure and record any required period-end revaluation outside the immutable source replay.", owner="finance_operator",
        )

    def _control_analytic(self):
        lines = self.env["account.move.line"].search([
            ("company_id", "=", self.company_id.id),
            ("move_id.state", "=", "posted"),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
            ("account_id.account_type", "in", ["income", "income_other", "expense", "expense_direct_cost", "expense_depreciation"]),
            ("analytic_distribution", "=", False),
        ])
        amount = sum(abs(value) for value in lines.mapped("balance"))
        return self._control_values(
            "analytic", "analytic", "Analytic completeness", "warning" if lines else "pass", len(lines), amount,
            f"{len(lines)} profit-and-loss line(s) have no analytic distribution; absolute balance {amount:.2f}.",
            "Confirm whether unallocated lines need analytic coding or a documented exclusion.", owner="finance_operator",
        )

    def _control_issues(self):
        hygiene_issues = self.env["rebuild.account.hygiene.issue"].search([
            ("company_id", "=", self.company_id.id),
            ("status", "=", "open"),
            "|",
            ("issue_date", "=", False),
            ("issue_date", "<=", self.date_to),
        ])
        blocking_hygiene = hygiene_issues.filtered(
            lambda issue: issue.severity == "1_blocking",
        )
        blocking_count = len(blocking_hygiene)
        actionable_hygiene = hygiene_issues.filtered(
            lambda issue: issue.severity != "4_information",
        )
        issue_count = len(actionable_hygiene)
        return self._control_values(
            "issues", "issues", "Accounting Hygiene issues",
            "block" if blocking_count else "warning" if issue_count else "pass",
            issue_count, sum(hygiene_issues.mapped("amount")),
            (
                f"{len(actionable_hygiene)} actionable and "
                f"{len(hygiene_issues - actionable_hygiene)} informational "
                "Hygiene result(s); "
                f"{blocking_count} blocking."
            ),
            "Open Accounting Hygiene, resolve the underlying records, and refresh the controls.",
        )

    def _control_reports(self):
        missing = [
            action_name
            for action_name in CANONICAL_CLOSING_REPORT_ACTIONS
            if not self.env.ref(
                f"rebuild_account_migration.{action_name}",
                raise_if_not_found=False,
            )
        ]
        return self._control_values(
            "reports", "reports", "Closing reports",
            "block" if missing else "pass", len(missing), 0.0,
            (
                f"{len(CANONICAL_CLOSING_REPORT_ACTIONS) - len(missing)} of "
                f"{len(CANONICAL_CLOSING_REPORT_ACTIONS)} required "
                "interactive report actions are available."
            ),
            (
                "Repair the missing canonical report actions before closing."
                if missing
                else "Open the required reports, review the period, and download the current PDF/XLSX evidence."
            ),
        )

    def _control_fec(self):
        fec_action = self.env.ref(
            "rebuild_account_migration.action_rebuild_account_report_export_fec",
            raise_if_not_found=False,
        )
        posted_moves = self.env["account.move"].search_count([
            ("company_id", "=", self.company_id.id),
            ("state", "=", "posted"),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
        ])
        available = bool(
            fec_action and "l10n_fr.fec.export.wizard" in self.env.registry
        )
        return self._control_values(
            "fec", "fec", "FEC readiness",
            "pass" if available else "block", 0 if available else 1, 0.0,
            (
                f"FEC export is available for {posted_moves} posted move(s) "
                "in this closing period."
                if available
                else "The normal FEC export action or export model is unavailable."
            ),
            (
                "Generate the FEC from Reporting, then retain it in the closing package."
                if available
                else "Restore the normal FEC export action before closing."
            ),
        )

    def _control_lock_dates(self):
        lock_fields = ["fiscalyear_lock_date", "tax_lock_date", "sale_lock_date", "purchase_lock_date"]
        missing = [name for name in lock_fields if not self.company_id[name] or self.company_id[name] < self.date_to]
        status = "pass" if not missing else "warning"
        return self._control_values(
            "lock_dates", "locks", "Lock-date readiness", status, len(missing), 0.0,
            "All standard accounting lock dates cover the period." if not missing else f"Lock dates not yet covering the close: {', '.join(missing)}.",
            "After all blockers and required approvals clear, use Close and Apply Standard Lock Dates.", owner="accounting_manager",
        )

    def action_prepare(self):
        self.action_refresh_controls()
        self.filtered(lambda closing: closing.state != "blocked").write({"state": "internal_review", "review_status": "internal_ready"})
        return True

    def action_request_accountant_review(self):
        self.action_refresh_controls()
        for closing in self:
            if closing.readiness_status == "blocked":
                message = (
                    "Resolve accounting blockers and technical control failures "
                    "before requesting accountant review."
                )
                raise UserError(message)
        self.write({"state": "accountant_review", "review_status": "accountant_requested"})
        return True

    def action_mark_ready_to_close(self):
        if not self.env.user.has_group("account.group_account_manager"):
            raise AccessError(
                "Only an Accounting Manager can approve a workspace as ready to close.",
            )
        self.action_refresh_controls()
        for closing in self:
            if closing.readiness_status == "blocked":
                raise UserError(
                    "Resolve accounting blockers and technical control failures "
                    "before approving this workspace as ready to close.",
                )
        self.write({"state": "ready", "review_status": "internal_ready"})
        return True

    def action_record_review_decision(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Closing Review Decision",
            "res_model": "rebuild.account.assurance.decision",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_gate": "closing_review",
                "default_conclusion": "pending",
                "default_required_authority": "accountant",
                "default_company_id": self.company_id.id,
                "default_period_key": f"{self.date_from}:{self.date_to}",
                "default_closing_period_id": self.id,
                "default_evidence_key": f"closing:{self.period_type}:{self.date_to}",
                "default_source_value": self.readiness_summary,
                "default_decision_summary": "Pending review.",
                "default_evidence_summary": self.accountant_information,
                "default_remaining_risk": self.actions_awaiting_valentin,
                "default_next_action": "Accept, accept with a documented difference, or require closing changes.",
            },
        }

    def action_close_and_apply_lock_dates(self):
        if not self.env.user.has_group("account.group_account_manager"):
            message = "Only an Accounting Manager can close a period and update lock dates."
            raise AccessError(message)
        for closing in self:
            closing.action_refresh_controls()
            if closing.readiness_status == "blocked":
                message = (
                    "The period cannot close while accounting blockers or "
                    "technical control failures remain."
                )
                raise UserError(message)
            if closing.review_status not in {"accepted", "accepted_with_difference"}:
                message = "A recorded closing review decision is required before lock dates can be applied."
                raise UserError(message)
            accepted_snapshots = closing.snapshot_ids.filtered(
                lambda snapshot: (
                    snapshot.review_decision_id.state == "recorded"
                    and snapshot.review_decision_id.conclusion in {
                        "accepted",
                        "accepted_with_difference",
                    }
                ),
            )
            if not accepted_snapshots:
                message = (
                    "Capture at least one immutable closing-package snapshot "
                    "for the current recorded acceptance before applying lock "
                    "dates."
                )
                raise UserError(message)
            previous = {
                key: fields.Date.to_string(closing.company_id[key]) if closing.company_id[key] else None
                for key in ["fiscalyear_lock_date", "tax_lock_date", "sale_lock_date", "purchase_lock_date", "hard_lock_date"]
            }
            lock_vals = {}
            for key in ["fiscalyear_lock_date", "tax_lock_date", "sale_lock_date", "purchase_lock_date"]:
                if not closing.company_id[key] or closing.company_id[key] < closing.date_to:
                    lock_vals[key] = closing.date_to
            if lock_vals:
                closing.company_id.write(lock_vals)
            final = {
                key: fields.Date.to_string(closing.company_id[key]) if closing.company_id[key] else None
                for key in ["fiscalyear_lock_date", "tax_lock_date", "sale_lock_date", "purchase_lock_date", "hard_lock_date"]
            }
            closing.write({
                "state": "closed",
                "readiness_status": "ready",
                "previous_lock_dates": json.dumps(previous, sort_keys=True),
                "final_lock_dates": json.dumps(final, sort_keys=True),
                "closed_at": fields.Datetime.now(),
                "closed_by_id": self.env.user.id,
            })
        return True

    def action_open_closing_package(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Closing Review Package",
            "res_model": "rebuild.account.report.export.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_report_type": "closing_package",
                "default_company_id": self.company_id.id,
                "default_date_from": fields.Date.to_string(self.date_from),
                "default_date_to": fields.Date.to_string(self.date_to),
                "default_export_format": "xlsx",
                "default_closing_period_id": self.id,
            },
        }

    def _capture_accepted_snapshots(self, decision):
        self.ensure_one()
        decision.ensure_one()
        if (
            decision.closing_period_id != self
            or decision.state != "recorded"
            or decision.conclusion not in {
                "accepted",
                "accepted_with_difference",
            }
        ):
            raise UserError(
                "Accepted snapshots require a recorded closing decision "
                "linked to this workspace."
            )
        if not self.package_attachment_ids:
            raise UserError(
                "Attach at least one generated closing package before "
                "recording an accepted closing decision."
            )
        Snapshot = self.env["rebuild.account.closing.snapshot"].sudo()
        snapshots = Snapshot.browse()
        for attachment in self.package_attachment_ids.sorted("id"):
            raw = bytes(attachment.raw or b"")
            if not raw:
                raise UserError(
                    f"Closing package attachment {attachment.name} has no "
                    "binary content and cannot be accepted as a snapshot."
                )
            sha256 = hashlib.sha256(raw).hexdigest()
            snapshot = Snapshot.search([
                ("closing_period_id", "=", self.id),
                ("review_decision_id", "=", decision.id),
                ("source_attachment_id", "=", attachment.id),
                ("sha256", "=", sha256),
            ], limit=1)
            if not snapshot:
                snapshot = Snapshot.create({
                    "closing_period_id": self.id,
                    "review_decision_id": decision.id,
                    "source_attachment_id": attachment.id,
                })
            snapshots |= snapshot
        return snapshots

    def action_capture_accepted_snapshots(self):
        self.ensure_one()
        if not self.env.user.has_group("account.group_account_manager"):
            raise AccessError(
                "Only an Accounting Manager can capture accepted closing "
                "snapshots."
            )
        decision = self.env["rebuild.account.assurance.decision"].search([
            ("closing_period_id", "=", self.id),
            ("gate", "=", "closing_review"),
            ("state", "=", "recorded"),
            (
                "conclusion",
                "in",
                ["accepted", "accepted_with_difference"],
            ),
        ], order="reviewed_at desc, id desc", limit=1)
        if not decision:
            raise UserError(
                "Record an accepted closing review decision before "
                "capturing snapshots."
            )
        self._capture_accepted_snapshots(decision)
        return self.action_open_snapshots()

    def action_open_snapshots(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Accepted Closing Snapshots",
            "res_model": "rebuild.account.closing.snapshot",
            "view_mode": "list,form",
            "domain": [("closing_period_id", "=", self.id)],
            "context": {"create": False, "delete": False},
        }

    def action_open_declarations(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Declarations for Fiscal Close",
            "res_model": "rebuild.account.declaration",
            "view_mode": "list,form,calendar",
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("fiscalyear_start", "=", self.fiscalyear_start),
                ("fiscalyear_end", "=", self.fiscalyear_end),
            ],
            "context": {"create": False, "delete": False},
        }


class RebuildAccountClosingSnapshot(models.Model):
    _name = "rebuild.account.closing.snapshot"
    _description = "Immutable Accepted Closing Snapshot"
    _order = "captured_at desc, id desc"

    _unique_snapshot = models.Constraint(
        "UNIQUE (closing_period_id, review_decision_id, "
        "source_attachment_id, sha256)",
        "This accepted closing snapshot already exists.",
    )

    closing_period_id = fields.Many2one(
        "rebuild.account.closing.period",
        required=True,
        index=True,
        ondelete="restrict",
    )
    company_id = fields.Many2one(
        related="closing_period_id.company_id",
        store=True,
        readonly=True,
        index=True,
    )
    review_decision_id = fields.Many2one(
        "rebuild.account.assurance.decision",
        required=True,
        index=True,
        ondelete="restrict",
    )
    source_attachment_id = fields.Many2one(
        "ir.attachment",
        required=True,
        index=True,
        ondelete="restrict",
    )
    name = fields.Char(required=True, readonly=True)
    mimetype = fields.Char(readonly=True)
    payload = fields.Binary(
        string="Accepted File",
        attachment=False,
        required=True,
        readonly=True,
    )
    sha256 = fields.Char(required=True, readonly=True, index=True)
    file_size = fields.Integer(required=True, readonly=True)
    package_reference = fields.Char(readonly=True)
    decision_conclusion = fields.Selection(
        [
            ("accepted", "Accepted"),
            ("accepted_with_difference", "Accepted with Difference"),
        ],
        required=True,
        readonly=True,
    )
    decision_summary = fields.Text(required=True, readonly=True)
    evidence_summary = fields.Text(readonly=True)
    reviewer_name = fields.Char(required=True, readonly=True)
    reviewed_at = fields.Datetime(required=True, readonly=True)
    captured_at = fields.Datetime(required=True, readonly=True)
    captured_by_id = fields.Many2one(
        "res.users",
        required=True,
        readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for vals in vals_list:
            closing = self.env["rebuild.account.closing.period"].browse(
                vals.get("closing_period_id"),
            ).exists()
            decision = self.env["rebuild.account.assurance.decision"].browse(
                vals.get("review_decision_id"),
            ).exists()
            attachment = self.env["ir.attachment"].browse(
                vals.get("source_attachment_id"),
            ).exists()
            if not closing or not decision or not attachment:
                raise UserError(
                    "A closing workspace, recorded decision and package "
                    "attachment are required."
                )
            if (
                decision.closing_period_id != closing
                or decision.state != "recorded"
                or decision.conclusion not in {
                    "accepted",
                    "accepted_with_difference",
                }
            ):
                raise UserError(
                    "The snapshot decision must be a recorded acceptance "
                    "linked to the same closing workspace."
                )
            if attachment not in closing.package_attachment_ids:
                raise UserError(
                    "Only an attachment in the closing package can be "
                    "captured as accepted evidence."
                )
            raw = bytes(attachment.raw or b"")
            if not raw:
                raise UserError(
                    "The accepted closing attachment must contain binary data."
                )
            prepared.append({
                **vals,
                "name": attachment.name,
                "mimetype": attachment.mimetype,
                "payload": BinaryBytes(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "file_size": len(raw),
                "package_reference": closing.package_reference,
                "decision_conclusion": decision.conclusion,
                "decision_summary": decision.decision_summary,
                "evidence_summary": decision.evidence_summary,
                "reviewer_name": (
                    decision.reviewer_name
                    or decision.reviewer_user_id.name
                    or self.env.user.name
                ),
                "reviewed_at": decision.reviewed_at or fields.Datetime.now(),
                "captured_at": fields.Datetime.now(),
                "captured_by_id": (
                    decision.reviewer_user_id.id or self.env.user.id
                ),
            })
        return super().create(prepared)

    def write(self, _vals):
        raise UserError(
            "Accepted closing snapshots are immutable. Create a new review "
            "decision and snapshot instead."
        )

    def unlink(self):
        raise UserError("Accepted closing snapshots cannot be deleted.")


class RebuildAccountClosingControlDefinition(models.Model):
    _name = "rebuild.account.closing.control.definition"
    _description = "Accounting Control Configuration"
    _inherit = ["rebuild.account.configurable.definition.mixin"]
    _order = "company_id, sequence, code"

    _unique_closing_control_definition = models.Constraint(
        "UNIQUE (company_id, code)",
        "A closing control can only be configured once per company.",
    )

    company_id = fields.Many2one(
        "res.company",
        required=True,
        index=True,
        default=lambda self: self.env.company,
    )
    sequence = fields.Integer(default=10)
    code = fields.Char(required=True, index=True, readonly=True)
    name = fields.Char(required=True, translate=True)
    category = fields.Selection(
        CLOSING_CONTROL_CATEGORIES,
        required=True,
        index=True,
    )
    description = fields.Text(
        required=True,
        help="What the control examines when a closing workspace is refreshed.",
    )
    accounting_consequence = fields.Text(
        required=True,
        help="Why an exception matters to the accounting review.",
    )
    owner = fields.Selection(
        CLOSING_CONTROL_OWNERS,
        required=True,
        default="accounting_manager",
        string="Responsible Role",
    )
    enabled = fields.Boolean(
        default=True,
        help=(
            "Enabled controls are calculated in every closing workspace for "
            "this company. Disabling a control removes it from the next "
            "refresh; it does not alter accounting records."
        ),
    )
    applies_to_hygiene = fields.Boolean(
        string="Accounting Hygiene",
        help="Run this control when Accounting Hygiene is refreshed.",
    )
    applies_to_closing = fields.Boolean(
        string="Closing Readiness",
        help="Run this control when a matching closing workspace is refreshed.",
    )
    closing_period_scope = fields.Selection(
        [
            ("all", "All Closing Periods"),
            ("month", "Month End Only"),
            ("quarter", "Quarter End Only"),
            ("annual", "Annual Close Only"),
        ],
        required=True,
        default="all",
        help="Limits closing execution without changing daily Hygiene execution.",
    )
    impact_policy = fields.Selection(
        CLOSING_CONTROL_IMPACT_POLICIES,
        required=True,
        default="evaluator",
        help=(
            "Dynamic preserves the installed evaluator's contextual result. "
            "The other choices consistently map every detected exception to "
            "information, a warning or a readiness blocker."
        ),
    )
    accountant_visible = fields.Boolean(
        string="Include in Accountant Summary",
        default=True,
        help=(
            "Include this control's summary in the accountant review text. "
            "The structured control and result remain inspectable."
        ),
    )
    expected_resolution = fields.Text(
        required=True,
        default=(
            "Open the current result, correct or document the underlying "
            "accounting condition, then refresh the control."
        ),
        help="Business-facing guidance shown to the person responsible for a failure.",
    )
    origin = fields.Selection(
        ACCOUNTING_DEFINITION_ORIGINS,
        required=True,
        default="usl",
        readonly=True,
    )
    source_module = fields.Char(required=True, default="rebuild_account_migration", readonly=True)
    evaluator_key = fields.Char(
        readonly=True,
        help="Stable key resolved through the installed evaluator registry.",
    )
    technical_model = fields.Char(readonly=True)
    technical_summary = fields.Text(
        readonly=True,
        help="Implementation boundary and important assumptions for technical review.",
    )
    closing_result_count = fields.Integer(compute="_compute_result_counts")
    hygiene_result_count = fields.Integer(compute="_compute_result_counts")

    @api.depends("company_id")
    def _compute_result_counts(self):
        ClosingResult = self.env["rebuild.account.closing.control"]
        HygieneResult = self.env["rebuild.account.hygiene.issue"]
        closing_groups = ClosingResult._read_group(
            [("definition_id", "in", self.ids)],
            ["definition_id"],
            ["__count"],
        ) if self.ids else []
        hygiene_groups = HygieneResult._read_group(
            [("definition_id", "in", self.ids)],
            ["definition_id"],
            ["__count"],
        ) if self.ids else []
        closing_counts = {definition.id: count for definition, count in closing_groups}
        hygiene_counts = {definition.id: count for definition, count in hygiene_groups}
        for definition in self:
            definition.closing_result_count = closing_counts.get(definition.id, 0)
            definition.hygiene_result_count = hygiene_counts.get(definition.id, 0)

    @api.constrains("applies_to_hygiene", "applies_to_closing")
    def _check_usage(self):
        for definition in self:
            if not definition.applies_to_hygiene and not definition.applies_to_closing:
                raise UserError(
                    "An Accounting Control must apply to Hygiene, Closing, or both.",
                )

    @api.model
    def _ensure_for_company(self, company):
        company.ensure_one()
        existing = {
            definition.code: definition
            for definition in self.with_context(active_test=False).search([
                ("company_id", "=", company.id),
            ])
        }
        for sequence, values in enumerate(CLOSING_CONTROL_DEFINITIONS, start=1):
            code, category, name, description, consequence, owner = values
            if code in existing:
                updates = {}
                if not existing[code].evaluator_key:
                    updates["evaluator_key"] = code
                if not existing[code].applies_to_closing:
                    updates["applies_to_closing"] = True
                if not existing[code].business_purpose:
                    updates["business_purpose"] = description
                if not existing[code].expected_outcome:
                    updates["expected_outcome"] = (
                        "The evaluator completes and any exception is resolved "
                        "or governed by the configured readiness policy."
                    )
                if updates:
                    existing[code].with_context(
                        accounting_control_seed=True,
                    ).write(updates)
                continue
            existing[code] = self.create({
                "company_id": company.id,
                "sequence": sequence * 10,
                "code": code,
                "evaluator_key": code,
                "category": category,
                "name": name,
                "description": description,
                "business_purpose": description,
                "accounting_consequence": consequence,
                "expected_outcome": (
                    "The evaluator completes and any exception is resolved or "
                    "governed according to the configured readiness policy."
                ),
                "owner": owner,
                "applies_to_closing": True,
                "expected_resolution": (
                    "Open the current closing result, resolve or document the "
                    "underlying condition, then refresh readiness."
                ),
                "technical_model": "rebuild.account.closing.period",
                "technical_summary": (
                    f"Python-backed closing evaluator registered as {code}. "
                    "It reads company-scoped accounting records for the "
                    "workspace dates and does not post or alter journal entries."
                ),
            })
        next_sequence = (len(CLOSING_CONTROL_DEFINITIONS) + 1) * 10
        for offset, values in enumerate(HYGIENE_CONTROL_DEFINITIONS):
            code, category, name, description, consequence, owner = values
            if code in existing:
                updates = {}
                if not existing[code].business_purpose:
                    updates["business_purpose"] = description
                if not existing[code].expected_outcome:
                    updates["expected_outcome"] = (
                        "The underlying accounting issue is corrected and the "
                        "next Hygiene refresh resolves the result naturally."
                    )
                if updates:
                    existing[code].with_context(
                        accounting_control_seed=True,
                    ).write(updates)
                continue
            existing[code] = self.create({
                "company_id": company.id,
                "sequence": next_sequence + offset * 10,
                "code": code,
                "evaluator_key": "builtin_hygiene",
                "category": category,
                "name": name,
                "description": description,
                "business_purpose": description,
                "accounting_consequence": consequence,
                "expected_outcome": (
                    "The underlying accounting issue is corrected and the "
                    "next Hygiene refresh resolves the result naturally."
                ),
                "owner": owner,
                "applies_to_hygiene": True,
                "expected_resolution": (
                    "Open the affected records, resolve or document the "
                    "underlying condition, then refresh Accounting Hygiene."
                ),
                "technical_model": "rebuild.account.hygiene.issue",
                "technical_summary": (
                    "Python-backed deterministic Hygiene evaluator. Results "
                    "retain first/last detection, resolution and source links."
                ),
            })
        return self.search([("company_id", "=", company.id)])

    def write(self, vals):
        vals = dict(vals)
        if "description" in vals and "business_purpose" not in vals:
            vals["business_purpose"] = vals["description"]
        if "expected_resolution" in vals and "expected_outcome" not in vals:
            vals["expected_outcome"] = vals["expected_resolution"]
        business_fields = {
            "enabled",
            "name",
            "category",
            "description",
            "accounting_consequence",
            "owner",
            "applies_to_hygiene",
            "applies_to_closing",
            "closing_period_scope",
            "impact_policy",
            "accountant_visible",
            "expected_resolution",
            "sequence",
            "definition_version",
            "lifecycle",
            "business_purpose",
            "expected_outcome",
            "effective_from",
            "effective_to",
        }
        if (
            business_fields & set(vals)
            and not self.env.context.get("accounting_control_seed")
        ):
            vals = {**vals, "origin": "company"}
        return super().write(vals)

    def _applies_to_period_type(self, period_type):
        self.ensure_one()
        return self.closing_period_scope in {"all", period_type}

    def _is_effective(self, on_date):
        self.ensure_one()
        on_date = fields.Date.to_date(on_date)
        return (
            self.lifecycle == "current"
            and (
                not self.effective_from
                or self.effective_from <= on_date
            )
            and (
                not self.effective_to
                or self.effective_to >= on_date
            )
        )

    def _apply_result_policy(self, values):
        self.ensure_one()
        status = values["status"]
        if (
            self.impact_policy != "evaluator"
            and status not in {"pass", "not_applicable", "technical_error"}
        ):
            status = {
                "informational": "info",
                "advisory": "warning",
                "blocking": "block",
            }[self.impact_policy]
        return {
            **values,
            "status": status,
            "next_action": values.get("next_action") or self.expected_resolution,
            "definition_version": self.definition_version,
            "definition_snapshot": self._definition_snapshot(),
        }

    def _apply_hygiene_policy(self, values):
        self.ensure_one()
        if self.impact_policy == "evaluator":
            severity = values["severity"]
        else:
            severity = {
                "informational": "4_information",
                "advisory": "2_warning",
                "blocking": "1_blocking",
            }[self.impact_policy]
        return {
            **values,
            "definition_id": self.id,
            "control_code": self.code,
            "severity": severity,
            "owner_role": self.owner,
            "definition_version": self.definition_version,
            "definition_snapshot": self._definition_snapshot(),
        }

    def action_refresh_open_workspaces(self):
        if not self.env.user.has_group("account.group_account_manager"):
            message = (
                "Only an Accounting Manager can refresh configured "
                "Accounting Controls."
            )
            raise AccessError(message)
        companies = self.company_id if self else self.env.companies
        closings = self.env["rebuild.account.closing.period"].search([
            ("company_id", "in", companies.ids),
            ("state", "not in", ["closed", "archived"]),
        ])
        closings.action_refresh_controls()
        for company in companies:
            self.env["rebuild.account.hygiene.issue"].sync_for_company(company)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Accounting controls refreshed",
                "message": (
                    f"Accounting Hygiene and {len(closings)} open closing "
                    "workspace(s) now use the current configuration."
                ),
                "type": "success",
                "sticky": False,
            },
        }

    def action_open_closing_results(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Closing Control Results",
            "res_model": "rebuild.account.closing.control",
            "view_mode": "list",
            "domain": [("definition_id", "in", self.ids)],
            "context": {"create": False, "delete": False},
        }

    def action_open_hygiene_results(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Accounting Hygiene Results",
            "res_model": "rebuild.account.hygiene.issue",
            "view_mode": "list,form",
            "domain": [("definition_id", "in", self.ids)],
            "context": {"create": False, "delete": False},
        }


class RebuildAccountClosingControl(models.Model):
    _name = "rebuild.account.closing.control"
    _description = "USL Accounting Closing Control"
    _order = "closing_period_id, sequence, code"

    _unique_closing_control = models.Constraint(
        "UNIQUE (closing_period_id, code)",
        "A closing control code must be unique within one workspace.",
    )

    closing_period_id = fields.Many2one("rebuild.account.closing.period", required=True, index=True, ondelete="cascade")
    definition_id = fields.Many2one(
        "rebuild.account.closing.control.definition",
        ondelete="restrict",
    )
    definition_version = fields.Char(readonly=True)
    definition_snapshot = fields.Json(readonly=True)
    company_id = fields.Many2one(related="closing_period_id.company_id", store=True, readonly=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)
    sequence = fields.Integer(default=10)
    code = fields.Char(required=True, index=True)
    category = fields.Selection(CLOSING_CONTROL_CATEGORIES, required=True, index=True)
    name = fields.Char(required=True)
    status = fields.Selection(
        [
            ("pass", "Passed"),
            ("info", "Information"),
            ("warning", "Warning"),
            ("block", "Blocking"),
            ("technical_error", "Technical Failure"),
            ("not_applicable", "Not Applicable"),
        ],
        required=True,
        index=True,
    )
    result_kind = fields.Selection(
        [
            ("accounting", "Accounting Result"),
            ("technical", "Technical Failure"),
        ],
        compute="_compute_result_kind",
        store=True,
        index=True,
    )
    record_count = fields.Integer()
    amount = fields.Monetary(currency_field="currency_id")
    summary = fields.Text(required=True)
    next_action = fields.Text(required=True)
    owner = fields.Selection(
        CLOSING_CONTROL_OWNERS,
        required=True,
        default="accounting_manager",
        string="Responsible Role",
    )
    accountant_visible = fields.Boolean(default=True)

    @api.depends("status")
    def _compute_result_kind(self):
        for control in self:
            control.result_kind = (
                "technical" if control.status == "technical_error" else "accounting"
            )

    @api.model
    def _upsert(self, closing, code, vals):
        control = self.search([("closing_period_id", "=", closing.id), ("code", "=", code)], limit=1)
        values = {"closing_period_id": closing.id, **vals}
        if control:
            control.write(values)
        else:
            control = self.create(values)
        return control

    def action_open_records(self):
        self.ensure_one()
        closing = self.closing_period_id
        if self.status == "technical_error" and self.definition_id:
            return {
                "type": "ir.actions.act_window",
                "name": "Accounting Control",
                "res_model": "rebuild.account.closing.control.definition",
                "res_id": self.definition_id.id,
                "view_mode": "form",
                "target": "current",
                "context": {"create": False, "delete": False},
            }
        if self.code == "accounting_completeness":
            return self._action("Draft Journal Entries", "account.move", [
                ("company_id", "=", self.company_id.id), ("date", ">=", closing.date_from),
                ("date", "<=", closing.date_to), ("state", "=", "draft"), ("move_type", "=", "entry"),
            ])
        if self.code == "document_completeness":
            return self._action("Closing Documents", "account.move", [
                ("company_id", "=", self.company_id.id), ("date", ">=", closing.date_from),
                ("date", "<=", closing.date_to),
                ("move_type", "in", ["out_invoice", "out_refund", "in_invoice", "in_refund"]),
            ])
        if self.code == "bank_reconciliation":
            return self._action("Bank Matching Items", "account.bank.statement.line", [
                ("company_id", "=", self.company_id.id), ("date", ">=", closing.date_from),
                ("date", "<=", closing.date_to), ("is_reconciled", "=", False),
            ], "kanban,list,form")
        if self.code == "unusual_balances":
            account_ids = [
                account.id
                for account, _balance, _line_count, _side
                in closing._unusual_balance_rows()
            ]
            action = self._action("Unusual Balance Journal Items", "account.move.line", [
                ("company_id", "=", self.company_id.id),
                ("move_id.state", "=", "posted"),
                ("date", "<=", closing.date_to),
                ("account_id", "in", account_ids),
                "|",
                (
                    "account_id.account_type",
                    "not in",
                    PROFIT_AND_LOSS_ACCOUNT_TYPES,
                ),
                ("date", ">=", closing.fiscalyear_start),
            ], "list,form,pivot")
            action["context"]["search_default_group_by_account"] = 1
            return action
        if self.code == "tax_declarations":
            return closing.action_open_declarations()
        if self.code == "issues":
            return self._action("Accounting Hygiene", "rebuild.account.hygiene.issue", [
                ("company_id", "=", self.company_id.id),
                ("status", "=", "open"),
                "|",
                ("issue_date", "=", False),
                ("issue_date", "<=", closing.date_to),
            ])
        if self.code == "reports":
            return self.env.ref(
                "rebuild_account_migration.action_rebuild_account_report_export_wizard"
            ).read()[0]
        return self._action("Closing Journal Items", "account.move.line", [
            ("company_id", "=", self.company_id.id), ("date", ">=", closing.date_from), ("date", "<=", closing.date_to),
        ], "list,form,pivot")

    @staticmethod
    def _action(name, model, domain, view_mode="list,form"):
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": model,
            "view_mode": view_mode,
            "domain": domain,
            "context": {"create": False, "delete": False},
        }
