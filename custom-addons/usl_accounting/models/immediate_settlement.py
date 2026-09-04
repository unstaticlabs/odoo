from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import formatLang

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


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    immediate_settlement_foreign_amount_source = fields.Selection(
        [("document_residual", "Selected document residual")],
        string="Foreign Amount Source",
        readonly=True,
        copy=False,
        index=True,
    )
    immediate_settlement_document_id = fields.Many2one(
        "account.move",
        readonly=True,
        copy=False,
        check_company=True,
        index=True,
    )
    active_immediate_settlement_id = fields.Many2one(
        "account.immediate.settlement",
        readonly=True,
        copy=False,
        check_company=True,
        index=True,
    )

    def _has_internal_settlement_token(self):
        return _has_internal_settlement_token(self.env)

    def _certified_reconciliation_metadata_fields(self):
        return super()._certified_reconciliation_metadata_fields() | {
            "immediate_settlement_foreign_amount_source",
            "immediate_settlement_document_id",
            "active_immediate_settlement_id",
        }

    def _reconcile_move_line_vals(self, line, move_id=False):
        vals = super()._reconcile_move_line_vals(line, move_id=move_id)
        for field_name in (
            "immediate_settlement_role",
            "immediate_settlement_source_line_id_snapshot",
        ):
            if line.get(field_name):
                vals[field_name] = line[field_name]
        return vals

    def write(self, vals):
        protected = {
            "immediate_settlement_foreign_amount_source",
            "immediate_settlement_document_id",
            "active_immediate_settlement_id",
        } & set(vals)
        if protected and not self._has_internal_settlement_token():
            raise AccessError(_("Settlement trace fields are service-managed."))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        protected_fields = {
            "immediate_settlement_foreign_amount_source",
            "immediate_settlement_document_id",
            "active_immediate_settlement_id",
        }
        if (
            any(protected_fields & set(vals) for vals in vals_list)
            and not self._has_internal_settlement_token()
        ):
            raise AccessError(_("Settlement trace fields are service-managed."))
        return super().create(vals_list)

    def unreconcile_bank_line(self):
        settlements = self.mapped("active_immediate_settlement_id").filtered(
            lambda settlement: settlement.state == "settled",
        )
        if settlements and not self._has_internal_settlement_token():
            return settlements.action_reverse()
        return super().unreconcile_bank_line()

    def action_undo_reconciliation(self):
        settlements = self.mapped("active_immediate_settlement_id").filtered(
            lambda settlement: settlement.state == "settled",
        )
        if settlements and not self._has_internal_settlement_token():
            return settlements.action_reverse()
        return super().action_undo_reconciliation()


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    immediate_settlement_id = fields.Many2one(
        "account.immediate.settlement",
        readonly=True,
        copy=False,
        index=True,
        check_company=True,
    )
    immediate_settlement_role = fields.Selection(
        [
            ("bank_counterpart", "Bank counterpart"),
            ("exchange_difference", "Settlement exchange difference"),
            ("payment_rate_economic", "Payment-rate economic adjustment"),
            ("suspense_clear", "Legacy suspense clearing"),
            ("payment_bridge", "Legacy payment bridge"),
            ("valuation", "Legacy document valuation"),
            ("economic", "Legacy economic allocation"),
        ],
        readonly=True,
        copy=False,
        index=True,
    )
    immediate_settlement_source_line_id_snapshot = fields.Integer(
        readonly=True,
        copy=False,
        index=True,
    )

    def write(self, vals):
        protected = {
            "immediate_settlement_id",
            "immediate_settlement_role",
            "immediate_settlement_source_line_id_snapshot",
        } & set(vals)
        if protected and not _has_internal_settlement_token(self.env):
            raise AccessError(_("Settlement trace fields are service-managed."))
        active_generated = self.filtered(
            lambda line: (
                line.immediate_settlement_id.state == "settled"
                and line.immediate_settlement_role
                in (
                    "bank_counterpart",
                    "exchange_difference",
                    "payment_rate_economic",
                )
            ),
        )
        accounting_fields = {
            "account_id",
            "partner_id",
            "balance",
            "debit",
            "credit",
            "amount_currency",
            "currency_id",
            "analytic_distribution",
            "tax_ids",
            "tax_tag_ids",
            "tax_repartition_line_id",
            "tax_base_amount",
            "name",
        }
        active_repriced_document_lines = self.filtered(
            lambda line: line.move_id.immediate_settlement_ids.filtered(
                lambda settlement: (
                    settlement.state == "settled"
                    and settlement.payment_rate_application
                    == "document_reprice"
                ),
            ),
        )
        if (
            active_repriced_document_lines
            and accounting_fields & set(vals)
            and not _has_internal_settlement_token(self.env)
        ):
            raise UserError(
                _(
                    "This repriced document's journal items cannot be edited "
                    "directly. Undo the linked settlement first.",
                ),
            )
        if (
            active_generated
            and accounting_fields & set(vals)
            and not _has_internal_settlement_token(self.env)
        ):
            raise UserError(
                _(
                    "Settlement-generated accounting lines cannot be edited "
                    "directly. Undo the linked settlement first.",
                ),
            )
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        protected_fields = {
            "immediate_settlement_id",
            "immediate_settlement_role",
            "immediate_settlement_source_line_id_snapshot",
        }
        if (
            any(protected_fields & set(vals) for vals in vals_list)
            and not _has_internal_settlement_token(self.env)
        ):
            raise AccessError(_("Settlement trace fields are service-managed."))
        return super().create(vals_list)

    def unlink(self):
        active_repriced_document_lines = self.filtered(
            lambda line: line.move_id.immediate_settlement_ids.filtered(
                lambda settlement: (
                    settlement.state == "settled"
                    and settlement.payment_rate_application
                    == "document_reprice"
                ),
            ),
        )
        if active_repriced_document_lines and not _has_internal_settlement_token(
            self.env,
        ):
            raise UserError(
                _(
                    "This repriced document's journal items cannot be deleted "
                    "directly. Undo the linked settlement first.",
                ),
            )
        active = self.filtered(
            lambda line: (
                line.immediate_settlement_id.state == "settled"
                and line.immediate_settlement_role
                in (
                    "bank_counterpart",
                    "exchange_difference",
                    "payment_rate_economic",
                )
            ),
        )
        if active and not _has_internal_settlement_token(self.env):
            raise UserError(
                _(
                    "Settlement-generated accounting lines cannot be deleted "
                    "directly. Undo the linked settlement first.",
                ),
            )
        return super().unlink()


class AccountPartialReconcile(models.Model):
    _inherit = "account.partial.reconcile"

    immediate_settlement_id = fields.Many2one(
        "account.immediate.settlement",
        readonly=True,
        copy=False,
        index=True,
        check_company=True,
    )

    def write(self, vals):
        if (
            "immediate_settlement_id" in vals
            and not _has_internal_settlement_token(self.env)
        ):
            raise AccessError(_("Settlement trace fields are service-managed."))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if (
            any("immediate_settlement_id" in vals for vals in vals_list)
            and not _has_internal_settlement_token(self.env)
        ):
            raise AccessError(_("Settlement trace fields are service-managed."))
        return super().create(vals_list)

    def unlink(self):
        settlements = self.immediate_settlement_id.filtered(
            lambda settlement: settlement.state == "settled",
        )
        internal = (
            self.env.context.get("immediate_settlement_internal_token")
            is _INTERNAL_SETTLEMENT_TOKEN
        )
        if settlements and not internal:
            settlements.action_reverse()
            remaining = self.exists()
            return (
                super(AccountPartialReconcile, remaining).unlink()
                if remaining
                else True
            )
        return super().unlink()


