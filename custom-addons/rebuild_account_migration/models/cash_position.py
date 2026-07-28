from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.tools import float_round

PROFIT_AND_LOSS_ACCOUNT_TYPES = (
    "income",
    "income_other",
    "expense",
    "expense_other",
    "expense_depreciation",
    "expense_direct_cost",
)
FRENCH_SME_REDUCED_RATE = 0.15
FRENCH_STANDARD_CORPORATE_TAX_RATE = 0.25
FRENCH_SME_REDUCED_RATE_CEILING = 42_500.0


class AccountJournal(models.Model):
    _inherit = "account.journal"

    rebuild_cash_position_included = fields.Boolean(
        string="Include in Cash Position",
        default=True,
        help=(
            "Include this bank or payment account in Cash on banks. Disable it "
            "for restricted balances or accounts that do not represent cash "
            "available to the company."
        ),
    )


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    rebuild_cash_projection_amount = fields.Monetary(
        string="Projected cash movement",
        currency_field="company_currency_id",
        compute="_compute_rebuild_cash_projection_amount",
    )

    @api.depends("amount_residual")
    def _compute_rebuild_cash_projection_amount(self):
        for line in self:
            line.rebuild_cash_projection_amount = abs(line.amount_residual)


class RebuildAccountReviewSummary(models.Model):
    _inherit = "rebuild.account.overview"

    cash_on_banks = fields.Monetary(
        string="Cash on banks",
        currency_field="currency_id",
        compute="_compute_cash_position",
    )
    cash_position_journal_count = fields.Integer(
        string="Included bank accounts",
        compute="_compute_cash_position",
    )
    projected_cash_after_settlement = fields.Monetary(
        string="Projected cash after settlement",
        currency_field="currency_id",
        compute="_compute_cash_position",
    )
    projected_cash_after_taxes = fields.Monetary(
        string="Projected cash after taxes",
        currency_field="currency_id",
        compute="_compute_cash_position",
    )
    expected_receipt_amount = fields.Monetary(
        string="Expected receipts",
        currency_field="currency_id",
        compute="_compute_cash_position",
    )
    expected_payment_amount = fields.Monetary(
        string="Expected payments",
        currency_field="currency_id",
        compute="_compute_cash_position",
    )
    cash_projection_reconciliation_amount = fields.Monetary(
        string="Open reconciliation balance",
        currency_field="currency_id",
        compute="_compute_cash_position",
    )
    cash_projection_reconciliation_item_count = fields.Integer(
        string="Open reconciliation items",
        compute="_compute_cash_position",
    )
    cash_projection_reconciliation_account_count = fields.Integer(
        string="Open reconciliation accounts",
        compute="_compute_cash_position",
    )
    cash_projection_unpaid_expense_amount = fields.Monetary(
        string="Unpaid expenses",
        currency_field="currency_id",
        compute="_compute_cash_position",
    )
    cash_projection_unpaid_expense_count = fields.Integer(
        string="Unpaid expense records",
        compute="_compute_cash_position",
    )
    cash_projection_draft_expense_amount = fields.Monetary(
        string="Draft expenses",
        currency_field="currency_id",
        compute="_compute_cash_position",
    )
    cash_projection_submitted_expense_amount = fields.Monetary(
        string="Submitted expenses",
        currency_field="currency_id",
        compute="_compute_cash_position",
    )
    cash_projection_approved_expense_amount = fields.Monetary(
        string="Approved expenses",
        currency_field="currency_id",
        compute="_compute_cash_position",
    )
    cash_projection_tax_estimate_enabled = fields.Boolean(
        string="Corporate-tax estimate enabled",
        compute="_compute_cash_position",
    )
    cash_projection_fiscalyear_start = fields.Date(
        string="Fiscal-year start",
        compute="_compute_cash_position",
    )
    cash_projection_fiscalyear_end = fields.Date(
        string="Fiscal-year end",
        compute="_compute_cash_position",
    )
    cash_projection_ytd_profit_before_tax = fields.Monetary(
        string="YTD profit before IS",
        currency_field="currency_id",
        compute="_compute_cash_position",
    )
    cash_projection_tax_reduced_rate_base = fields.Monetary(
        string="Estimated 15% tax base",
        currency_field="currency_id",
        compute="_compute_cash_position",
    )
    cash_projection_tax_standard_rate_base = fields.Monetary(
        string="Estimated 25% tax base",
        currency_field="currency_id",
        compute="_compute_cash_position",
    )
    cash_projection_estimated_corporate_tax = fields.Monetary(
        string="Estimated YTD corporate tax",
        currency_field="currency_id",
        compute="_compute_cash_position",
    )
    cash_projection_tax_prepayment_amount = fields.Monetary(
        string="IS instalments already paid",
        currency_field="currency_id",
        compute="_compute_cash_position",
    )
    cash_projection_tax_recognized_liability_amount = fields.Monetary(
        string="IS liability already in settlement",
        currency_field="currency_id",
        compute="_compute_cash_position",
    )
    cash_projection_corporate_tax_reserve = fields.Monetary(
        string="Additional corporate-tax reserve",
        currency_field="currency_id",
        compute="_compute_cash_position",
    )

    def _cash_position_journals(self):
        self.ensure_one()
        return self.env["account.journal"].search([
            ("company_id", "=", self.company_id.id),
            ("active", "=", True),
            ("type", "in", ("bank", "cash")),
            ("rebuild_cash_position_included", "=", True),
            ("default_account_id", "!=", False),
        ])

    def _cash_projection_base_domain(self):
        self.ensure_one()
        return [
            ("company_id", "=", self.company_id.id),
            ("parent_state", "=", "posted"),
            ("date", "<=", fields.Date.context_today(self)),
            (
                "account_id.account_type",
                "in",
                ("asset_receivable", "liability_payable"),
            ),
            ("reconciled", "=", False),
            ("amount_residual", "!=", 0),
        ]

    def _cash_projection_receipt_domain(self):
        return [
            *self._cash_projection_base_domain(),
            (
                "move_id.move_type",
                "in",
                ("out_invoice", "out_receipt", "in_refund"),
            ),
        ]

    def _cash_projection_payment_domain(self):
        self.ensure_one()
        return [
            *self._cash_projection_base_domain(),
            "|",
            (
                "move_id.move_type",
                "in",
                ("in_invoice", "in_receipt", "out_refund"),
            ),
            ("move_id.expense_ids.payment_mode", "=", "own_account"),
        ]

    def _cash_projection_reconciliation_domain(self):
        self.ensure_one()
        return [
            ("company_id", "=", self.company_id.id),
            ("parent_state", "=", "posted"),
            ("date", "<=", fields.Date.context_today(self)),
            ("account_id.reconcile", "=", True),
            ("reconciled", "=", False),
            ("amount_residual", "!=", 0),
        ]

    def _cash_projection_unpaid_expense_domain(self):
        self.ensure_one()
        return [
            ("company_id", "=", self.company_id.id),
            ("state", "in", ("draft", "submitted", "approved")),
            ("payment_mode", "=", "own_account"),
            ("account_move_id", "=", False),
        ]

    def _cash_projection_tax_base_domain(self):
        self.ensure_one()
        today = fields.Date.context_today(self)
        fiscal_year = self.company_id.compute_fiscalyear_dates(today)
        corporate_tax_expense_accounts = self.env[
            "account.account"
        ].with_company(self.company_id).search([
            ("company_ids", "in", self.company_id.id),
            ("code", "=like", "695%"),
        ])
        return [
            ("company_id", "=", self.company_id.id),
            ("parent_state", "=", "posted"),
            ("date", ">=", fiscal_year["date_from"]),
            ("date", "<=", today),
            (
                "account_id.account_type",
                "in",
                PROFIT_AND_LOSS_ACCOUNT_TYPES,
            ),
            ("account_id", "not in", corporate_tax_expense_accounts.ids),
        ]

    def _cash_projection_tax_account_domain(self):
        self.ensure_one()
        corporate_tax_accounts = self.env[
            "account.account"
        ].with_company(self.company_id).search([
            ("company_ids", "in", self.company_id.id),
            ("code", "=like", "444%"),
        ])
        return [
            *self._cash_projection_reconciliation_domain(),
            ("account_id", "in", corporate_tax_accounts.ids),
        ]

    @staticmethod
    def _cash_projection_reduced_rate_ceiling(fiscal_year):
        """Prorate the 12-month French SME ceiling for irregular years."""
        duration = relativedelta(
            fields.Date.add(fiscal_year["date_to"], days=1),
            fiscal_year["date_from"],
        )
        month_count = (
            duration.years * 12
            + duration.months
            + duration.days / 30
        )
        return (
            FRENCH_SME_REDUCED_RATE_CEILING
            * month_count
            / 12
        )

    def _cash_tax_projection_data(self):
        self.ensure_one()
        company = self.company_id
        today = fields.Date.context_today(self)
        fiscal_year = company.compute_fiscalyear_dates(today)
        fiscal_country = (
            company.account_fiscal_country_id
            or company.country_id
        )
        profile = company.rebuild_corporate_tax_projection_profile
        enabled = bool(
            company.rebuild_declaration_profile_active
            and company.rebuild_corporate_tax_regime == "is"
            and fiscal_country.code == "FR"
            and profile != "disabled",
        )
        tax_base_lines = self.env["account.move.line"]
        tax_account_lines = self.env["account.move.line"]
        if enabled:
            tax_base_lines = tax_base_lines.search(
                self._cash_projection_tax_base_domain(),
            )
            tax_account_lines = tax_account_lines.search(
                self._cash_projection_tax_account_domain(),
            )

        profit_before_tax = float_round(
            max(-sum(tax_base_lines.mapped("balance")), 0.0),
            precision_digits=0,
        )
        reduced_rate_base = 0.0
        if enabled and profile == "fr_sme_15_25":
            reduced_rate_base = min(
                profit_before_tax,
                float_round(
                    self._cash_projection_reduced_rate_ceiling(
                        fiscal_year,
                    ),
                    precision_digits=0,
                ),
            )
        standard_rate_base = max(
            profit_before_tax - reduced_rate_base,
            0.0,
        )
        estimated_tax = float_round(
            (
                reduced_rate_base * FRENCH_SME_REDUCED_RATE
                + standard_rate_base
                * FRENCH_STANDARD_CORPORATE_TAX_RATE
            ),
            precision_digits=0,
        )
        prepayments = sum(
            max(line.amount_residual, 0.0)
            for line in tax_account_lines
        )
        recognized_liability = abs(sum(
            min(line.amount_residual, 0.0)
            for line in tax_account_lines
        ))
        additional_reserve = max(
            estimated_tax - recognized_liability,
            0.0,
        )
        return {
            "enabled": enabled,
            "fiscal_year": fiscal_year,
            "tax_base_lines": tax_base_lines,
            "tax_account_lines": tax_account_lines,
            "profit_before_tax": profit_before_tax,
            "reduced_rate_base": reduced_rate_base,
            "standard_rate_base": standard_rate_base,
            "estimated_tax": estimated_tax,
            "prepayments": prepayments,
            "recognized_liability": recognized_liability,
            "additional_reserve": additional_reserve,
        }

    def _cash_position_lines(self):
        self.ensure_one()
        line_model = self.env["account.move.line"]
        receipt_lines = line_model.search(self._cash_projection_receipt_domain())
        payment_lines = line_model.search(self._cash_projection_payment_domain())
        reconciliation_lines = line_model.search(
            self._cash_projection_reconciliation_domain(),
        )
        unpaid_expenses = self.env["hr.expense"].search(
            self._cash_projection_unpaid_expense_domain(),
        )
        return (
            receipt_lines,
            payment_lines,
            reconciliation_lines,
            unpaid_expenses,
        )

    @api.depends_context("allowed_company_ids", "company", "tz")
    def _compute_cash_position(self):
        for summary in self:
            company = summary.company_id
            currency = company.currency_id
            journals = summary._cash_position_journals()
            cash_lines = self.env["account.move.line"].search([
                ("company_id", "=", company.id),
                ("parent_state", "=", "posted"),
                ("date", "<=", fields.Date.context_today(summary)),
                ("account_id", "in", journals.default_account_id.ids),
            ])
            (
                receipt_lines,
                payment_lines,
                reconciliation_lines,
                unpaid_expenses,
            ) = summary._cash_position_lines()
            cash_on_banks = sum(cash_lines.mapped("balance"))
            expected_receipts = sum(
                abs(line.amount_residual)
                for line in receipt_lines
                if not currency.is_zero(line.amount_residual)
            )
            expected_payments = sum(
                abs(line.amount_residual)
                for line in payment_lines
                if not currency.is_zero(line.amount_residual)
            )
            reconciliation_lines = reconciliation_lines.filtered(
                lambda line: not currency.is_zero(line.amount_residual),
            )
            reconciliation_amount = sum(
                reconciliation_lines.mapped("amount_residual"),
            )
            unpaid_expense_amount = sum(
                unpaid_expenses.mapped("total_amount"),
            )
            expense_amounts_by_state = {
                state: sum(
                    unpaid_expenses.filtered(
                        lambda expense, state=state: expense.state == state,
                    ).mapped("total_amount"),
                )
                for state in ("draft", "submitted", "approved")
            }
            projected_after_settlement = (
                cash_on_banks
                + reconciliation_amount
                - unpaid_expense_amount
            )
            tax_projection = summary._cash_tax_projection_data()
            summary.cash_on_banks = cash_on_banks
            summary.cash_position_journal_count = len(journals)
            summary.expected_receipt_amount = expected_receipts
            summary.expected_payment_amount = expected_payments
            summary.projected_cash_after_settlement = (
                projected_after_settlement
            )
            summary.projected_cash_after_taxes = (
                projected_after_settlement
                - tax_projection["additional_reserve"]
            )
            summary.cash_projection_reconciliation_amount = (
                reconciliation_amount
            )
            summary.cash_projection_reconciliation_item_count = len(
                reconciliation_lines,
            )
            summary.cash_projection_reconciliation_account_count = len(
                reconciliation_lines.account_id,
            )
            summary.cash_projection_unpaid_expense_amount = (
                unpaid_expense_amount
            )
            summary.cash_projection_unpaid_expense_count = len(
                unpaid_expenses,
            )
            summary.cash_projection_draft_expense_amount = (
                expense_amounts_by_state["draft"]
            )
            summary.cash_projection_submitted_expense_amount = (
                expense_amounts_by_state["submitted"]
            )
            summary.cash_projection_approved_expense_amount = (
                expense_amounts_by_state["approved"]
            )
            summary.cash_projection_tax_estimate_enabled = (
                tax_projection["enabled"]
            )
            summary.cash_projection_fiscalyear_start = (
                tax_projection["fiscal_year"]["date_from"]
            )
            summary.cash_projection_fiscalyear_end = (
                tax_projection["fiscal_year"]["date_to"]
            )
            summary.cash_projection_ytd_profit_before_tax = (
                tax_projection["profit_before_tax"]
            )
            summary.cash_projection_tax_reduced_rate_base = (
                tax_projection["reduced_rate_base"]
            )
            summary.cash_projection_tax_standard_rate_base = (
                tax_projection["standard_rate_base"]
            )
            summary.cash_projection_estimated_corporate_tax = (
                tax_projection["estimated_tax"]
            )
            summary.cash_projection_tax_prepayment_amount = (
                tax_projection["prepayments"]
            )
            summary.cash_projection_tax_recognized_liability_amount = (
                tax_projection["recognized_liability"]
            )
            summary.cash_projection_corporate_tax_reserve = (
                tax_projection["additional_reserve"]
            )

    def action_open_cash_position_journals(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "account.open_account_journal_dashboard_kanban",
        )
        action["name"] = "Cash on banks"
        action["domain"] = [
            ("id", "in", self._cash_position_journals().ids),
        ]
        return action

    def action_open_projected_cash_accounts(self):
        self.ensure_one()
        _receipts, _payments, reconciliation_lines, _expenses = (
            self._cash_position_lines()
        )
        accounts = (
            self._cash_position_journals().default_account_id
            | reconciliation_lines.account_id
        )
        action = self.env["ir.actions.actions"]._for_xml_id(
            "account.action_account_form",
        )
        action["name"] = "Projected cash accounts"
        action["domain"] = [("id", "in", accounts.ids)]
        return action

    def action_open_projected_cash_after_taxes(self):
        self.ensure_one()
        fiscal_year = self._cash_tax_projection_data()["fiscal_year"]
        action = self.env["ir.actions.actions"]._for_xml_id(
            "rebuild_account_migration.action_rebuild_account_declaration",
        )
        action["name"] = "Corporate Tax Declarations"
        action["domain"] = [
            ("company_id", "=", self.company_id.id),
            ("fiscalyear_start", "=", fiscal_year["date_from"]),
            ("fiscalyear_end", "=", fiscal_year["date_to"]),
            ("rule_id.code", "in", ("FR_2571", "FR_2572", "FR_2065")),
        ]
        action["context"] = {
            "create": False,
            "delete": False,
        }
        return action

    def _cash_projection_line_action(self, name, domain):
        self.ensure_one()
        list_view = self.env.ref(
            "rebuild_account_migration.view_rebuild_cash_projection_line_list",
        )
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": "account.move.line",
            "view_mode": "list,pivot,graph",
            "views": [
                (list_view.id, "list"),
                (False, "pivot"),
                (False, "graph"),
            ],
            "domain": domain,
            "context": {
                "create": False,
                "delete": False,
            },
        }

    def action_open_expected_receipts(self):
        return self._cash_projection_line_action(
            "Expected receipts",
            self._cash_projection_receipt_domain(),
        )

    def action_open_expected_payments(self):
        return self._cash_projection_line_action(
            "Expected payments",
            self._cash_projection_payment_domain(),
        )

    def action_open_cash_projection_reconciliation(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "rebuild_account_migration."
            "action_rebuild_account_reconciliation_overview",
        )
        action.update({
            "name": "Open balances in cash projection",
            "domain": self._cash_projection_reconciliation_domain(),
            "context": {
                "search_default_unreconciled": 1,
                "search_default_group_by_account": 1,
                "create": False,
                "delete": False,
            },
        })
        return action

    def action_open_cash_projection_tax_base(self):
        self.ensure_one()
        list_view = self.env.ref(
            "rebuild_account_migration."
            "view_rebuild_cash_tax_base_line_list",
        )
        return {
            "type": "ir.actions.act_window",
            "name": "YTD profit before corporate tax",
            "res_model": "account.move.line",
            "view_mode": "list,pivot,graph",
            "views": [
                (list_view.id, "list"),
                (False, "pivot"),
                (False, "graph"),
            ],
            "domain": self._cash_projection_tax_base_domain(),
            "context": {
                "create": False,
                "delete": False,
                "search_default_group_by_account": 1,
            },
        }

    def action_open_cash_projection_tax_items(self):
        return self._cash_projection_line_action(
            "Corporate tax instalments and liabilities",
            self._cash_projection_tax_account_domain(),
        )

    def action_open_cash_projection_unpaid_expenses(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "hr_expense.action_hr_expense_account",
        )
        action.update({
            "name": "Unpaid expenses in cash projection",
            "domain": self._cash_projection_unpaid_expense_domain(),
            "context": {
                "create": False,
                "delete": False,
            },
        })
        return action
