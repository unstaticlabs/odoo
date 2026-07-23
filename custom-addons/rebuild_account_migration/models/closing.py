import calendar
import json

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import date_utils

PROFIT_AND_LOSS_ACCOUNT_TYPES = (
    "income",
    "income_other",
    "expense",
    "expense_other",
    "expense_depreciation",
    "expense_direct_cost",
)


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
            closing.passed_count = len(closing.control_line_ids.filtered(lambda line: line.status == "pass"))

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
        fiscal_start, fiscal_end = date_utils.get_fiscal_year(
            data_end,
            day=company.fiscalyear_last_day,
            month=int(company.fiscalyear_last_month),
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
            locked_start, locked_end = date_utils.get_fiscal_year(
                company.fiscalyear_lock_date,
                day=company.fiscalyear_last_day,
                month=int(company.fiscalyear_last_month),
            )
            if company.rebuild_first_fiscalyear_start and locked_end == company.fiscalyear_lock_date:
                locked_start = company.rebuild_first_fiscalyear_start
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
        for closing in self:
            closing._refresh_controls()
        return True

    def _refresh_controls(self):
        self.ensure_one()
        controls = [
            self._control_accounting_completeness(),
            self._control_document_completeness(),
            self._control_bank_reconciliation(),
            self._control_partner_open_items(),
            self._control_unusual_balances(),
            self._control_tax_declarations(),
            self._control_payroll(),
            self._control_assets_deferrals(),
            self._control_currency(),
            self._control_analytic(),
            self._control_issues(),
            self._control_reports(),
            self._control_fec(),
            self._control_lock_dates(),
        ]
        seen = set()
        Control = self.env["rebuild.account.closing.control"]
        for values in controls:
            seen.add(values["code"])
            Control._upsert(self, values["code"], values)
        self.control_line_ids.filtered(lambda line: line.code not in seen).unlink()
        blocking = [control for control in controls if control["status"] == "block"]
        warnings = [control for control in controls if control["status"] == "warning"]
        readiness = "blocked" if blocking else "warning" if warnings else "ready"
        actions = [control["next_action"] for control in blocking + warnings if control.get("owner") == "valentin"]
        accountant = [control["summary"] for control in controls if control.get("accountant_visible")]
        vals = {
            "readiness_status": readiness,
            "readiness_summary": (
                f"{len(blocking)} blocking control(s), {len(warnings)} warning(s), "
                f"{len(controls) - len(blocking) - len(warnings)} passed/not-applicable control(s)."
            ),
            "actions_awaiting_valentin": "\n".join(actions),
            "accountant_information": "\n".join(accountant),
            "last_refreshed_at": fields.Datetime.now(),
        }
        if self.state in {"open", "preparing", "blocked", "internal_review"}:
            vals["state"] = "blocked" if blocking else "internal_review"
        self.write(vals)

    def _control_values(self, code, category, name, status, count, amount, summary, next_action, owner="valentin", accountant_visible=True):
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
            f"{drafts} draft business document(s); {missing_evidence} posted document(s) without a main attachment.",
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
            "Review ageing and document legitimate open customer and supplier balances.", owner="operator",
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
            owner="operator",
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
                "Confirm the external payroll and social-declaration package for the period.", owner="operator",
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
        asset_gaps = self.env["rebuild.account.asset.depreciation.schedule.line"].search_count([
            ("company_id", "=", self.company_id.id),
            ("depreciation_date", ">=", self.date_from),
            ("depreciation_date", "<=", self.date_to),
            ("representation_status", "!=", "imported_posted_entry"),
        ])
        deferral_gaps = self.env["rebuild.account.deferred.schedule.line"].search_count([
            ("company_id", "=", self.company_id.id),
            ("deferred_date", ">=", self.date_from),
            ("deferred_date", "<=", self.date_to),
            ("review_status", "=", "review_required"),
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
            "Review currency exposure and record any required period-end revaluation outside the immutable source replay.", owner="operator",
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
            "Confirm whether unallocated lines need analytic coding or a documented exclusion.", owner="operator",
        )

    def _control_issues(self):
        issues = self.env["rebuild.account.discrepancy"].search([
            ("company_id", "=", self.company_id.id),
            ("status", "in", ["open", "investigating"]),
        ])
        high = issues.filtered(lambda issue: issue.severity in {"P0", "P1"})
        return self._control_values(
            "issues", "issues", "Unresolved accounting issues", "block" if high else "warning" if issues else "pass",
            len(issues), 0.0,
            f"{len(high)} P0/P1 and {len(issues - high)} lower-priority unresolved discrepancy record(s).",
            "Resolve or record an authorized evidence-backed decision for every close-impacting discrepancy.",
        )

    def _control_reports(self):
        reports = self.env["rebuild.account.source.report"].search([
            ("active", "=", True),
            ("parity_level", "!=", "level_4_accepted"),
        ])
        status = "block" if self.period_type == "annual" and reports else "warning" if reports else "pass"
        return self._control_values(
            "reports", "reports", "Report readiness", status, len(reports), 0.0,
            f"{len(reports)} active source report(s) have technical evidence but no recorded professional acceptance.",
            "Review report packages and record accountant-authorized parity decisions without fabricating acceptance.",
        )

    def _control_fec(self):
        accepted = self.env["rebuild.account.review.decision"].search_count([
            ("company_id", "=", self.company_id.id),
            ("gate", "=", "fec_validation"),
            ("period_key", "=", f"{self.date_from}:{self.date_to}"),
            ("state", "=", "recorded"),
            ("conclusion", "in", ["accepted", "accepted_with_difference"]),
        ])
        status = "pass" if accepted else "block" if self.period_type == "annual" else "warning"
        return self._control_values(
            "fec", "fec", "FEC readiness", status, 0 if accepted else 1, 0.0,
            "A recorded FEC review decision exists." if accepted else "Technical FEC evidence exists outside this workspace, but no authorized FEC acceptance decision is recorded.",
            "Generate and validate the FEC, retain its hash/log, and record the authorized review decision.",
        )

    def _control_lock_dates(self):
        lock_fields = ["fiscalyear_lock_date", "tax_lock_date", "sale_lock_date", "purchase_lock_date"]
        missing = [name for name in lock_fields if not self.company_id[name] or self.company_id[name] < self.date_to]
        status = "pass" if not missing else "warning"
        return self._control_values(
            "lock_dates", "locks", "Lock-date readiness", status, len(missing), 0.0,
            "All standard accounting lock dates cover the period." if not missing else f"Lock dates not yet covering the close: {', '.join(missing)}.",
            "After all blockers and required approvals clear, use Close and Apply Standard Lock Dates.", owner="valentin",
        )

    def action_prepare(self):
        self.action_refresh_controls()
        self.filtered(lambda closing: closing.state != "blocked").write({"state": "internal_review", "review_status": "internal_ready"})
        return True

    def action_request_accountant_review(self):
        self.action_refresh_controls()
        for closing in self:
            if closing.blocking_count:
                message = "Resolve all blocking closing controls before requesting accountant review."
                raise UserError(message)
        self.write({"state": "accountant_review", "review_status": "accountant_requested"})
        return True

    def action_record_review_decision(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Closing Review Decision",
            "res_model": "rebuild.account.review.decision",
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
            if closing.blocking_count:
                message = "The period cannot close while blocking controls remain."
                raise UserError(message)
            if closing.review_status not in {"accepted", "accepted_with_difference"}:
                message = "A recorded closing review decision is required before lock dates can be applied."
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
            },
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


class RebuildAccountClosingControl(models.Model):
    _name = "rebuild.account.closing.control"
    _description = "USL Accounting Closing Control"
    _order = "closing_period_id, sequence, code"

    _unique_closing_control = models.Constraint(
        "UNIQUE (closing_period_id, code)",
        "A closing control code must be unique within one workspace.",
    )

    closing_period_id = fields.Many2one("rebuild.account.closing.period", required=True, index=True, ondelete="cascade")
    company_id = fields.Many2one(related="closing_period_id.company_id", store=True, readonly=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)
    sequence = fields.Integer(default=10)
    code = fields.Char(required=True, index=True)
    category = fields.Selection(
        [
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
        ],
        required=True,
        index=True,
    )
    name = fields.Char(required=True)
    status = fields.Selection(
        [("pass", "Passed"), ("warning", "Warning"), ("block", "Blocking"), ("not_applicable", "Not Applicable")],
        required=True,
        index=True,
    )
    record_count = fields.Integer()
    amount = fields.Monetary(currency_field="currency_id")
    summary = fields.Text(required=True)
    next_action = fields.Text(required=True)
    owner = fields.Selection(
        [("valentin", "Valentin"), ("accountant", "Prosper / Accountant"), ("operator", "Finance Operator / Agent")],
        required=True,
        default="valentin",
    )
    accountant_visible = fields.Boolean(default=True)

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
            return self._action("Accounting Issues", "rebuild.account.discrepancy", [
                ("company_id", "=", self.company_id.id), ("status", "in", ["open", "investigating"]),
            ])
        if self.code == "reports":
            return self._action("Report Review", "rebuild.account.source.report", [
                ("active", "=", True),
                ("parity_level", "!=", "level_4_accepted"),
            ])
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
