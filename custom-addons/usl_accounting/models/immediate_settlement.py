from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_INTERNAL_SETTLEMENT_TOKEN = object()
_INTERNAL_SETTLEMENT_CONTEXT_KEY = "immediate_settlement_internal_token"

_ECONOMIC_DISPLAY_TYPES = {
    "product",
    "discount",
    "rounding",
    "epd",
    "non_deductible_product",
}
_SAFE_ECONOMIC_ACCOUNT_TYPES = {
    "expense",
    "expense_other",
    "expense_depreciation",
    "expense_direct_cost",
    "income",
    "income_other",
    "asset_current",
    "asset_non_current",
    "liability_current",
    "liability_non_current",
}


def _has_internal_settlement_token(env):
    return (
        env.context.get(_INTERNAL_SETTLEMENT_CONTEXT_KEY)
        is _INTERNAL_SETTLEMENT_TOKEN
    )


def _as_settlement_service(records):
    return records.with_context(
        **{_INTERNAL_SETTLEMENT_CONTEXT_KEY: _INTERNAL_SETTLEMENT_TOKEN},
    ).sudo()


class ResCompany(models.Model):
    _inherit = "res.company"

    immediate_settlement_max_days = fields.Integer(
        string="Payment-rate maximum delay",
        default=3,
        help=(
            "Maximum calendar-day gap for treating a foreign-currency document "
            "and its bank transaction as one immediate economic event. Exact "
            "foreign-amount settlement remains available outside this delay."
        ),
    )
    immediate_settlement_max_rate_deviation = fields.Float(
        string="Payment-rate maximum rate deviation (%)",
        default=3.0,
        digits=(12, 4),
        help=(
            "Maximum deviation between the bank's inferred executed rate and "
            "Odoo's reference rate for the Use payment rate action."
        ),
    )

    @api.constrains(
        "immediate_settlement_max_days",
        "immediate_settlement_max_rate_deviation",
    )
    def _check_immediate_settlement_policy(self):
        for company in self:
            if company.immediate_settlement_max_days < 0:
                raise UserError(
                    _("The payment-rate delay cannot be negative."),
                )
            if not 0 <= company.immediate_settlement_max_rate_deviation <= 100:
                raise UserError(
                    _(
                        "The payment-rate deviation must be "
                        "between 0% and 100%.",
                    ),
                )


class AccountJournal(models.Model):
    _inherit = "account.journal"

    immediate_settlement_policy_override = fields.Boolean(
        string="Override payment-rate policy",
    )
    immediate_settlement_max_days = fields.Integer(
        string="Maximum delay",
        default=3,
    )
    immediate_settlement_max_rate_deviation = fields.Float(
        string="Maximum rate deviation (%)",
        default=3.0,
        digits=(12, 4),
    )

    @api.constrains(
        "immediate_settlement_max_days",
        "immediate_settlement_max_rate_deviation",
    )
    def _check_immediate_settlement_policy(self):
        for journal in self.filtered("immediate_settlement_policy_override"):
            if journal.immediate_settlement_max_days < 0:
                raise UserError(
                    _("The payment-rate delay cannot be negative."),
                )
            if not 0 <= journal.immediate_settlement_max_rate_deviation <= 100:
                raise UserError(
                    _(
                        "The payment-rate deviation must be "
                        "between 0% and 100%.",
                    ),
                )


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    immediate_settlement_max_days = fields.Integer(
        related="company_id.immediate_settlement_max_days",
        readonly=False,
    )
    immediate_settlement_max_rate_deviation = fields.Float(
        related="company_id.immediate_settlement_max_rate_deviation",
        readonly=False,
    )


