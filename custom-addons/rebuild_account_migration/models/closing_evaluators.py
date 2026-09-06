"""Built-in closing control evaluators dispatched by the control registry."""

from odoo import models

from .closing import (
    CANONICAL_CLOSING_REPORT_ACTIONS,
    PROFIT_AND_LOSS_ACCOUNT_TYPES,
)


class RebuildAccountClosingPeriod(models.Model):
    _inherit = "rebuild.account.closing.period"

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
            fec_action and "l10n_fr.fec.export.wizard" in self.env.registry,
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
