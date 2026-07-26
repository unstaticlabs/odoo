from odoo import api, fields, models


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
    _inherit = "rebuild.account.review.summary"

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
    cash_projection_unresolved_count = fields.Integer(
        string="Unresolved open items",
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

    def _cash_position_lines(self):
        self.ensure_one()
        line_model = self.env["account.move.line"]
        receipt_lines = line_model.search(self._cash_projection_receipt_domain())
        payment_lines = line_model.search(self._cash_projection_payment_domain())
        unresolved_lines = line_model.search(
            self._cash_projection_base_domain(),
        ) - receipt_lines - payment_lines
        return receipt_lines, payment_lines, unresolved_lines

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
            receipt_lines, payment_lines, unresolved_lines = (
                summary._cash_position_lines()
            )
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
            unresolved_count = sum(
                not currency.is_zero(line.amount_residual)
                for line in unresolved_lines
            )
            summary.cash_on_banks = cash_on_banks
            summary.cash_position_journal_count = len(journals)
            summary.expected_receipt_amount = expected_receipts
            summary.expected_payment_amount = expected_payments
            summary.projected_cash_after_settlement = (
                cash_on_banks + expected_receipts - expected_payments
            )
            summary.cash_projection_unresolved_count = unresolved_count

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

    def action_open_cash_projection_unresolved(self):
        self.ensure_one()
        _receipts, _payments, unresolved_lines = self._cash_position_lines()
        return self._cash_projection_line_action(
            "Open items excluded from the cash estimate",
            [("id", "in", unresolved_lines.ids)],
        )
