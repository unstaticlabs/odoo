from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare


class UslPlatformBillingBankAllocation(models.Model):
    _name = "usl.platform.billing.bank.allocation"
    _description = "Platform Payout Bank Allocation"
    _inherit = ["mail.thread"]
    _order = "bank_date desc, id desc"
    _check_company_auto = True

    payout_id = fields.Many2one(
        "usl.platform.billing.payout",
        required=True,
        check_company=True,
        index=True,
        ondelete="cascade",
    )
    session_id = fields.Many2one(
        related="payout_id.session_id",
        store=True,
        index=True,
    )
    company_id = fields.Many2one(
        related="payout_id.company_id",
        store=True,
        index=True,
    )
    platform_id = fields.Many2one(
        related="payout_id.platform_id",
        store=True,
        index=True,
    )
    bank_statement_line_id = fields.Many2one(
        "account.bank.statement.line",
        string="Bank Transaction",
        required=True,
        check_company=True,
        index=True,
        ondelete="restrict",
    )
    bank_currency_id = fields.Many2one(
        related="bank_statement_line_id.currency_id",
        store=True,
    )
    payout_currency_id = fields.Many2one(
        related="payout_id.platform_currency_id",
        store=True,
    )
    bank_amount = fields.Monetary(
        string="Allocated Bank Amount",
        currency_field="bank_currency_id",
        required=True,
    )
    payout_amount = fields.Monetary(
        string="Settled Payout Amount",
        currency_field="payout_currency_id",
        required=True,
        help=(
            "Part of the platform payout settled by this bank transaction. "
            "It may differ from the bank amount when currencies differ."
        ),
    )
    bank_date = fields.Date(
        related="bank_statement_line_id.date",
        store=True,
    )
    bank_journal_id = fields.Many2one(
        related="bank_statement_line_id.journal_id",
        store=True,
    )
    bank_label = fields.Char(
        related="bank_statement_line_id.payment_ref",
        store=True,
    )
    score = fields.Integer(readonly=True, copy=False)
    amount_difference = fields.Monetary(
        currency_field="bank_currency_id",
        readonly=True,
        copy=False,
    )
    date_difference = fields.Integer(readonly=True, copy=False)
    detection_reason = fields.Text(readonly=True, copy=False)
    blocked_reason = fields.Text(readonly=True, copy=False, tracking=True)
    state = fields.Selection(
        [
            ("linked", "Linked"),
            ("reconciled", "Reconciled"),
            ("blocked", "Blocked"),
        ],
        compute="_compute_state",
        store=True,
    )

    _payout_bank_line_unique = models.Constraint(
        "UNIQUE(payout_id, bank_statement_line_id)",
        "A bank transaction can be allocated to a payout only once.",
    )

    @api.depends("bank_statement_line_id.is_reconciled", "blocked_reason")
    def _compute_state(self):
        for allocation in self:
            if allocation.bank_statement_line_id.is_reconciled:
                allocation.state = "reconciled"
            elif allocation.blocked_reason:
                allocation.state = "blocked"
            else:
                allocation.state = "linked"

    @api.constrains(
        "payout_id",
        "bank_statement_line_id",
        "bank_amount",
        "payout_amount",
    )
    def _check_allocation(self):
        bank_line_ids = self.bank_statement_line_id.ids
        payout_ids = self.payout_id.ids
        if bank_line_ids:
            self.env.cr.execute(
                """
                SELECT id
                  FROM account_bank_statement_line
                 WHERE id IN %s
                 ORDER BY id
                   FOR UPDATE
                """,
                [tuple(bank_line_ids)],
            )
        if payout_ids:
            self.env.cr.execute(
                """
                SELECT id
                  FROM usl_platform_billing_payout
                 WHERE id IN %s
                 ORDER BY id
                   FOR UPDATE
                """,
                [tuple(payout_ids)],
            )
        for allocation in self:
            payout = allocation.payout_id
            bank_line = allocation.bank_statement_line_id
            if bank_line.company_id != payout.company_id:
                raise ValidationError(
                    _("The bank transaction and payout must belong to the same company."),
                )
            if bank_line.journal_id.type != "bank" or bank_line.amount <= 0:
                raise ValidationError(
                    _("Only an incoming transaction from a bank journal can be allocated."),
                )
            if bank_line.currency_id != payout.bank_currency_id:
                raise ValidationError(
                    _("The bank transaction must use the session bank currency."),
                )
            if allocation.bank_amount <= 0 or allocation.payout_amount < 0:
                raise ValidationError(
                    _("The bank amount must be positive and the payout amount cannot be negative."),
                )
            if allocation.payout_amount == 0 and (
                payout.state != "draft"
                or (
                    payout.platform_id
                    and payout.platform_currency_id
                    and payout.net_platform_amount > 0
                )
            ):
                raise ValidationError(
                    _("Complete the imported payout amount before continuing."),
                )
            line_allocations = self.search(
                [("bank_statement_line_id", "=", bank_line.id)],
            )
            if (
                float_compare(
                    sum(line_allocations.mapped("bank_amount")),
                    bank_line.amount,
                    precision_rounding=bank_line.currency_id.rounding,
                )
                > 0
            ):
                raise ValidationError(
                    _("Allocations cannot exceed the bank transaction amount."),
                )
            payout_allocations = self.search(
                [("payout_id", "=", payout.id)],
            )
            if payout.platform_currency_id and (
                float_compare(
                    sum(payout_allocations.mapped("payout_amount")),
                    payout.net_platform_amount,
                    precision_rounding=payout.platform_currency_id.rounding,
                )
                > 0
            ):
                raise ValidationError(
                    _("Allocations cannot exceed the platform payout amount."),
                )

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(
                _("Bank allocations can only be created by Platform Billing actions."),
            )
        return super().create(vals_list)

    @api.model_create_multi
    def _action_create(self, vals_list):
        payouts = self.env["usl.platform.billing.payout"].browse(
            [values.get("payout_id") for values in vals_list],
        ).exists()
        payouts.session_id._check_operator()
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise AccessError(
                _("Bank allocations can only be changed by Platform Billing actions."),
            )
        return super().write(vals)

    def _action_write(self, vals):
        self.payout_id.session_id._check_operator()
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise AccessError(
                _("Bank allocations can only be removed by Platform Billing actions."),
            )
        return super().unlink()

    def _action_unlink(self):
        self.payout_id.session_id._check_operator()
        if self.filtered(
            lambda allocation: (
                allocation.bank_statement_line_id.is_reconciled
                or allocation.payout_id.state in {"paid", "cancelled"}
            ),
        ):
            raise UserError(
                _("Reconciled, paid or cancelled bank allocations cannot be removed."),
            )
        return super().unlink()

    def _reconcile_bank_transaction(self):
        if not self:
            return
        bank_lines = self.bank_statement_line_id
        if len(bank_lines) != 1:
            raise UserError(_("Process one bank transaction at a time."))
        bank_line = bank_lines
        allocations = self.search(
            [("bank_statement_line_id", "=", bank_line.id)],
        )
        payouts = allocations.payout_id
        # A platform-level compensation entry may settle commission across
        # several invoices in an order that does not preserve each payout's
        # individual residual. Reconcile receipts against the complete,
        # economically equivalent compensation pool instead of treating an
        # arbitrary residual distribution as a payout mismatch.
        pool_payouts = payouts
        for compensation in payouts.compensation_move_id:
            pool_payouts |= compensation.platform_billing_payout_ids
        invoices = pool_payouts.customer_invoice_id
        if not invoices or invoices.filtered(lambda invoice: invoice.state != "posted"):
            raise UserError(_("Every linked customer invoice must be posted first."))
        if bank_line.is_reconciled:
            allocations._action_write({"blocked_reason": False})
            return
        if bank_line.move_id.state != "posted":
            raise UserError(_("The selected bank transaction is not posted."))
        allocated_bank_amount = bank_line.currency_id.round(
            sum(allocations.mapped("bank_amount")),
        )
        if float_compare(
            bank_line.amount,
            allocated_bank_amount,
            precision_rounding=bank_line.currency_id.rounding,
        ):
            raise UserError(
                _(
                    "Allocate the complete bank transaction before reconciliation: "
                    "%(allocated)s of %(actual)s is currently allocated.",
                    allocated=allocated_bank_amount,
                    actual=bank_line.amount,
                ),
            )
        partners = invoices.commercial_partner_id
        currencies = invoices.currency_id
        if len(partners) != 1:
            raise UserError(
                _(
                    "Automatic pooled reconciliation needs one customer partner. "
                    "The links are saved; reconcile this mixed payment in Accounting.",
                ),
            )
        if len(currencies) != 1:
            raise UserError(
                _(
                    "Automatic pooled reconciliation needs one invoice currency. "
                    "The links are saved; reconcile this mixed payment in Accounting.",
                ),
            )
        receivable = invoices.line_ids.filtered(
            lambda line: (
                line.account_id.account_type == "asset_receivable"
                and not line.reconciled
            ),
        )
        if not receivable:
            raise UserError(
                _(
                    "No open receivable remains in the linked compensation pool. "
                    "Review whether this bank receipt was already settled elsewhere.",
                ),
            )

        values = {"partner_id": partners.id}
        transaction_currency = bank_line.currency_id
        invoice_currency = currencies
        payout_amount = invoice_currency.round(
            sum(allocations.mapped("payout_amount")),
        )
        if invoice_currency != transaction_currency:
            if bank_line.foreign_currency_id not in (
                self.env["res.currency"],
                invoice_currency,
            ):
                raise UserError(
                    _(
                        "The bank transaction carries another foreign currency. "
                        "Keep the links and reconcile it manually in Accounting.",
                    ),
                )
            values.update(
                {
                    "foreign_currency_id": invoice_currency.id,
                    "amount_currency": payout_amount,
                },
            )
        elif bank_line.foreign_currency_id:
            raise UserError(
                _(
                    "The bank transaction has an unexpected foreign currency. "
                    "Keep the links and reconcile it manually in Accounting.",
                ),
            )
        bank_line._write_reconciliation_metadata(values)
        bank_line.reconcile_data_info = bank_line._default_reconcile_data()
        for line in receivable.sorted(key=lambda item: (item.date, item.id)):
            bank_line._add_account_move_line(line)
        if not bank_line.can_reconcile:
            raise UserError(
                _("OCA reconciliation could not balance this bank transaction."),
            )
        bank_line.reconcile_bank_line()
        if not bank_line.is_reconciled:
            raise UserError(_("The bank transaction remains unreconciled."))
        allocations._action_write({"blocked_reason": False})