class AccountImmediateSettlement(models.Model):
    _name = "account.immediate.settlement"
    _description = "Foreign-Currency Settlement"
    _order = "settlement_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, readonly=True, copy=False)
    mechanism = fields.Selection(
        [
            ("bank_statement", "Exact amount with native FX"),
            ("payment_rate", "Payment rate without FX"),
            ("legacy_adjustment", "Legacy payment-rate adjustment"),
        ],
        required=True,
        default="bank_statement",
        readonly=True,
        index=True,
        copy=False,
    )
    payment_rate_application = fields.Selection(
        [
            ("document_reprice", "Document repricing"),
            ("legacy_bank_adjustment", "Legacy bank adjustment"),
        ],
        readonly=True,
        index=True,
        copy=False,
        help=(
            "How a payment-rate settlement was applied. New settlements reprice "
            "the document through Odoo's native draft/post workflow. The legacy "
            "value preserves preview-era bank adjustment records."
        ),
    )
    state = fields.Selection(
        [("settled", "Settled"), ("reversed", "Reversed")],
        required=True,
        default="settled",
        readonly=True,
        index=True,
        copy=False,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        readonly=True,
        index=True,
    )
    company_currency_id = fields.Many2one(
        related="company_id.currency_id",
        string="Company Currency",
        readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Foreign Currency",
        required=True,
        readonly=True,
    )
    document_id = fields.Many2one(
        "account.move",
        required=True,
        readonly=True,
        check_company=True,
        index=True,
    )
    document_line_ids = fields.Many2many(
        "account.move.line",
        relation="account_immediate_settlement_document_line_rel",
        column1="settlement_id",
        column2="line_id",
        readonly=True,
        check_company=True,
    )
    source_line_id_snapshot = fields.Integer(
        string="Original Suggested Line ID",
        required=True,
        default=0,
        readonly=True,
        index=True,
    )
    payment_line_id = fields.Many2one(
        "account.move.line",
        readonly=True,
        check_company=True,
        index=True,
        ondelete="set null",
        help="Legacy source journal item. New bank settlements use the snapshot ID.",
    )
    payment_move_id = fields.Many2one(
        related="payment_line_id.move_id",
        readonly=True,
        store=True,
    )
    payment_id = fields.Many2one(
        related="payment_line_id.payment_id",
        readonly=True,
        store=True,
    )
    statement_line_id = fields.Many2one(
        "account.bank.statement.line",
        readonly=True,
        check_company=True,
        index=True,
        ondelete="restrict",
    )
    original_statement_foreign_currency_id = fields.Many2one(
        "res.currency",
        readonly=True,
    )
    original_statement_foreign_amount = fields.Monetary(
        currency_field="original_statement_foreign_currency_id",
        readonly=True,
    )
    original_statement_foreign_amount_source = fields.Char(readonly=True)
    bank_move_id = fields.Many2one(
        "account.move",
        string="Bank Entry",
        readonly=True,
        check_company=True,
        index=True,
    )
    foreign_amount = fields.Monetary(
        currency_field="currency_id",
        required=True,
        readonly=True,
    )
    foreign_amount_source = fields.Selection(
        [
            ("document_residual", "Selected document residual"),
            ("bank_reported", "Bank-reported foreign amount"),
        ],
        readonly=True,
    )
    company_amount = fields.Monetary(
        currency_field="company_currency_id",
        required=True,
        readonly=True,
    )
    reference_company_amount = fields.Monetary(
        string="Document Carrying Value",
        currency_field="company_currency_id",
        required=True,
        readonly=True,
    )
    benchmark_company_amount = fields.Monetary(
        string="Reference-Rate Value",
        currency_field="company_currency_id",
        readonly=True,
    )
    synthetic_foreign_amount = fields.Monetary(
        string="Discarded Odoo Estimate",
        currency_field="currency_id",
        readonly=True,
    )
    preview_settlement_difference = fields.Monetary(
        string="Previewed Carrying-Value Difference",
        currency_field="company_currency_id",
        readonly=True,
        help=(
            "Company-currency difference predicted before reconciliation. "
            "Settle records it as native FX; Use payment rate removes it by "
            "repricing the eligible document before reconciliation."
        ),
    )
    settlement_difference = fields.Monetary(
        currency_field="company_currency_id",
        readonly=True,
        help=(
            "Signed company-currency difference produced by OCA's native bank "
            "reconciliation. Positive uses the exchange-loss account."
        ),
    )
    settlement_difference_type = fields.Selection(
        [("none", "No difference"), ("loss", "FX loss"), ("gain", "FX gain")],
        required=True,
        default="none",
        readonly=True,
    )
    exchange_account_id = fields.Many2one(
        "account.account",
        readonly=True,
        check_company=True,
    )
    exchange_line_ids = fields.Many2many(
        "account.move.line",
        relation="account_immediate_settlement_exchange_line_rel",
        column1="settlement_id",
        column2="line_id",
        readonly=True,
        check_company=True,
    )
    exchange_move_ids = fields.Many2many(
        "account.move",
        relation="account_immediate_settlement_exchange_move_rel",
        column1="settlement_id",
        column2="move_id",
        string="Native Exchange Entries",
        readonly=True,
        check_company=True,
    )
    exchange_move_names = fields.Char(
        string="Native Exchange Entry References",
        readonly=True,
        help="Stable reference snapshot retained if native reversal removes an entry.",
    )
    economic_adjustment_amount = fields.Monetary(
        currency_field="company_currency_id",
        readonly=True,
        help=(
            "Signed company-currency amount allocated to the original economic "
            "accounts by a preserved legacy payment-rate settlement."
        ),
    )
    economic_adjustment_line_ids = fields.Many2many(
        "account.move.line",
        relation="account_immediate_settlement_economic_line_rel",
        column1="settlement_id",
        column2="line_id",
        string="Payment-Rate Adjustment Lines",
        readonly=True,
        check_company=True,
    )
    original_invoice_currency_rate = fields.Float(
        digits=(16, 10),
        readonly=True,
    )
    applied_invoice_currency_rate = fields.Float(
        digits=(16, 10),
        readonly=True,
    )
    original_document_company_amount = fields.Monetary(
        currency_field="company_currency_id",
        readonly=True,
    )
    repriced_document_company_amount = fields.Monetary(
        currency_field="company_currency_id",
        readonly=True,
    )
    document_revaluation_amount = fields.Monetary(
        currency_field="company_currency_id",
        readonly=True,
    )
    original_document_line_snapshot = fields.Json(readonly=True)
    repriced_document_line_snapshot = fields.Json(readonly=True)
    policy_date_distance = fields.Integer(readonly=True)
    policy_warning = fields.Char(readonly=True)
    generated_line_ids = fields.One2many(
        "account.move.line",
        "immediate_settlement_id",
        string="Generated Journal Items",
        readonly=True,
    )
    partial_reconcile_ids = fields.One2many(
        "account.partial.reconcile",
        "immediate_settlement_id",
        readonly=True,
    )
    executed_rate = fields.Float(
        digits=(16, 10),
        required=True,
        readonly=True,
    )
    reference_rate = fields.Float(
        digits=(16, 10),
        required=True,
        readonly=True,
    )
    rate_deviation = fields.Float(
        digits=(12, 4),
        required=True,
        readonly=True,
    )
    document_date = fields.Date(required=True, readonly=True)
    payment_date = fields.Date(required=True, readonly=True)
    settlement_date = fields.Date(required=True, readonly=True, index=True)
    provenance = fields.Char(required=True, readonly=True)
    provenance_details = fields.Json(readonly=True)
    trusted_source = fields.Boolean(readonly=True)
    user_id = fields.Many2one(
        "res.users",
        required=True,
        readonly=True,
    )
    reversed_user_id = fields.Many2one("res.users", readonly=True)
    reversed_at = fields.Datetime(readonly=True)

    # Kept only so preview-era records remain inspectable and reversible.
    adjustment_move_id = fields.Many2one(
        "account.move",
        readonly=True,
        check_company=True,
        index=True,
        copy=False,
    )
    reversal_move_id = fields.Many2one(
        "account.move",
        readonly=True,
        check_company=True,
        index=True,
        copy=False,
    )
    allocation_ids = fields.One2many(
        "account.immediate.settlement.allocation",
        "settlement_id",
        readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su or not _has_internal_settlement_token(self.env):
            raise AccessError(
                _("Settlement audit records can only be created by the service."),
            )
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su or not _has_internal_settlement_token(self.env):
            raise AccessError(_("Settlement audit records cannot be edited."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_module_uninstall(self):
        raise UserError(_("Settlement audit records cannot be deleted."))

    def _check_reversal_access_and_locks(self):
        if not self.env.user.has_group("account.group_account_user"):
            raise AccessError(_("Only accountants can reverse a settlement."))
        for settlement in self:
            settlement.document_id.check_access("write")
            if settlement.statement_line_id:
                settlement.statement_line_id.check_access("write")
            violations = []
            for accounting_date in {
                settlement.document_date,
                settlement.document_id.date,
                settlement.payment_date,
                settlement.settlement_date,
            }:
                violations.extend(
                    settlement.company_id._get_lock_date_violations(
                        accounting_date,
                        fiscalyear=True,
                        sale=True,
                        purchase=True,
                        tax=True,
                        hard=True,
                    ),
                )
            if violations:
                raise UserError(
                    _(
                        "This settlement cannot be reversed because its "
                        "accounting period is locked: %(locks)s.",
                        locks=settlement.company_id._format_lock_dates(
                            list(set(violations)),
                        ),
                    ),
                )

    def _reverse_bank_statement_settlement(self):
        for settlement in self:
            statement_line = settlement.statement_line_id
            if not statement_line:
                raise UserError(
                    _("The linked bank transaction is no longer available."),
                )
            if statement_line.move_id.inalterable_hash:
                raise UserError(
                    _("The linked bank entry is protected by a secure hash."),
                )
            statement_line.with_context(
                immediate_settlement_internal_token=_INTERNAL_SETTLEMENT_TOKEN,
            ).unreconcile_bank_line()
            statement_line.with_context(
                immediate_settlement_internal_token=_INTERNAL_SETTLEMENT_TOKEN,
                rebuild_skip_partner_inference=True,
            )._write_reconciliation_metadata(
                {
                    "foreign_currency_id": (
                        settlement.original_statement_foreign_currency_id.id
                    ),
                    "amount_currency": settlement.original_statement_foreign_amount,
                    "immediate_settlement_foreign_amount_source": (
                        settlement.original_statement_foreign_amount_source
                        if settlement.original_statement_foreign_amount_source
                        == "document_residual"
                        else False
                    ),
                    "immediate_settlement_document_id": False,
                    "active_immediate_settlement_id": False,
                },
            )

    def _restore_payment_rate_document(self):
        for settlement in self:
            document = settlement.document_id
            if (
                settlement.payment_rate_application != "document_reprice"
                or not settlement.original_invoice_currency_rate
            ):
                continue
            document.invalidate_recordset()
            if document.state != "posted" or document.payment_state != "not_paid":
                raise UserError(
                    _(
                        "The repriced document is not open and posted. Restore "
                        "it before reversing this settlement.",
                    ),
                )
            current_snapshot = document._payment_rate_document_snapshot()
            if document._payment_rate_snapshot_accounting_values(
                current_snapshot,
            ) != document._payment_rate_snapshot_accounting_values(
                settlement.repriced_document_line_snapshot or [],
            ):
                raise UserError(
                    _(
                        "The repriced document no longer matches its settlement "
                        "snapshot and cannot be restored automatically.",
                    ),
                )
            original_name = document.name
            original_date = document.date
            original_currency = document.currency_id
            internal_document = document.with_context(
                immediate_settlement_internal_token=_INTERNAL_SETTLEMENT_TOKEN,
            )
            internal_document.button_draft()
            internal_document.write(
                {
                    "invoice_currency_rate": (
                        settlement.original_invoice_currency_rate
                    ),
                },
            )
            internal_document._post(soft=False)
            document.invalidate_recordset()
            restored_snapshot = document._payment_rate_document_snapshot()
            if (
                document.state != "posted"
                or document.name != original_name
                or document.date != original_date
                or document.currency_id != original_currency
                or document._payment_rate_snapshot_accounting_values(
                    restored_snapshot,
                )
                != document._payment_rate_snapshot_accounting_values(
                    settlement.original_document_line_snapshot or [],
                )
            ):
                raise UserError(
                    _(
                        "Odoo could not restore the document's original "
                        "valuation. No reversal changes were saved.",
                    ),
                )

    def _reverse_legacy_settlement(self):
        for settlement in self:
            partials = settlement.partial_reconcile_ids
            if partials:
                partials.with_context(
                    immediate_settlement_internal_token=_INTERNAL_SETTLEMENT_TOKEN,
                ).unlink()
            reversal = self.env["account.move"]
            if settlement.adjustment_move_id:
                reversal = settlement.adjustment_move_id.with_context(
                    immediate_settlement_internal_token=_INTERNAL_SETTLEMENT_TOKEN,
                )._reverse_moves(
                    [
                        {
                            "date": fields.Date.context_today(settlement),
                            "ref": _(
                                "Reversal of legacy settlement %(name)s",
                                name=settlement.name,
                            ),
                        },
                    ],
                    cancel=True,
                )
            _as_settlement_service(settlement).write(
                {"reversal_move_id": reversal.id},
            )

    def action_reverse(self):
        active = self.filtered(lambda settlement: settlement.state == "settled")
        if not active:
            return True
        active._check_reversal_access_and_locks()
        settlement_ids = tuple(active.ids)
        self.env.cr.execute(
            "SELECT id FROM account_immediate_settlement "
            "WHERE id IN %s FOR UPDATE",
            [settlement_ids],
        )
        active.invalidate_recordset()
        for settlement in active.filtered(lambda item: item.state == "settled"):
            if settlement.mechanism in ("bank_statement", "payment_rate"):
                settlement._reverse_bank_statement_settlement()
                settlement._restore_payment_rate_document()
            else:
                settlement._reverse_legacy_settlement()
            _as_settlement_service(settlement).write(
                {
                    "state": "reversed",
                    "reversed_user_id": self.env.user.id,
                    "reversed_at": fields.Datetime.now(),
                },
            )
            settlement.document_id.message_post(
                body=_(
                    "Foreign-currency settlement %(name)s was reversed.",
                    name=settlement.name,
                ),
            )
        return True


class AccountImmediateSettlementAllocation(models.Model):
    """Immutable allocation snapshot for legacy payment-rate settlements."""

    _name = "account.immediate.settlement.allocation"
    _description = "Settlement Economic Allocation"
    _order = "id"
    _check_company_auto = True

    settlement_id = fields.Many2one(
        "account.immediate.settlement",
        required=True,
        readonly=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(
        related="settlement_id.company_id",
        store=True,
        readonly=True,
    )
    original_line_id = fields.Many2one(
        "account.move.line",
        required=True,
        readonly=True,
        check_company=True,
    )
    adjustment_line_id = fields.Many2one(
        "account.move.line",
        readonly=True,
        check_company=True,
        ondelete="set null",
    )
    adjustment_line_id_snapshot = fields.Integer(readonly=True)
    adjustment_line_name = fields.Char(readonly=True)
    account_id_snapshot = fields.Many2one(
        "account.account",
        readonly=True,
        check_company=True,
    )
    account_id = fields.Many2one(
        related="original_line_id.account_id",
        store=True,
        readonly=True,
    )
    company_amount = fields.Monetary(
        currency_field="company_currency_id",
        required=True,
        readonly=True,
    )
    company_currency_id = fields.Many2one(
        related="company_id.currency_id",
        readonly=True,
    )
    proportion = fields.Float(digits=(16, 10), readonly=True)
    analytic_distribution_snapshot = fields.Json(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su or not _has_internal_settlement_token(self.env):
            raise AccessError(_("Settlement allocations are service-managed."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su or not _has_internal_settlement_token(self.env):
            raise AccessError(_("Settlement allocations are service-managed."))
        return super().write(vals)
