from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import formatLang

_INTERNAL_REVERSAL_TOKEN = object()


class ResCompany(models.Model):
    _inherit = "res.company"

    immediate_settlement_max_days = fields.Integer(
        string="Exact-amount settlement maximum delay",
        default=3,
        help=(
            "Maximum calendar-day gap between a foreign-currency document and "
            "a bank transaction whose foreign amount is inferred from it."
        ),
    )
    immediate_settlement_max_rate_deviation = fields.Float(
        string="Exact-amount settlement maximum rate deviation (%)",
        default=3.0,
        digits=(12, 4),
        help=(
            "Maximum deviation between the bank/document inferred rate and "
            "Odoo's reference rate on the bank transaction date."
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
                    _("The exact-amount settlement delay cannot be negative."),
                )
            if not 0 <= company.immediate_settlement_max_rate_deviation <= 100:
                raise UserError(
                    _(
                        "The exact-amount settlement rate deviation must be "
                        "between 0% and 100%.",
                    ),
                )


class AccountJournal(models.Model):
    _inherit = "account.journal"

    immediate_settlement_policy_override = fields.Boolean(
        string="Override exact-amount settlement policy",
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
                    _("The exact-amount settlement delay cannot be negative."),
                )
            if not 0 <= journal.immediate_settlement_max_rate_deviation <= 100:
                raise UserError(
                    _(
                        "The exact-amount settlement rate deviation must be "
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
    _description = "Exact Foreign-Amount Settlement"
    _order = "settlement_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, readonly=True, copy=False)
    mechanism = fields.Selection(
        [
            ("bank_statement", "Exact bank-statement foreign amount"),
            ("legacy_adjustment", "Legacy payment-rate adjustment"),
        ],
        required=True,
        default="bank_statement",
        readonly=True,
        index=True,
        copy=False,
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
        [("document_residual", "Selected document residual")],
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
        if not self.env.su:
            raise AccessError(
                _("Settlement audit records can only be created by the service."),
            )
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
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
                immediate_settlement_internal_token=_INTERNAL_REVERSAL_TOKEN,
            ).unreconcile_bank_line()
            statement_line.with_context(
                immediate_settlement_internal_token=_INTERNAL_REVERSAL_TOKEN,
                rebuild_skip_partner_inference=True,
            ).write(
                {
                    "foreign_currency_id": False,
                    "amount_currency": 0.0,
                    "immediate_settlement_foreign_amount_source": False,
                    "immediate_settlement_document_id": False,
                    "active_immediate_settlement_id": False,
                },
            )

    def _reverse_legacy_settlement(self):
        for settlement in self:
            partials = settlement.partial_reconcile_ids
            if partials:
                partials.with_context(
                    immediate_settlement_internal_token=_INTERNAL_REVERSAL_TOKEN,
                ).unlink()
            reversal = self.env["account.move"]
            if settlement.adjustment_move_id:
                reversal = settlement.adjustment_move_id.with_context(
                    immediate_settlement_internal_token=_INTERNAL_REVERSAL_TOKEN,
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
            settlement.sudo().write({"reversal_move_id": reversal.id})

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
            if settlement.mechanism == "bank_statement":
                settlement._reverse_bank_statement_settlement()
            else:
                settlement._reverse_legacy_settlement()
            settlement.sudo().write(
                {
                    "state": "reversed",
                    "reversed_user_id": self.env.user.id,
                    "reversed_at": fields.Datetime.now(),
                },
            )
            settlement.document_id.message_post(
                body=_(
                    "Exact foreign-amount settlement %(name)s was reversed.",
                    name=settlement.name,
                ),
            )
        return True


class AccountImmediateSettlementAllocation(models.Model):
    """Read-only compatibility model for preview-era allocation records."""

    _name = "account.immediate.settlement.allocation"
    _description = "Legacy Immediate Settlement Allocation"
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
        required=True,
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
        if not self.env.su:
            raise AccessError(_("Legacy settlement allocations are read-only."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise AccessError(_("Legacy settlement allocations are read-only."))
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
        return (
            self.env.context.get("immediate_settlement_internal_token")
            is _INTERNAL_REVERSAL_TOKEN
        )

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
            ("suspense_clear", "Legacy suspense clearing"),
            ("payment_bridge", "Legacy payment bridge"),
            ("valuation", "Legacy document valuation"),
            ("economic", "Legacy economic allocation"),
        ],
        readonly=True,
        copy=False,
        index=True,
    )

    def write(self, vals):
        protected = {"immediate_settlement_id", "immediate_settlement_role"} & set(vals)
        if protected and not self.env.su:
            raise AccessError(_("Settlement trace fields are service-managed."))
        return super().write(vals)


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
        if "immediate_settlement_id" in vals and not self.env.su:
            raise AccessError(_("Settlement trace fields are service-managed."))
        return super().write(vals)

    def unlink(self):
        settlements = self.immediate_settlement_id.filtered(
            lambda settlement: settlement.state == "settled",
        )
        internal = (
            self.env.context.get("immediate_settlement_internal_token")
            is _INTERNAL_REVERSAL_TOKEN
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
        max_deviation,
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
            if deviation > max_deviation:
                continue
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

    def _get_immediate_settlement_eligibility(
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
                _("Settle is available only for imported bank transactions."),
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
        if facts.get("authoritative_foreign"):
            return blocked(
                _(
                    "The bank transaction already contains an authoritative "
                    "foreign amount. Use Add for standard reconciliation.",
                ),
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
        if statement_line.foreign_currency_id or statement_line.amount_currency:
            return blocked(
                _(
                    "The bank transaction already contains foreign-currency "
                    "data. Review it in Bank Matching.",
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
        if not facts.get("trusted_date") and date_distance > policy["max_days"]:
            return blocked(
                _(
                    "The bank transaction is %(days)s days from the document, "
                    "above the %(maximum)s-day exact-settlement policy.",
                    days=date_distance,
                    maximum=policy["max_days"],
                ),
                plausible=True,
            )

        candidates = self._immediate_settlement_term_candidates(
            company_amount,
            transaction_date,
            policy["max_deviation"],
        )
        if not candidates:
            return blocked(
                _(
                    "The bank amount does not identify a document residual or "
                    "payment term within the %(maximum).2f%% rate policy.",
                    maximum=policy["max_deviation"],
                ),
                plausible=True,
            )
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
        if difference_type != "none" and not exchange_account:
            return blocked(
                _("Configure Odoo's currency exchange accounts before settlement."),
                plausible=True,
            )

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
        foreign_label = self.currency_id.format(foreign_amount)
        company_label = self.company_currency_id.format(company_amount_abs)
        synthetic_label = self.currency_id.format(synthetic_foreign_amount)
        if difference_type == "none":
            difference_preview = _("No settlement difference expected.")
            action_preview = _(
                "Settle %(foreign)s against %(company)s · no settlement difference",
                foreign=foreign_label,
                company=company_label,
            )
        else:
            difference_label = self.company_currency_id.format(
                abs(settlement_difference),
            )
            difference_preview = _(
                "A %(difference)s FX %(kind)s will be recorded.",
                difference=difference_label,
                kind=difference_type,
            )
            action_preview = _(
                "Settle %(foreign)s against %(company)s · records "
                "%(difference)s FX %(kind)s",
                foreign=foreign_label,
                company=company_label,
                difference=difference_label,
                kind=difference_type,
            )
        return {
            "eligible": True,
            "plausible": True,
            "reason": _(
                "Match the exact document amount against the actual bank amount "
                "and record the resulting company-currency settlement difference.",
            ),
            "preview": action_preview,
            "difference_preview": difference_preview,
            "synthetic_preview": _(
                "Odoo estimated payment: %(amount)s",
                amount=synthetic_label,
            ),
            "confidence": "high" if date_distance <= 1 else "normal",
            "facts": facts,
            "allocation": allocation,
            "foreign_amount": foreign_amount,
            "company_amount": company_amount_abs,
            "synthetic_foreign_amount": synthetic_foreign_amount,
            "settlement_difference": settlement_difference,
            "settlement_difference_type": difference_type,
            "exchange_account": exchange_account,
            "executed_rate": executed_rate,
            "reference_rate": reference_rate,
            "rate_deviation": allocation["rate_deviation"],
            "document_date": document_date,
            "payment_date": transaction_date,
            "settlement_date": transaction_date,
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
            data += line_data
        return statement_line._recompute_suspense_line(
            data,
            reconcile_auxiliary_id,
            statement_line.manual_reference,
        )

    def _lock_exact_settlement_records(self, payment_line):
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

    def js_settle_outstanding_line(self, line_id):
        self.ensure_one()
        if not self.env.user.has_group("account.group_account_user"):
            raise AccessError(
                _("Only accountants can settle an exact foreign amount."),
            )
        self.check_access("write")
        existing = self.env["account.immediate.settlement"].search(
            [
                ("document_id", "=", self.id),
                ("source_line_id_snapshot", "=", line_id),
                ("state", "=", "settled"),
            ],
            limit=1,
        )
        if existing:
            return {"settlement_id": existing.id}
        payment_line = self.env["account.move.line"].browse(line_id).exists()
        if not payment_line:
            raise UserError(
                _("This bank transaction no longer exists. Refresh the document."),
            )
        statement_line = payment_line.move_id.statement_line_id
        if statement_line:
            statement_line.check_access("write")
        self._lock_exact_settlement_records(payment_line)
        self.invalidate_recordset()
        payment_line.invalidate_recordset()
        payment_line = payment_line.exists()
        existing = self.env["account.immediate.settlement"].search(
            [
                ("document_id", "=", self.id),
                ("source_line_id_snapshot", "=", line_id),
                ("state", "=", "settled"),
            ],
            limit=1,
        )
        if existing:
            return {"settlement_id": existing.id}
        if not payment_line:
            raise UserError(
                _("This bank transaction changed while settling. Refresh the document."),
            )
        eligibility = self._get_immediate_settlement_eligibility(
            payment_line,
            raise_exception=True,
        )
        statement_line = eligibility["facts"]["statement_line"]
        bank_move = statement_line.move_id
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
        original_line_ids = set(bank_move.line_ids.ids)
        original_partial_ids = set(
            (
                eligibility["allocation"]["lines"].matched_debit_ids
                + eligibility["allocation"]["lines"].matched_credit_ids
            ).ids,
        )
        signed_foreign_amount = (
            eligibility["foreign_amount"]
            if statement_line.amount > 0
            else -eligibility["foreign_amount"]
        )
        partner_vals = {}
        if statement_line.partner_id != self.commercial_partner_id:
            partner_vals["partner_id"] = self.commercial_partner_id.id
        statement_line.with_context(
            rebuild_skip_partner_inference=True,
        ).write(
            {
                **partner_vals,
                "foreign_currency_id": self.currency_id.id,
                "amount_currency": signed_foreign_amount,
                "immediate_settlement_foreign_amount_source": (
                    "document_residual"
                ),
                "immediate_settlement_document_id": self.id,
            },
        )
        reconcile_data = self._prepare_exact_settlement_reconcile_data(
            statement_line,
            eligibility,
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
        statement_line._reconcile_bank_line_edit(
            statement_line._prepare_reconcile_line_data(
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
                _("Settlement attempted to change the imported bank liquidity amount."),
            )
        _liquidity, suspense_lines, other_lines = statement_line._seek_for_lines()
        if suspense_lines or not statement_line.is_reconciled:
            raise UserError(
                _("Settlement did not fully clear the bank suspense balance."),
            )
        selected_lines = eligibility["allocation"]["lines"]
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
        new_partials = (
            selected_lines.matched_debit_ids + selected_lines.matched_credit_ids
        ).filtered(lambda partial: partial.id not in original_partial_ids)
        new_lines = other_lines.filtered(lambda line: line.id not in original_line_ids)
        counterpart_lines = new_lines.filtered(
            lambda line: (
                line.account_id == eligibility["allocation"]["account"]
                and line.currency_id == self.currency_id
                and self.currency_id.compare_amounts(
                    abs(line.amount_currency),
                    eligibility["foreign_amount"],
                )
                == 0
            ),
        )
        exchange_moves = new_partials.exchange_move_id
        exchange_lines = exchange_moves.line_ids.filtered(
            lambda line: line.account_id == eligibility["exchange_account"],
        )
        actual_difference = self.company_currency_id.round(
            sum(exchange_lines.mapped("balance")),
        )
        if self.company_currency_id.compare_amounts(
            actual_difference,
            eligibility["settlement_difference"],
        ):
            raise UserError(
                _(
                    "Native reconciliation produced an unexpected settlement "
                    "difference. No accounting changes were saved.",
                ),
            )
        settlement = (
            self.env["account.immediate.settlement"]
            .sudo()
            .create(
                {
                    "name": self.env["ir.sequence"].next_by_code(
                        "account.immediate.settlement",
                    )
                    or _("New"),
                    "mechanism": "bank_statement",
                    "company_id": self.company_id.id,
                    "currency_id": self.currency_id.id,
                    "document_id": self.id,
                    "document_line_ids": [Command.set(selected_lines.ids)],
                    "source_line_id_snapshot": line_id,
                    "statement_line_id": statement_line.id,
                    "bank_move_id": bank_move.id,
                    "foreign_amount": eligibility["foreign_amount"],
                    "foreign_amount_source": "document_residual",
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
                    "settlement_difference": actual_difference,
                    "settlement_difference_type": eligibility[
                        "settlement_difference_type"
                    ],
                    "exchange_account_id": eligibility["exchange_account"].id,
                    "exchange_line_ids": [Command.set(exchange_lines.ids)],
                    "exchange_move_ids": [Command.set(exchange_moves.ids)],
                    "exchange_move_names": ", ".join(exchange_moves.mapped("name")),
                    "executed_rate": eligibility["executed_rate"],
                    "reference_rate": eligibility["reference_rate"],
                    "rate_deviation": eligibility["rate_deviation"],
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
        counterpart_lines.sudo().write(
            {
                "immediate_settlement_id": settlement.id,
                "immediate_settlement_role": "bank_counterpart",
            },
        )
        exchange_lines.sudo().write(
            {
                "immediate_settlement_id": settlement.id,
                "immediate_settlement_role": "exchange_difference",
            },
        )
        new_partials.sudo().write({"immediate_settlement_id": settlement.id})
        statement_line.sudo().write(
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
        self.message_post(
            body=_(
                "Settled the exact document amount %(foreign)s against the "
                "bank amount %(company)s; %(difference)s. The foreign amount "
                "came from the selected document, not the bank feed.",
                foreign=self.currency_id.format(settlement.foreign_amount),
                company=self.company_currency_id.format(settlement.company_amount),
                difference=difference_text,
            ),
        )
        bank_move.message_post(
            body=_(
                "Exact foreign amount %(foreign)s inferred from %(document)s. "
                "Odoo's synthetic estimate %(synthetic)s was not used.",
                foreign=self.currency_id.format(settlement.foreign_amount),
                document=self.display_name,
                synthetic=self.currency_id.format(
                    settlement.synthetic_foreign_amount,
                ),
            ),
        )
        return {"settlement_id": settlement.id}

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
            raise UserError(_("No exact foreign-amount settlement is linked."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Exact Foreign-Amount Settlement"),
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
                eligibility = move._get_immediate_settlement_eligibility(
                    payment_line,
                )
                item["can_immediate_settle"] = eligibility["eligible"]
                item["immediate_settlement_reason"] = eligibility["reason"]
                item["immediate_settlement_confidence"] = eligibility.get(
                    "confidence",
                )
                item["immediate_settlement_preview"] = eligibility.get("preview")
                item["immediate_settlement_difference_preview"] = eligibility.get(
                    "difference_preview",
                )
                item["immediate_settlement_synthetic_preview"] = eligibility.get(
                    "synthetic_preview",
                )
                item["show_immediate_settlement_reason"] = eligibility.get(
                    "plausible",
                    False,
                )
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
                    _("No settlement difference")
                    if settlement.settlement_difference_type == "none"
                    else _(
                        "%(amount)s FX %(kind)s",
                        amount=settlement.company_currency_id.format(
                            abs(settlement.settlement_difference),
                        ),
                        kind=settlement.settlement_difference_type,
                    )
                )
                content.append(
                    {
                        "name": _("Exact foreign-amount settlement"),
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
                        "immediate_settlement_id": settlement.id,
                        "settlement_summary": _(
                            "%(foreign)s · %(difference)s",
                            foreign=settlement.currency_id.format(
                                settlement.foreign_amount,
                            ),
                            difference=difference_label,
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

    def button_draft(self):
        protected = self.filtered("immediate_settlement_adjustment_id")
        internal = (
            self.env.context.get("immediate_settlement_internal_token")
            is _INTERNAL_REVERSAL_TOKEN
        )
        if protected and not internal:
            raise UserError(
                _(
                    "Reverse the linked legacy settlement instead of resetting "
                    "its adjustment to draft.",
                ),
            )
        return super().button_draft()

    def button_cancel(self):
        protected = self.filtered("immediate_settlement_adjustment_id")
        internal = (
            self.env.context.get("immediate_settlement_internal_token")
            is _INTERNAL_REVERSAL_TOKEN
        )
        if protected and not internal:
            raise UserError(
                _(
                    "Reverse the linked legacy settlement instead of cancelling "
                    "its adjustment.",
                ),
            )
        return super().button_cancel()

    @api.ondelete(at_uninstall=False)
    def _unlink_immediate_settlement_adjustment(self):
        internal = (
            self.env.context.get("immediate_settlement_internal_token")
            is _INTERNAL_REVERSAL_TOKEN
        )
        if self.immediate_settlement_adjustment_id and not internal:
            raise UserError(
                _(
                    "Legacy settlement adjustments cannot be deleted. Reverse "
                    "the settlement instead.",
                ),
            )