class AccountMove(models.Model):
    _inherit = "account.move"

    immediate_settlement_ids = fields.One2many(
        "account.immediate.settlement",
        "document_id",
        readonly=True,
    )
    immediate_settlement_count = fields.Integer(
        compute="_compute_immediate_settlement_count",
    )
    immediate_settlement_adjustment_id = fields.Many2one(
        "account.immediate.settlement",
        readonly=True,
        copy=False,
        index=True,
        check_company=True,
        help="Legacy preview-era adjustment link.",
    )

    def _compute_immediate_settlement_count(self):
        settlement_data = self.env["account.immediate.settlement"]._read_group(
            [
                "|",
                ("document_id", "in", self.ids),
                ("bank_move_id", "in", self.ids),
            ],
            ["document_id", "bank_move_id"],
            ["__count"],
        )
        counts = {move.id: 0 for move in self}
        for document, bank_move, count in settlement_data:
            if document.id in counts:
                counts[document.id] += count
            if bank_move.id in counts and bank_move != document:
                counts[bank_move.id] += count
        for move in self:
            move.immediate_settlement_count = counts[move.id]

    def _get_immediate_settlement_source_facts(self, payment_line):
        """Return server-owned bank facts used by exact-amount settlement.

        Integrations may override this hook to provide a trusted transaction
        date and provenance, or to mark a stored foreign amount authoritative.
        They must not provide a client-controlled trust flag.
        """
        self.ensure_one()
        statement_line = payment_line.move_id.statement_line_id
        authoritative_foreign = bool(
            statement_line
            and statement_line.foreign_currency_id
            and statement_line.amount_currency
            and not statement_line.immediate_settlement_foreign_amount_source,
        )
        return {
            "statement_line": statement_line,
            "company_amount": payment_line.amount_residual,
            "foreign_currency": (
                statement_line.foreign_currency_id if statement_line else False
            ),
            "foreign_amount": (
                statement_line.amount_currency if statement_line else 0.0
            ),
            "authoritative_foreign": authoritative_foreign,
            "conflicting_foreign": False,
            "has_fee_or_withholding": False,
            "transaction_date": statement_line.date if statement_line else payment_line.date,
            "trusted_date": False,
            "provenance": "bank_eur_and_document_residual",
            "details": {
                "company_amount_source": "bank_statement",
                "foreign_amount_source": "selected_document_residual",
            },
        }

    def _get_immediate_settlement_policy(self, payment_line):
        self.ensure_one()
        journal = payment_line.journal_id
        if journal.immediate_settlement_policy_override:
            return {
                "max_days": journal.immediate_settlement_max_days,
                "max_deviation": journal.immediate_settlement_max_rate_deviation,
            }
        return {
            "max_days": self.company_id.immediate_settlement_max_days,
            "max_deviation": self.company_id.immediate_settlement_max_rate_deviation,
        }

    def _immediate_settlement_term_candidates(
        self,
        company_amount,
        transaction_date,
    ):
        self.ensure_one()
        term_lines = self.line_ids.filtered(
            lambda line: (
                line.account_id.account_type
                in ("asset_receivable", "liability_payable")
                and not line.reconciled
                and line.currency_id == self.currency_id
                and not self.currency_id.is_zero(line.amount_residual_currency)
            ),
        )
        if not term_lines or len(term_lines.account_id) != 1:
            return []
        groups = [term_lines]
        if len(term_lines) > 1:
            groups.extend(line for line in term_lines)
        candidates = []
        seen_line_sets = set()
        for lines in groups:
            line_ids = tuple(sorted(lines.ids))
            if line_ids in seen_line_sets:
                continue
            seen_line_sets.add(line_ids)
            signed_document_company = sum(lines.mapped("amount_residual"))
            signed_document_foreign = sum(lines.mapped("amount_residual_currency"))
            if (
                self.company_currency_id.is_zero(signed_document_company)
                or self.currency_id.is_zero(signed_document_foreign)
                or signed_document_company * company_amount >= 0
            ):
                continue
            foreign_amount = abs(signed_document_foreign)
            benchmark_company_amount = abs(
                self.currency_id._convert(
                    foreign_amount,
                    self.company_currency_id,
                    self.company_id,
                    transaction_date,
                ),
            )
            if self.company_currency_id.is_zero(benchmark_company_amount):
                continue
            deviation = (
                abs(abs(company_amount) / benchmark_company_amount - 1.0) * 100.0
            )
            settlement_difference = self.company_currency_id.round(
                company_amount + signed_document_company,
            )
            candidates.append(
                {
                    "lines": lines,
                    "account": lines.account_id,
                    "foreign_amount": foreign_amount,
                    "signed_document_company": signed_document_company,
                    "signed_document_foreign": signed_document_foreign,
                    "reference_company_amount": abs(signed_document_company),
                    "benchmark_company_amount": benchmark_company_amount,
                    "rate_deviation": deviation,
                    "settlement_difference": settlement_difference,
                },
            )
        return candidates

    def _get_foreign_settlement_context(
        self,
        payment_line,
        *,
        raise_exception=False,
    ):
        self.ensure_one()

        def blocked(reason, *, plausible=False):
            if raise_exception:
                raise UserError(reason)
            return {
                "eligible": False,
                "reason": reason,
                "plausible": plausible,
            }

        if not self.env.user.has_group("account.group_account_user"):
            return blocked(
                _("Only accountants can settle an exact foreign amount."),
                plausible=True,
            )
        if (
            self.state != "posted"
            or not self.is_invoice(include_receipts=True)
            or self.payment_state not in ("not_paid", "partial")
        ):
            return blocked(_("The document must be posted and still open."))
        if self.currency_id == self.company_currency_id:
            return blocked(_("The document must use a foreign currency."))
        if not payment_line.exists() or payment_line.parent_state != "posted":
            return blocked(_("The selected bank transaction is no longer posted."))
        if payment_line.company_id != self.company_id:
            return blocked(
                _("The document and bank transaction must use the same company."),
                plausible=True,
            )
        if payment_line.reconciled:
            return blocked(
                _("The selected bank transaction is already reconciled."),
                plausible=True,
            )

        facts = self._get_immediate_settlement_source_facts(payment_line)
        statement_line = facts.get("statement_line")
        if not statement_line:
            return blocked(
                _("Settle is available only for bank transactions."),
            )
        if statement_line.is_reconciled:
            return blocked(
                _("The selected bank transaction is already reconciled."),
                plausible=True,
            )
        if statement_line.currency_id != self.company_currency_id:
            return blocked(
                _("The bank journal must use the company currency."),
                plausible=True,
            )
        if statement_line.journal_id.reconcile_mode != "edit":
            return blocked(
                _("This bank journal must use editable OCA reconciliation."),
                plausible=True,
            )
        if facts.get("conflicting_foreign"):
            return blocked(
                _(
                    "The bank or integration foreign amount conflicts with the "
                    "document. Review it in Bank Matching.",
                ),
                plausible=True,
            )
        if facts.get("has_fee_or_withholding"):
            return blocked(
                _(
                    "The bank transaction includes a fee or withholding. "
                    "Keep it separate in Bank Matching.",
                ),
                plausible=True,
            )
        if statement_line.immediate_settlement_foreign_amount_source:
            return blocked(
                _("This bank transaction already has an inferred foreign amount."),
                plausible=True,
            )
        if payment_line.account_id != statement_line.journal_id.suspense_account_id:
            return blocked(
                _(
                    "The bank transaction must still be on the journal's "
                    "suspense account.",
                ),
                plausible=True,
            )
        if not payment_line.account_id.reconcile:
            return blocked(
                _("The bank journal suspense account must allow reconciliation."),
                plausible=True,
            )
        liquidity_lines, suspense_lines, other_lines = statement_line._seek_for_lines()
        if (
            len(liquidity_lines) != 1
            or len(suspense_lines) != 1
            or suspense_lines != payment_line
            or other_lines
        ):
            return blocked(
                _(
                    "The bank transaction contains another allocation, fee, or "
                    "withholding. Review it in Bank Matching.",
                ),
                plausible=True,
            )
        if (
            statement_line.move_id.inalterable_hash
            or statement_line.move_id._is_protected_by_audit_trail()
        ):
            return blocked(
                _("The bank entry is protected by immutable accounting controls."),
                plausible=True,
            )
        company_amount = facts.get("company_amount")
        if not company_amount or self.company_currency_id.is_zero(company_amount):
            return blocked(
                _("The bank transaction has no remaining company-currency amount."),
                plausible=True,
            )
        assigned_partner = (
            payment_line.partner_id or statement_line.partner_id
        ).commercial_partner_id
        if assigned_partner and assigned_partner != self.commercial_partner_id:
            return blocked(
                _(
                    "The bank transaction is assigned to another partner. "
                    "Review it before settlement.",
                ),
                plausible=True,
            )

        policy = self._get_immediate_settlement_policy(payment_line)
        document_date = self.invoice_date or self.date
        transaction_date = facts.get("transaction_date") or payment_line.date
        date_distance = abs((transaction_date - document_date).days)

        candidates = self._immediate_settlement_term_candidates(
            company_amount,
            transaction_date,
        )
        if not candidates:
            return blocked(
                _(
                    "The bank amount does not identify a document residual or "
                    "payment term with a coherent direction.",
                ),
                plausible=True,
            )
        if facts.get("authoritative_foreign"):
            fact_currency = facts.get("foreign_currency")
            fact_amount = abs(facts.get("foreign_amount") or 0.0)
            if fact_currency != self.currency_id:
                return blocked(
                    _(
                        "The bank-reported foreign currency conflicts with the "
                        "document. Review it in Bank Matching.",
                    ),
                    plausible=True,
                )
            candidates = [
                candidate
                for candidate in candidates
                if self.currency_id.compare_amounts(
                    candidate["foreign_amount"],
                    fact_amount,
                )
                == 0
            ]
            if not candidates:
                return blocked(
                    _(
                        "The bank-reported foreign amount conflicts with the "
                        "selected document residual.",
                    ),
                    plausible=True,
                )
        elif len(candidates) > 1:
            policy_matches = [
                candidate
                for candidate in candidates
                if candidate["rate_deviation"] <= policy["max_deviation"]
            ]
            if len(policy_matches) == 1:
                candidates = policy_matches
        if len(candidates) != 1:
            return blocked(
                _(
                    "The bank amount matches more than one payment term. "
                    "Review it in Bank Matching.",
                ),
                plausible=True,
            )
        allocation = candidates[0]
        settlement_difference = allocation["settlement_difference"]
        if self.company_currency_id.is_zero(settlement_difference):
            difference_type = "none"
            exchange_account = self.env["account.account"]
        elif settlement_difference > 0:
            difference_type = "loss"
            exchange_account = self.company_id.expense_currency_exchange_account_id
        else:
            difference_type = "gain"
            exchange_account = self.company_id.income_currency_exchange_account_id
        violations = []
        for accounting_date in {document_date, transaction_date}:
            violations.extend(
                self.company_id._get_lock_date_violations(
                    accounting_date,
                    fiscalyear=True,
                    sale=True,
                    purchase=True,
                    tax=True,
                    hard=True,
                ),
            )
        if violations:
            return blocked(
                _(
                    "The settlement period is locked: %(locks)s.",
                    locks=self.company_id._format_lock_dates(list(set(violations))),
                ),
                plausible=True,
            )

        foreign_amount = allocation["foreign_amount"]
        company_amount_abs = abs(company_amount)
        executed_rate = company_amount_abs / foreign_amount
        reference_rate = (
            allocation["benchmark_company_amount"] / foreign_amount
        )
        synthetic_foreign_amount = self._rebuild_payment_candidate_amount(payment_line)
        synthetic_difference = self.currency_id.round(
            synthetic_foreign_amount - foreign_amount,
        )
        foreign_label = self.currency_id.format(foreign_amount)
        company_label = self.company_currency_id.format(company_amount_abs)
        synthetic_label = self.currency_id.format(synthetic_foreign_amount)
        warnings = []
        if not facts.get("trusted_date") and date_distance > policy["max_days"]:
            warnings.append(
                _(
                    "%(days)s days after the document",
                    days=date_distance,
                ),
            )
        if allocation["rate_deviation"] > policy["max_deviation"]:
            warnings.append(
                _(
                    "%(deviation).2f%% from the reference rate",
                    deviation=allocation["rate_deviation"],
                ),
            )
        warning = " · ".join(warnings)
        return {
            "eligible": True,
            "plausible": True,
            "facts": facts,
            "policy": policy,
            "allocation": allocation,
            "foreign_amount": foreign_amount,
            "company_amount": company_amount_abs,
            "synthetic_foreign_amount": synthetic_foreign_amount,
            "synthetic_difference": synthetic_difference,
            "settlement_difference": settlement_difference,
            "settlement_difference_type": difference_type,
            "exchange_account": exchange_account,
            "executed_rate": executed_rate,
            "reference_rate": reference_rate,
            "rate_deviation": allocation["rate_deviation"],
            "document_date": document_date,
            "payment_date": transaction_date,
            "settlement_date": transaction_date,
            "date_distance": date_distance,
            "policy_warning": warning,
            "foreign_label": foreign_label,
            "company_label": company_label,
            "synthetic_label": synthetic_label,
        }

    def _get_immediate_settlement_eligibility(
        self,
        payment_line,
        *,
        raise_exception=False,
    ):
        context = self._get_foreign_settlement_context(
            payment_line,
            raise_exception=raise_exception,
        )
        if not context["eligible"]:
            return context
        if context["facts"].get("authoritative_foreign") or (
            self.currency_id.compare_amounts(
                context["synthetic_foreign_amount"],
                context["foreign_amount"],
            )
            == 0
        ):
            reason = _(
                "Add already uses the exact foreign amount for this bank transaction.",
            )
            if raise_exception:
                raise UserError(reason)
            return {
                "eligible": False,
                "plausible": False,
                "reason": reason,
            }
        if (
            context["settlement_difference_type"] != "none"
            and not context["exchange_account"]
        ):
            reason = _("Configure Odoo's currency exchange accounts before settlement.")
            if raise_exception:
                raise UserError(reason)
            return {
                "eligible": False,
                "plausible": True,
                "reason": reason,
            }
        difference_type = context["settlement_difference_type"]
        if difference_type == "none":
            consequence = _("No settlement difference.")
        else:
            consequence = _(
                "Odoo records %(amount)s FX %(kind)s.",
                amount=self.company_currency_id.format(
                    abs(context["settlement_difference"]),
                ),
                kind=difference_type,
            )
        reason = _(
            "Use the document's exact %(foreign)s. %(consequence)s",
            foreign=context["foreign_label"],
            consequence=consequence,
        )
        if context["policy_warning"]:
            reason = _(
                "%(reason)s Check: %(warning)s.",
                reason=reason,
                warning=context["policy_warning"],
            )
        return {
            **context,
            "reason": reason,
            "confidence": (
                "recommended" if context["policy_warning"] else "alternative"
            ),
        }

    def _immediate_settlement_safe_economic_lines(self):
        self.ensure_one()
        economic_lines = self.line_ids.filtered(
            lambda line: (
                line.display_type in _ECONOMIC_DISPLAY_TYPES
                and not line.tax_line_id
                and not self.company_currency_id.is_zero(line.balance)
            ),
        )
        if not economic_lines:
            return economic_lines
        lines = economic_lines.filtered(
            lambda line: (
                not line.account_id.reconcile
                and line.account_id.account_type in _SAFE_ECONOMIC_ACCOUNT_TYPES
            ),
        )
        if lines != economic_lines:
            return self.env["account.move.line"]
        signs = {1 if line.balance > 0 else -1 for line in lines}
        if len(signs) != 1:
            return self.env["account.move.line"]
        for line in lines:
            if (
                ("asset_id" in line._fields and line.asset_id)
                or ("asset_profile_id" in line._fields and line.asset_profile_id)
                or (
                    "deferred_start_date" in line._fields
                    and line.deferred_start_date
                )
                or ("deferred_end_date" in line._fields and line.deferred_end_date)
                or (
                    line.product_id
                    and "property_valuation" in line.product_id.categ_id._fields
                    and line.product_id.categ_id.property_valuation == "real_time"
                )
            ):
                return self.env["account.move.line"]
        return lines

    def _payment_rate_term_lines(self):
        self.ensure_one()
        return self.line_ids.filtered(
            lambda line: (
                line.account_id.account_type
                in ("asset_receivable", "liability_payable")
                and line.currency_id == self.currency_id
            ),
        )

    def _payment_rate_document_company_amount(self):
        self.ensure_one()
        return abs(sum(self._payment_rate_term_lines().mapped("balance")))

    def _payment_rate_document_snapshot(self):
        self.ensure_one()
        return [
            {
                "line_id": line.id,
                "account_id": line.account_id.id,
                "display_type": line.display_type,
                "partner_id": line.partner_id.id,
                "name": line.name,
                "balance": line.balance,
                "amount_currency": line.amount_currency,
                "currency_id": line.currency_id.id,
                "analytic_distribution": line.analytic_distribution or {},
                "tax_line_id": line.tax_line_id.id,
                "tax_ids": sorted(line.tax_ids.ids),
                "tax_tag_ids": sorted(line.tax_tag_ids.ids),
                "tax_repartition_line_id": line.tax_repartition_line_id.id,
                "tax_base_amount": line.tax_base_amount,
            }
            for line in self.line_ids.sorted("id")
        ]

    @api.model
    def _payment_rate_snapshot_structure(self, snapshot):
        ignored = {"line_id", "balance", "amount_currency"}
        structure = [
            {key: value for key, value in line.items() if key not in ignored}
            for line in snapshot
        ]
        return sorted(
            structure,
            key=lambda line: (
                line["display_type"] or "",
                line["account_id"],
                line["name"] or "",
                repr(line),
            ),
        )

    def _payment_rate_snapshot_accounting_values(self, snapshot):
        self.ensure_one()
        return sorted(
            (
                line["account_id"],
                line["display_type"] or "",
                line["name"] or "",
                line["currency_id"],
                self.company_currency_id.round(line["balance"]),
                self.currency_id.round(line["amount_currency"]),
                repr(line["analytic_distribution"]),
            )
            for line in snapshot
        )

    def _payment_rate_document_tax_metadata(self):
        self.ensure_one()
        return self.line_ids.filtered(
            lambda line: (
                line.tax_line_id
                or line.tax_ids
                or line.tax_tag_ids
                or line.tax_repartition_line_id
                or not self.company_currency_id.is_zero(line.tax_base_amount)
            ),
        )

    def _get_payment_rate_settlement_eligibility(
        self,
        payment_line,
        *,
        raise_exception=False,
    ):
        context = self._get_foreign_settlement_context(
            payment_line,
            raise_exception=raise_exception,
        )
        if not context["eligible"]:
            return context

        def blocked(reason):
            if raise_exception:
                raise UserError(reason)
            return {
                "eligible": False,
                "plausible": True,
                "reason": reason,
            }

        if self.payment_state != "not_paid":
            return blocked(
                _(
                    "Use payment rate requires a completely unpaid document. "
                    "Use Settle for a remaining balance.",
                ),
            )
        term_lines = self._payment_rate_term_lines()
        if (
            not term_lines
            or term_lines != context["allocation"]["lines"]
            or any(
                line.reconciled
                or line.matched_debit_ids
                or line.matched_credit_ids
                for line in term_lines
            )
        ):
            return blocked(
                _(
                    "Use payment rate requires one bank transaction for the "
                    "complete, never-paid document.",
                ),
            )
        if self._payment_rate_document_tax_metadata():
            return blocked(
                _(
                    "Use payment rate is not available when the document "
                    "contains taxes. Use Settle to preserve its tax valuation.",
                ),
            )
        if (
            not context["facts"].get("trusted_date")
            and context["date_distance"] > context["policy"]["max_days"]
        ):
            return blocked(
                _(
                    "Use payment rate is limited to %(maximum)s days; this "
                    "transaction is %(days)s days from the document.",
                    maximum=context["policy"]["max_days"],
                    days=context["date_distance"],
                ),
            )
        if context["rate_deviation"] > context["policy"]["max_deviation"]:
            return blocked(
                _(
                    "The payment rate is %(deviation).2f%% from Odoo's reference "
                    "rate, above the %(maximum).2f%% policy.",
                    deviation=context["rate_deviation"],
                    maximum=context["policy"]["max_deviation"],
                ),
            )
        if not self.env.user.has_group("account.group_account_invoice"):
            return blocked(
                _(
                    "You need permission to reset and post the document before "
                    "using its payment rate.",
                ),
            )
        try:
            self.check_access("write")
        except AccessError:
            return blocked(
                _(
                    "You do not have permission to revalue and repost this "
                    "document.",
                ),
            )
        if (
            not self.show_reset_to_draft_button
            or self.need_cancel_request
            or self.inalterable_hash
            or self._is_protected_by_audit_trail()
        ):
            return blocked(
                _(
                    "This document is protected from reset to draft. Use "
                    "Settle without changing its original valuation.",
                ),
            )
        if self.is_move_sent:
            return blocked(
                _(
                    "This document was already sent and cannot be repriced "
                    "automatically.",
                ),
            )
        if "edi_document_ids" in self._fields and self.edi_document_ids.filtered(
            lambda document: document.state not in ("cancelled",),
        ):
            return blocked(
                _(
                    "This document has an active electronic-invoice record and "
                    "cannot be repriced automatically.",
                ),
            )
        lock_violations = self.company_id._get_lock_date_violations(
            self.date,
            fiscalyear=True,
            sale=True,
            purchase=True,
            tax=True,
            hard=True,
        )
        if lock_violations:
            return blocked(
                _(
                    "The document cannot be reset because its accounting period "
                    "is locked: %(locks)s.",
                    locks=self.company_id._format_lock_dates(lock_violations),
                ),
            )
        try:
            self._check_draftable()
        except UserError as error:
            return blocked(str(error))
        economic_lines = self._immediate_settlement_safe_economic_lines()
        if not economic_lines:
            return blocked(
                _(
                    "This document contains economic lines that cannot safely "
                    "be repriced at the payment rate.",
                ),
            )
        applied_invoice_currency_rate = (
            context["foreign_amount"] / context["company_amount"]
        )
        expected_company_amount = abs(
            sum(
                self.company_currency_id.round(
                    line.amount_currency / applied_invoice_currency_rate,
                )
                for line in economic_lines
            ),
        )
        if self.company_currency_id.compare_amounts(
            expected_company_amount,
            context["company_amount"],
        ):
            return blocked(
                _(
                    "Document-line rounding does not produce the exact bank "
                    "amount at this payment rate. Use Settle instead.",
                ),
            )
        reason = _(
            "Value the document at the bank's %(company)s rate and match "
            "%(foreign)s. No FX.",
            foreign=context["foreign_label"],
            company=context["company_label"],
        )
        return {
            **context,
            "eligible": True,
            "reason": reason,
            "confidence": "recommended",
            "economic_lines": economic_lines,
            "applied_invoice_currency_rate": applied_invoice_currency_rate,
            "original_document_company_amount": (
                self._payment_rate_document_company_amount()
            ),
            "repriced_document_company_amount": expected_company_amount,
        }

    def _prepare_exact_settlement_reconcile_data(self, statement_line, eligibility):
        data = []
        reconcile_auxiliary_id = 1
        liquidity_lines, _suspense_lines, _other_lines = (
            statement_line._seek_for_lines()
        )
        for liquidity_line in liquidity_lines:
            reconcile_auxiliary_id, line_data = statement_line._get_reconcile_line(
                liquidity_line,
                "liquidity",
                reconcile_auxiliary_id=reconcile_auxiliary_id,
                move=True,
            )
            data += line_data
        for term_line in eligibility["allocation"]["lines"]:
            reconcile_auxiliary_id, line_data = statement_line._get_reconcile_line(
                term_line,
                "other",
                is_counterpart=True,
                reconcile_auxiliary_id=reconcile_auxiliary_id,
                move=True,
            )
            for values in line_data:
                if values.get("counterpart_line_ids"):
                    values.update(
                        {
                            "immediate_settlement_role": "bank_counterpart",
                            "immediate_settlement_source_line_id_snapshot": (
                                term_line.id
                            ),
                        },
                    )
            data += line_data
        return statement_line._recompute_suspense_line(
            data,
            reconcile_auxiliary_id,
            statement_line.manual_reference,
        )

    def _apply_payment_rate_to_document(self, eligibility):
        self.ensure_one()
        original_name = self.name
        original_date = self.date
        original_currency = self.currency_id
        original_rate = self.invoice_currency_rate
        original_snapshot = self._payment_rate_document_snapshot()
        original_company_amount = self._payment_rate_document_company_amount()

        self.button_draft()
        self.write(
            {
                "invoice_currency_rate": eligibility[
                    "applied_invoice_currency_rate"
                ],
            },
        )
        self._post(soft=False)
        self.invalidate_recordset()

        repriced_snapshot = self._payment_rate_document_snapshot()
        repriced_company_amount = self._payment_rate_document_company_amount()
        if (
            self.state != "posted"
            or self.name != original_name
            or self.date != original_date
            or self.currency_id != original_currency
        ):
            raise UserError(
                _(
                    "Reposting changed the document identity. No accounting "
                    "changes were saved.",
                ),
            )
        if self._payment_rate_snapshot_structure(
            original_snapshot,
        ) != self._payment_rate_snapshot_structure(repriced_snapshot):
            raise UserError(
                _(
                    "Reposting changed the document's accounts, analytics, or "
                    "line structure. No accounting changes were saved.",
                ),
            )
        original_foreign = [
            values[:4] + (values[5], values[6])
            for values in self._payment_rate_snapshot_accounting_values(
                original_snapshot,
            )
        ]
        repriced_foreign = [
            values[:4] + (values[5], values[6])
            for values in self._payment_rate_snapshot_accounting_values(
                repriced_snapshot,
            )
        ]
        if original_foreign != repriced_foreign:
            raise UserError(
                _(
                    "Reposting changed the document's foreign-currency amounts. "
                    "No accounting changes were saved.",
                ),
            )
        if self.company_currency_id.compare_amounts(
            repriced_company_amount,
            eligibility["company_amount"],
        ):
            raise UserError(
                _(
                    "The payment rate did not produce the exact bank amount on "
                    "the document. No accounting changes were saved.",
                ),
            )
        if self._payment_rate_document_tax_metadata():
            raise UserError(
                _(
                    "Reposting introduced tax metadata. No accounting changes "
                    "were saved.",
                ),
            )
        return {
            "original_rate": original_rate,
            "applied_rate": self.invoice_currency_rate,
            "original_company_amount": original_company_amount,
            "repriced_company_amount": repriced_company_amount,
            "original_snapshot": original_snapshot,
            "repriced_snapshot": repriced_snapshot,
        }

    def _lock_foreign_settlement_records(self, payment_line):
        move_ids = tuple(sorted({self.id, payment_line.move_id.id}))
        self.env.cr.execute(
            "SELECT id FROM account_move WHERE id IN %s FOR UPDATE",
            [move_ids],
        )
        statement_line = payment_line.move_id.statement_line_id
        if statement_line:
            self.env.cr.execute(
                "SELECT id FROM account_bank_statement_line "
                "WHERE id = %s FOR UPDATE",
                [statement_line.id],
            )
        line_ids = tuple(
            sorted(
                {
                    payment_line.id,
                    *self.line_ids.filtered(
                        lambda line: line.account_id.account_type
                        in ("asset_receivable", "liability_payable"),
                    ).ids,
                },
            ),
        )
        self.env.cr.execute(
            "SELECT id FROM account_move_line WHERE id IN %s FOR UPDATE",
            [line_ids],
        )

    def _active_foreign_settlement(self, line_id):
        return self.env["account.immediate.settlement"].search(
            [
                ("document_id", "=", self.id),
                ("source_line_id_snapshot", "=", line_id),
                ("state", "=", "settled"),
            ],
            limit=1,
        )

    def _foreign_settlement_tax_snapshot(self):
        self.ensure_one()
        return [
            (
                line.id,
                line.balance,
                line.amount_currency,
                line.tax_line_id.id,
                tuple(sorted(line.tax_ids.ids)),
                tuple(sorted(line.tax_tag_ids.ids)),
                line.tax_repartition_line_id.id,
                line.tax_base_amount,
            )
            for line in self.line_ids.sorted("id")
        ]

    def _execute_foreign_settlement(self, line_id, mechanism):
        self.ensure_one()
        if not self.env.user.has_group("account.group_account_user"):
            raise AccessError(
                _("Only accountants can settle a foreign-currency document."),
            )
        self.check_access("write")
        existing = self._active_foreign_settlement(line_id)
        if existing:
            if existing.mechanism == mechanism:
                return {"settlement_id": existing.id}
            raise UserError(
                _(
                    "This bank transaction was already settled using %(method)s.",
                    method=(
                        _("Use payment rate")
                        if existing.mechanism == "payment_rate"
                        else _("Settle")
                    ),
                ),
            )
        payment_line = self.env["account.move.line"].browse(line_id).exists()
        if not payment_line:
            raise UserError(
                _("This bank transaction no longer exists. Refresh the document."),
            )
        statement_line = payment_line.move_id.statement_line_id
        if statement_line:
            statement_line.check_access("write")
        self._lock_foreign_settlement_records(payment_line)
        self.invalidate_recordset()
        payment_line.invalidate_recordset()
        payment_line = payment_line.exists()
        existing = self._active_foreign_settlement(line_id)
        if existing:
            if existing.mechanism == mechanism:
                return {"settlement_id": existing.id}
            raise UserError(
                _("This bank transaction was settled by another action."),
            )
        if not payment_line:
            raise UserError(
                _("This bank transaction changed while settling. Refresh the document."),
            )
        eligibility_method = (
            self._get_payment_rate_settlement_eligibility
            if mechanism == "payment_rate"
            else self._get_immediate_settlement_eligibility
        )
        eligibility = eligibility_method(payment_line, raise_exception=True)
        statement_line = eligibility["facts"]["statement_line"]
        bank_move = statement_line.move_id
        original_foreign_currency = statement_line.foreign_currency_id
        original_foreign_amount = statement_line.amount_currency
        original_foreign_source = (
            statement_line.immediate_settlement_foreign_amount_source
            or (
                "bank_reported"
                if eligibility["facts"].get("authoritative_foreign")
                else "missing"
            )
        )
        original_liquidity = statement_line._seek_for_lines()[0]
        original_liquidity_snapshot = [
            (
                line.id,
                line.balance,
                line.amount_currency,
                line.currency_id.id,
            )
            for line in original_liquidity
        ]
        original_tax_snapshot = (
            False
            if mechanism == "payment_rate"
            else self._foreign_settlement_tax_snapshot()
        )
        original_line_ids = set(bank_move.line_ids.ids)
        original_partial_ids = set(
            (
                eligibility["allocation"]["lines"].matched_debit_ids
                + eligibility["allocation"]["lines"].matched_credit_ids
            ).ids,
        )
        reprice_result = {}
        if mechanism == "payment_rate":
            reprice_result = self._apply_payment_rate_to_document(eligibility)
            selected_lines = self._payment_rate_term_lines()
            reconcile_eligibility = {
                **eligibility,
                "allocation": {
                    **eligibility["allocation"],
                    "lines": selected_lines,
                },
            }
        else:
            selected_lines = eligibility["allocation"]["lines"]
            reconcile_eligibility = eligibility
        signed_foreign_amount = (
            eligibility["foreign_amount"]
            if statement_line.amount > 0
            else -eligibility["foreign_amount"]
        )
        partner_vals = {}
        if statement_line.partner_id != self.commercial_partner_id:
            partner_vals["partner_id"] = self.commercial_partner_id.id
        statement_values = {
            **partner_vals,
            "immediate_settlement_document_id": self.id,
        }
        if not eligibility["facts"].get("authoritative_foreign"):
            statement_values.update(
                {
                    "foreign_currency_id": self.currency_id.id,
                    "amount_currency": signed_foreign_amount,
                    "immediate_settlement_foreign_amount_source": (
                        "document_residual"
                    ),
                },
            )
        statement_line.with_context(
            immediate_settlement_internal_token=_INTERNAL_SETTLEMENT_TOKEN,
            rebuild_skip_partner_inference=True,
        )._write_reconciliation_metadata(statement_values)
        reconcile_data = self._prepare_exact_settlement_reconcile_data(
            statement_line,
            reconcile_eligibility,
        )
        if not reconcile_data.get("can_reconcile") or any(
            line.get("kind") == "suspense"
            for line in reconcile_data.get("data", [])
        ):
            raise UserError(
                _(
                    "OCA could not balance this exact foreign-amount settlement. "
                    "No accounting changes were saved.",
                ),
            )
        reconcile_statement = statement_line.with_context(
            immediate_settlement_internal_token=_INTERNAL_SETTLEMENT_TOKEN,
        )
        reconcile_statement._reconcile_bank_line_edit(
            reconcile_statement._prepare_reconcile_line_data(
                reconcile_data["data"],
            ),
        )
        statement_line.invalidate_recordset()
        bank_move.invalidate_recordset()
        self.invalidate_recordset()
        current_liquidity = statement_line._seek_for_lines()[0]
        current_liquidity_snapshot = [
            (
                line.id,
                line.balance,
                line.amount_currency,
                line.currency_id.id,
            )
            for line in current_liquidity
        ]
        if current_liquidity_snapshot != original_liquidity_snapshot:
            raise UserError(
                _("Settlement attempted to change the bank liquidity amount."),
            )
        _liquidity, suspense_lines, other_lines = statement_line._seek_for_lines()
        if suspense_lines or not statement_line.is_reconciled:
            raise UserError(
                _("Settlement did not fully clear the bank suspense balance."),
            )
        selected_lines.invalidate_recordset(
            ["amount_residual", "amount_residual_currency", "reconciled"],
        )
        if any(
            not line.currency_id.is_zero(line.amount_residual_currency)
            or not line.company_currency_id.is_zero(line.amount_residual)
            for line in selected_lines
        ):
            raise UserError(
                _("Settlement did not fully clear the selected document amount."),
            )
        if (
            mechanism != "payment_rate"
            and self._foreign_settlement_tax_snapshot() != original_tax_snapshot
        ):
            raise UserError(
                _("Settlement attempted to change the document's tax lines."),
            )
        new_partials = (
            selected_lines.matched_debit_ids + selected_lines.matched_credit_ids
        ).filtered(lambda partial: partial.id not in original_partial_ids)
        new_lines = other_lines.filtered(lambda line: line.id not in original_line_ids)
        counterpart_lines = new_lines.filtered(
            lambda line: (
                line.account_id == eligibility["allocation"]["account"]
                and line.currency_id == self.currency_id
            ),
        )
        if (
            not counterpart_lines
            or self.currency_id.compare_amounts(
                abs(sum(counterpart_lines.mapped("amount_currency"))),
                eligibility["foreign_amount"],
            )
            != 0
        ):
            raise UserError(
                _(
                    "Settlement did not create the exact foreign-currency "
                    "receivable or payable counterpart.",
                ),
            )
        exchange_moves = new_partials.exchange_move_id
        exchange_lines = exchange_moves.line_ids.filtered(
            lambda line: line.account_id == eligibility["exchange_account"],
        )
        actual_difference = self.company_currency_id.round(
            sum(exchange_lines.mapped("balance")),
        )
        economic_lines = new_lines.filtered(
            lambda line: line.immediate_settlement_role
            == "payment_rate_economic",
        )
        actual_economic_adjustment = self.company_currency_id.round(
            sum(economic_lines.mapped("balance")),
        )
        if mechanism == "payment_rate":
            if exchange_moves or exchange_lines or not self.company_currency_id.is_zero(
                actual_difference,
            ):
                raise UserError(
                    _(
                        "Payment-rate settlement unexpectedly created an "
                        "exchange entry. No accounting changes were saved.",
                    ),
                )
            if economic_lines or not self.company_currency_id.is_zero(
                actual_economic_adjustment,
            ):
                raise UserError(
                    _(
                        "Payment-rate settlement created an unexpected technical "
                        "adjustment line. No accounting changes were saved.",
                    ),
                )
        elif self.company_currency_id.compare_amounts(
            actual_difference,
            eligibility["settlement_difference"],
        ):
            raise UserError(
                _(
                    "Native reconciliation produced an unexpected settlement "
                    "difference. No accounting changes were saved.",
                ),
            )
        foreign_amount_source = (
            "bank_reported"
            if eligibility["facts"].get("authoritative_foreign")
            else "document_residual"
        )
        settlement_difference_type = (
            "none"
            if mechanism == "payment_rate"
            else eligibility["settlement_difference_type"]
        )
        exchange_account = (
            self.env["account.account"]
            if mechanism == "payment_rate"
            else eligibility["exchange_account"]
        )
        settlement = (
            _as_settlement_service(
                self.env["account.immediate.settlement"],
            )
            .create(
                {
                    "name": self.env["ir.sequence"].next_by_code(
                        "account.immediate.settlement",
                    )
                    or _("New"),
                    "mechanism": mechanism,
                    "payment_rate_application": (
                        "document_reprice"
                        if mechanism == "payment_rate"
                        else False
                    ),
                    "company_id": self.company_id.id,
                    "currency_id": self.currency_id.id,
                    "document_id": self.id,
                    "document_line_ids": [Command.set(selected_lines.ids)],
                    "source_line_id_snapshot": line_id,
                    "statement_line_id": statement_line.id,
                    "bank_move_id": bank_move.id,
                    "original_statement_foreign_currency_id": (
                        original_foreign_currency.id
                    ),
                    "original_statement_foreign_amount": original_foreign_amount,
                    "original_statement_foreign_amount_source": (
                        original_foreign_source
                    ),
                    "foreign_amount": eligibility["foreign_amount"],
                    "foreign_amount_source": foreign_amount_source,
                    "company_amount": eligibility["company_amount"],
                    "reference_company_amount": eligibility["allocation"][
                        "reference_company_amount"
                    ],
                    "benchmark_company_amount": eligibility["allocation"][
                        "benchmark_company_amount"
                    ],
                    "synthetic_foreign_amount": eligibility[
                        "synthetic_foreign_amount"
                    ],
                    "preview_settlement_difference": eligibility[
                        "settlement_difference"
                    ],
                    "settlement_difference": (
                        actual_difference if mechanism == "bank_statement" else 0.0
                    ),
                    "settlement_difference_type": settlement_difference_type,
                    "exchange_account_id": exchange_account.id,
                    "exchange_line_ids": [Command.set(exchange_lines.ids)],
                    "exchange_move_ids": [Command.set(exchange_moves.ids)],
                    "exchange_move_names": ", ".join(exchange_moves.mapped("name")),
                    "economic_adjustment_amount": actual_economic_adjustment,
                    "economic_adjustment_line_ids": [
                        Command.set(economic_lines.ids),
                    ],
                    "original_invoice_currency_rate": reprice_result.get(
                        "original_rate",
                        0.0,
                    ),
                    "applied_invoice_currency_rate": reprice_result.get(
                        "applied_rate",
                        0.0,
                    ),
                    "original_document_company_amount": reprice_result.get(
                        "original_company_amount",
                        0.0,
                    ),
                    "repriced_document_company_amount": reprice_result.get(
                        "repriced_company_amount",
                        0.0,
                    ),
                    "document_revaluation_amount": (
                        reprice_result.get("repriced_company_amount", 0.0)
                        - reprice_result.get("original_company_amount", 0.0)
                    ),
                    "original_document_line_snapshot": reprice_result.get(
                        "original_snapshot",
                    ),
                    "repriced_document_line_snapshot": reprice_result.get(
                        "repriced_snapshot",
                    ),
                    "executed_rate": eligibility["executed_rate"],
                    "reference_rate": eligibility["reference_rate"],
                    "rate_deviation": eligibility["rate_deviation"],
                    "policy_date_distance": eligibility["date_distance"],
                    "policy_warning": eligibility["policy_warning"],
                    "document_date": eligibility["document_date"],
                    "payment_date": eligibility["payment_date"],
                    "settlement_date": eligibility["settlement_date"],
                    "provenance": eligibility["facts"]["provenance"],
                    "provenance_details": eligibility["facts"]["details"],
                    "trusted_source": eligibility["facts"].get(
                        "trusted_date",
                        False,
                    ),
                    "user_id": self.env.user.id,
                },
            )
        )
        _as_settlement_service(counterpart_lines).write(
            {
                "immediate_settlement_id": settlement.id,
                "immediate_settlement_role": "bank_counterpart",
            },
        )
        _as_settlement_service(exchange_lines).write(
            {
                "immediate_settlement_id": settlement.id,
                "immediate_settlement_role": "exchange_difference",
            },
        )
        _as_settlement_service(economic_lines).write(
            {"immediate_settlement_id": settlement.id},
        )
        _as_settlement_service(new_partials).write(
            {"immediate_settlement_id": settlement.id},
        )
        _as_settlement_service(statement_line).write(
            {"active_immediate_settlement_id": settlement.id},
        )
        difference_text = (
            _("no settlement difference")
            if settlement.settlement_difference_type == "none"
            else _(
                "%(amount)s FX %(kind)s",
                amount=self.company_currency_id.format(
                    abs(settlement.settlement_difference),
                ),
                kind=settlement.settlement_difference_type,
            )
        )
        if mechanism == "payment_rate":
            self.message_post(
                body=_(
                    "Payment rate applied to the document for %(foreign)s "
                    "against %(company)s. Its company-currency value changed "
                    "from %(original)s to %(repriced)s; no FX was recorded.",
                    foreign=self.currency_id.format(settlement.foreign_amount),
                    company=self.company_currency_id.format(
                        settlement.company_amount,
                    ),
                    original=self.company_currency_id.format(
                        settlement.original_document_company_amount,
                    ),
                    repriced=self.company_currency_id.format(
                        settlement.repriced_document_company_amount,
                    ),
                ),
            )
        else:
            self.message_post(
                body=_(
                    "Settled the exact document amount %(foreign)s against the "
                    "bank amount %(company)s; %(difference)s. The foreign amount "
                    "came from the selected document, not the bank feed.",
                    foreign=self.currency_id.format(settlement.foreign_amount),
                    company=self.company_currency_id.format(
                        settlement.company_amount,
                    ),
                    difference=difference_text,
                ),
            )
        bank_move.message_post(
            body=_(
                "%(method)s for %(document)s: %(foreign)s against %(company)s. "
                "Odoo's synthetic estimate %(synthetic)s was not used.",
                method=(
                    _("Payment rate")
                    if mechanism == "payment_rate"
                    else _("Exact foreign amount")
                ),
                document=self.display_name,
                foreign=self.currency_id.format(settlement.foreign_amount),
                company=self.company_currency_id.format(settlement.company_amount),
                synthetic=self.currency_id.format(
                    settlement.synthetic_foreign_amount,
                ),
            ),
        )
        return {"settlement_id": settlement.id}

    def js_settle_outstanding_line(self, line_id):
        return self._execute_foreign_settlement(line_id, "bank_statement")

    def js_use_payment_rate_outstanding_line(self, line_id):
        return self._execute_foreign_settlement(line_id, "payment_rate")

    def action_open_immediate_settlement(self):
        self.ensure_one()
        settlement = (
            self.immediate_settlement_adjustment_id
            or self.immediate_settlement_ids[:1]
            or self.env["account.immediate.settlement"].search(
                [("bank_move_id", "=", self.id)],
                order="settlement_date desc, id desc",
                limit=1,
            )
        )
        if not settlement:
            raise UserError(_("No foreign-currency settlement is linked."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Foreign-Currency Settlement"),
            "res_model": "account.immediate.settlement",
            "res_id": settlement.id,
            "view_mode": "form",
            "target": "current",
        }

    def _compute_payments_widget_to_reconcile_info(self):
        super()._compute_payments_widget_to_reconcile_info()
        for move in self:
            widget = move.invoice_outstanding_credits_debits_widget
            if not widget:
                continue
            content = [dict(line) for line in widget.get("content", [])]
            lines = {
                line.id: line
                for line in self.env["account.move.line"]
                .browse([item["id"] for item in content])
                .exists()
            }
            for item in content:
                payment_line = lines.get(item["id"])
                if not payment_line:
                    continue
                context = move._get_foreign_settlement_context(
                    payment_line,
                )
                settle = move._get_immediate_settlement_eligibility(
                    payment_line,
                )
                payment_rate = move._get_payment_rate_settlement_eligibility(
                    payment_line,
                )
                item["can_immediate_settle"] = settle["eligible"]
                item["can_use_payment_rate"] = payment_rate["eligible"]
                item["immediate_settlement_reason"] = settle["reason"]
                item["payment_rate_settlement_reason"] = payment_rate["reason"]
                item["recommended_settlement_action"] = (
                    "payment_rate"
                    if payment_rate["eligible"]
                    else "settle"
                    if settle["eligible"]
                    else False
                )
                if context["eligible"]:
                    if move.currency_id.is_zero(context["synthetic_difference"]):
                        item["add_action_helper"] = _(
                            "Use Odoo's existing %(amount)s candidate.",
                            amount=context["synthetic_label"],
                        )
                    else:
                        item["add_action_helper"] = _(
                            "Use Odoo's %(estimate)s estimate. This may leave "
                            "a %(difference)s difference.",
                            estimate=context["synthetic_label"],
                            difference=move.currency_id.format(
                                abs(context["synthetic_difference"]),
                            ),
                        )
                plausible_reason = (
                    payment_rate["reason"]
                    if payment_rate.get("plausible")
                    and not payment_rate["eligible"]
                    else settle["reason"]
                    if settle.get("plausible") and not settle["eligible"]
                    else False
                )
                item["settlement_review_reason"] = plausible_reason
            widget = dict(widget)
            widget["content"] = content
            move.invoice_outstanding_credits_debits_widget = widget

    def _compute_payments_widget_reconciled_info(self):
        super()._compute_payments_widget_reconciled_info()
        for move in self:
            widget = move.invoice_payments_widget
            if not widget:
                continue
            active_settlements = move.immediate_settlement_ids.filtered(
                lambda settlement: settlement.state == "settled",
            )
            if not active_settlements:
                continue
            content = list(widget.get("content", []))
            settlement_exchange_moves = active_settlements.exchange_move_ids
            exchange_info = dict(widget.get("exchange_info", {}))
            exchange_lines = self.env["account.move.line"].browse(
                exchange_info.get("line_ids", []),
            )
            exchange_lines = exchange_lines.filtered(
                lambda line: line.move_id not in settlement_exchange_moves,
            )
            exchange_amount = sum(exchange_lines.mapped("balance"))
            exchange_info.update(
                {
                    "line_ids": exchange_lines.ids,
                    "exchange_amount": exchange_amount,
                    "exchange_amount_formatted": formatLang(
                        self.env,
                        abs(exchange_amount),
                        currency_obj=move.company_currency_id,
                    ),
                },
            )
            comparison = move.company_currency_id.compare_amounts(
                exchange_amount,
                0,
            )
            if comparison == 0:
                exchange_info["label"] = (
                    _("See exchange information") if exchange_lines else ""
                )
            elif comparison > 0:
                exchange_info["label"] = _("Exchange Profit")
            else:
                exchange_info["label"] = _("Exchange Loss")
            for settlement in active_settlements:
                is_payment_rate = settlement.mechanism == "payment_rate"
                is_document_reprice = (
                    is_payment_rate
                    and settlement.payment_rate_application == "document_reprice"
                )
                partial_ids = set(settlement.partial_reconcile_ids.ids)
                if not any(
                    item.get("partial_id") in partial_ids for item in content
                ):
                    continue
                content = [
                    item
                    for item in content
                    if item.get("partial_id") not in partial_ids
                ]
                difference_label = (
                    _("No FX")
                    if is_payment_rate
                    else _("No settlement difference")
                    if settlement.settlement_difference_type == "none"
                    else _(
                        "%(amount)s FX %(kind)s",
                        amount=settlement.company_currency_id.format(
                            abs(settlement.settlement_difference),
                        ),
                        kind=settlement.settlement_difference_type,
                    )
                )
                foreign_label = settlement.currency_id.format(
                    settlement.foreign_amount,
                )
                company_label = settlement.company_currency_id.format(
                    settlement.company_amount,
                )
                settlement_summary = (
                    _(
                        "%(foreign)s · %(document)s %(company)s · no FX",
                        foreign=foreign_label,
                        document={
                            "in_invoice": _("Bill"),
                            "in_refund": _("Vendor credit"),
                            "in_receipt": _("Purchase receipt"),
                            "out_invoice": _("Invoice"),
                            "out_refund": _("Credit note"),
                            "out_receipt": _("Sales receipt"),
                        }.get(move.move_type, _("Document")),
                        company=company_label,
                    )
                    if is_document_reprice
                    else
                    _(
                        "%(foreign)s · %(company)s · no FX",
                        foreign=foreign_label,
                        company=company_label,
                    )
                    if is_payment_rate
                    else _(
                        "%(foreign)s · %(difference)s",
                        foreign=foreign_label,
                        difference=difference_label,
                    )
                )
                content.append(
                    {
                        "name": (
                            _("Payment-rate settlement")
                            if is_payment_rate
                            else _("Exact foreign-amount settlement")
                        ),
                        "journal_name": settlement.bank_move_id.journal_id.name,
                        "amount": settlement.foreign_amount,
                        "currency_id": settlement.currency_id.id,
                        "date": settlement.settlement_date,
                        "partial_id": settlement.partial_reconcile_ids[:1].id,
                        "move_id": settlement.bank_move_id.id,
                        "ref": settlement.name,
                        "amount_company_currency": formatLang(
                            self.env,
                            settlement.company_amount,
                            currency_obj=settlement.company_currency_id,
                        ),
                        "amount_foreign_currency": formatLang(
                            self.env,
                            settlement.foreign_amount,
                            currency_obj=settlement.currency_id,
                        ),
                        "is_exchange": False,
                        "is_refund": False,
                        "is_immediate_settlement": True,
                        "is_payment_rate_settlement": is_payment_rate,
                        "is_document_reprice": is_document_reprice,
                        "immediate_settlement_id": settlement.id,
                        "settlement_summary": settlement_summary,
                        "settlement_method": (
                            _("Use payment rate")
                            if is_payment_rate
                            else _("Settle")
                        ),
                        "executed_pair": _(
                            "%(foreign)s from the document = %(company)s "
                            "reported on the bank statement",
                            foreign=settlement.currency_id.format(
                                settlement.foreign_amount,
                            ),
                            company=settlement.company_currency_id.format(
                                settlement.company_amount,
                            ),
                        ),
                        "synthetic_estimate": settlement.currency_id.format(
                            settlement.synthetic_foreign_amount,
                        ),
                        "carrying_value": settlement.company_currency_id.format(
                            settlement.reference_company_amount,
                        ),
                        "settlement_difference_label": difference_label,
                        "economic_adjustment_label": (
                            settlement.company_currency_id.format(
                                abs(settlement.economic_adjustment_amount),
                            )
                            if is_payment_rate and not is_document_reprice
                            else _("None")
                        ),
                        "economic_account_names": ", ".join(
                            settlement.allocation_ids.mapped(
                                "account_id_snapshot.display_name",
                            ),
                        ),
                        "original_document_value": (
                            settlement.company_currency_id.format(
                                settlement.original_document_company_amount,
                            )
                            if is_document_reprice
                            else False
                        ),
                        "repriced_document_value": (
                            settlement.company_currency_id.format(
                                settlement.repriced_document_company_amount,
                            )
                            if is_document_reprice
                            else False
                        ),
                        "document_revaluation_label": (
                            settlement.company_currency_id.format(
                                abs(settlement.document_revaluation_amount),
                            )
                            if is_document_reprice
                            else False
                        ),
                        "original_invoice_currency_rate": (
                            settlement.original_invoice_currency_rate
                            if is_document_reprice
                            else False
                        ),
                        "applied_invoice_currency_rate": (
                            settlement.applied_invoice_currency_rate
                            if is_document_reprice
                            else False
                        ),
                        "exchange_account_name": (
                            settlement.exchange_account_id.display_name
                            if settlement.exchange_account_id
                            else _("None")
                        ),
                        "exchange_move_names": settlement.exchange_move_names,
                        "executed_rate": settlement.executed_rate,
                        "reference_rate": settlement.reference_rate,
                        "rate_deviation": settlement.rate_deviation,
                        "provenance": _(
                            "%(company_currency)s amount from bank statement; "
                            "foreign amount from selected document residual",
                            company_currency=(
                                settlement.company_currency_id.display_name
                            ),
                        ),
                    },
                )
            widget = dict(widget)
            widget["content"] = sorted(
                content,
                key=lambda item: item.get("date") or fields.Date.today(),
            )
            widget["exchange_info"] = exchange_info
            move.invoice_payments_widget = widget

    def _immediate_settlement_protected_moves(self):
        settlements = self.env["account.immediate.settlement"].search(
            [
                ("state", "=", "settled"),
                "|",
                "|",
                ("bank_move_id", "in", self.ids),
                ("exchange_move_ids", "in", self.ids),
                "&",
                ("document_id", "in", self.ids),
                ("payment_rate_application", "=", "document_reprice"),
            ],
        )
        return self.filtered(
            lambda move: (
                move.immediate_settlement_adjustment_id
                or move in settlements.bank_move_id
                or move in settlements.exchange_move_ids
                or move in settlements.document_id
            ),
        )

    def write(self, vals):
        protected_fields = {
            "invoice_currency_rate",
            "line_ids",
            "invoice_line_ids",
            "currency_id",
            "date",
            "invoice_date",
        } & set(vals)
        internal = (
            self.env.context.get("immediate_settlement_internal_token")
            is _INTERNAL_SETTLEMENT_TOKEN
        )
        if (
            protected_fields
            and not internal
            and self._immediate_settlement_protected_moves()
        ):
            raise UserError(
                _(
                    "An active foreign-currency settlement protects this "
                    "accounting valuation. Undo the settlement first.",
                ),
            )
        return super().write(vals)

    def button_draft(self):
        protected = self._immediate_settlement_protected_moves()
        internal = (
            self.env.context.get("immediate_settlement_internal_token")
            is _INTERNAL_SETTLEMENT_TOKEN
        )
        if protected and not internal:
            raise UserError(
                _(
                    "Settlement accounting entries cannot be reset to draft "
                    "directly. Undo the linked settlement first.",
                ),
            )
        return super().button_draft()

    def button_cancel(self):
        protected = self._immediate_settlement_protected_moves()
        internal = (
            self.env.context.get("immediate_settlement_internal_token")
            is _INTERNAL_SETTLEMENT_TOKEN
        )
        if protected and not internal:
            raise UserError(
                _(
                    "Undo the linked settlement instead of cancelling its "
                    "accounting entry.",
                ),
            )
        return super().button_cancel()

    @api.ondelete(at_uninstall=False)
    def _unlink_immediate_settlement_adjustment(self):
        internal = (
            self.env.context.get("immediate_settlement_internal_token")
            is _INTERNAL_SETTLEMENT_TOKEN
        )
        if self._immediate_settlement_protected_moves() and not internal:
            raise UserError(
                _(
                    "Settlement accounting entries cannot be deleted directly. "
                    "Undo the linked settlement instead.",
                ),
            )
