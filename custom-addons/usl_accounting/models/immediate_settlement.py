from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError

ECONOMIC_DISPLAY_TYPES = {
    "product",
    "discount",
    "rounding",
    "epd",
    "non_deductible_product",
}
EXCLUDED_ACCOUNT_TYPES = {
    "asset_cash",
    "asset_receivable",
    "liability_credit_card",
    "liability_payable",
    "off_balance",
}


class ResCompany(models.Model):
    _inherit = "res.company"

    immediate_settlement_max_days = fields.Integer(
        string="Immediate settlement maximum delay",
        default=3,
        help="Maximum calendar-day gap between a document and its immediate payment.",
    )
    immediate_settlement_max_rate_deviation = fields.Float(
        string="Immediate settlement maximum rate deviation (%)",
        default=3.0,
        digits=(12, 4),
        help=(
            "Maximum percentage deviation between the document reference rate "
            "and the payment's executed rate."
        ),
    )
    immediate_settlement_journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Immediate settlement journal",
        check_company=True,
        domain="[('type', '=', 'general'), ('company_id', '=', id)]",
    )
    immediate_settlement_fee_account_ids = fields.Many2many(
        comodel_name="account.account",
        relation="res_company_immediate_settlement_fee_account_rel",
        column1="company_id",
        column2="account_id",
        string="Explicit fee accounts",
        check_company=True,
        help=(
            "Separate fee lines on these accounts do not prevent immediate "
            "settlement. They are never included in the executed rate."
        ),
    )

    @api.model_create_multi
    def create(self, vals_list):
        companies = super().create(vals_list)
        companies.sudo()._ensure_immediate_settlement_journal()
        return companies

    @api.constrains(
        "immediate_settlement_max_days",
        "immediate_settlement_max_rate_deviation",
    )
    def _check_immediate_settlement_policy(self):
        for company in self:
            if company.immediate_settlement_max_days < 0:
                raise UserError(
                    _("The immediate settlement maximum delay cannot be negative."),
                )
            if not 0 <= company.immediate_settlement_max_rate_deviation <= 100:
                raise UserError(
                    _(
                        "The immediate settlement rate deviation must be "
                        "between 0% and 100%.",
                    ),
                )

    def _ensure_immediate_settlement_journal(self):
        for company in self:
            if company.immediate_settlement_journal_id:
                continue
            base_code = "IMST"
            code = base_code
            sequence = 1
            while self.env["account.journal"].search_count(
                [("company_id", "=", company.id), ("code", "=", code)],
                limit=1,
            ):
                sequence += 1
                code = f"IM{sequence:02d}"[-5:]
            journal = self.env["account.journal"].create(
                {
                    "name": _("Immediate Settlements"),
                    "code": code,
                    "type": "general",
                    "company_id": company.id,
                },
            )
            company.immediate_settlement_journal_id = journal
        return self.mapped("immediate_settlement_journal_id")


class AccountJournal(models.Model):
    _inherit = "account.journal"

    immediate_settlement_policy_override = fields.Boolean(
        string="Override immediate settlement policy",
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
                    _("The immediate settlement maximum delay cannot be negative."),
                )
            if not 0 <= journal.immediate_settlement_max_rate_deviation <= 100:
                raise UserError(
                    _(
                        "The immediate settlement rate deviation must be "
                        "between 0% and 100%.",
                    ),
                )


class AccountChartTemplate(models.AbstractModel):
    _inherit = "account.chart.template"

    def _post_load_data(self, template_code, company, template_data):
        result = super()._post_load_data(template_code, company, template_data)
        company._ensure_immediate_settlement_journal()
        return result


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
    immediate_settlement_journal_id = fields.Many2one(
        related="company_id.immediate_settlement_journal_id",
        readonly=False,
    )
    immediate_settlement_fee_account_ids = fields.Many2many(
        related="company_id.immediate_settlement_fee_account_ids",
        readonly=False,
    )


class AccountImmediateSettlement(models.Model):
    _name = "account.immediate.settlement"
    _description = "Immediate Settlement"
    _order = "settlement_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, readonly=True, copy=False)
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
    payment_line_id = fields.Many2one(
        "account.move.line",
        required=True,
        readonly=True,
        check_company=True,
        index=True,
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
    )
    document_line_ids = fields.Many2many(
        "account.move.line",
        relation="account_immediate_settlement_document_line_rel",
        column1="settlement_id",
        column2="line_id",
        readonly=True,
        check_company=True,
    )
    foreign_amount = fields.Monetary(
        currency_field="currency_id",
        required=True,
        readonly=True,
    )
    company_amount = fields.Monetary(
        currency_field="company_currency_id",
        required=True,
        readonly=True,
    )
    reference_company_amount = fields.Monetary(
        currency_field="company_currency_id",
        required=True,
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
    partial_reconcile_ids = fields.One2many(
        "account.partial.reconcile",
        "immediate_settlement_id",
        readonly=True,
    )
    allocation_ids = fields.One2many(
        "account.immediate.settlement.allocation",
        "settlement_id",
        readonly=True,
    )

    @api.ondelete(at_uninstall=False)
    def _unlink_except_module_uninstall(self):
        raise UserError(
            _("Immediate settlement audit records cannot be deleted."),
        )

    def write(self, vals):
        if not self.env.context.get("immediate_settlement_internal"):
            raise UserError(
                _("Immediate settlement audit records cannot be edited."),
            )
        return super().write(vals)

    def _check_reversal_lock_dates(self):
        for settlement in self:
            dates = {
                settlement.document_date,
                settlement.payment_date,
                settlement.settlement_date,
            }
            violations = []
            for accounting_date in dates:
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
                        "This immediate settlement cannot be reversed because "
                        "its accounting period is locked: %(locks)s.",
                        locks=settlement.company_id._format_lock_dates(
                            list(set(violations)),
                        ),
                    ),
                )

    def action_reverse(self):
        for settlement in self:
            if settlement.state == "reversed":
                continue
            settlement._check_reversal_lock_dates()
            partials = settlement.partial_reconcile_ids
            if partials:
                partials.with_context(
                    immediate_settlement_reversal=True,
                ).unlink()
            reversal = self.env["account.move"]
            if settlement.adjustment_move_id:
                reversal = settlement.adjustment_move_id.with_context(
                    immediate_settlement_reversal=True,
                )._reverse_moves(
                    [
                        {
                            "date": fields.Date.context_today(settlement),
                            "ref": _(
                                "Reversal of immediate settlement %(name)s",
                                name=settlement.name,
                            ),
                        },
                    ],
                    cancel=True,
                )
            settlement.sudo().with_context(
                immediate_settlement_internal=True,
            ).write(
                {
                    "state": "reversed",
                    "reversal_move_id": reversal.id,
                },
            )
            settlement.document_id.message_post(
                body=_(
                    "Immediate settlement %(name)s was reversed.",
                    name=settlement.name,
                ),
            )
        return True


class AccountImmediateSettlementAllocation(models.Model):
    _name = "account.immediate.settlement.allocation"
    _description = "Immediate Settlement Allocation"
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
            ("suspense_clear", "Suspense clearing"),
            ("payment_bridge", "Payment bridge"),
            ("valuation", "Document valuation"),
            ("economic", "Economic allocation"),
        ],
        readonly=True,
        copy=False,
        index=True,
    )


class AccountPartialReconcile(models.Model):
    _inherit = "account.partial.reconcile"

    immediate_settlement_id = fields.Many2one(
        "account.immediate.settlement",
        readonly=True,
        copy=False,
        index=True,
        check_company=True,
    )

    def unlink(self):
        settlements = self.immediate_settlement_id.filtered(
            lambda settlement: settlement.state == "settled",
        )
        if settlements and not self.env.context.get("immediate_settlement_reversal"):
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
    immediate_settlement_adjustment_id = fields.Many2one(
        "account.immediate.settlement",
        readonly=True,
        copy=False,
        index=True,
        check_company=True,
    )

    def _get_immediate_settlement_source_facts(self, payment_line):
        """Extension hook for trusted server-side transaction sources.

        Integrations may override this method and return the same keys. A
        trusted source may replace date inference only; all monetary,
        currency, policy, lock, company, and permission checks remain active.
        """
        self.ensure_one()
        statement_line = payment_line.move_id.statement_line_id
        is_suspense = bool(
            statement_line
            and payment_line.account_id
            == statement_line.journal_id.suspense_account_id,
        )
        if is_suspense:
            (
                foreign_amount,
                statement_company_amount,
            ) = statement_line._get_statement_line_residual_amounts()
            company_amount = (
                payment_line.amount_residual
                if not self.company_currency_id.is_zero(
                    payment_line.amount_residual,
                )
                else statement_company_amount
            )
            currency = (
                statement_line.foreign_currency_id
                or payment_line.currency_id
            )
        else:
            currency = payment_line.currency_id
            foreign_amount = payment_line.amount_residual_currency
            company_amount = payment_line.amount_residual
        return {
            "currency": currency,
            "foreign_amount": foreign_amount,
            "company_amount": company_amount,
            "statement_line": statement_line,
            "trusted": False,
            "provenance": (
                "bank_statement"
                if statement_line
                else "odoo_payment"
                if payment_line.payment_id
                else "journal_item"
            ),
            "details": {},
        }

    def _get_immediate_settlement_policy(self, payment_line):
        self.ensure_one()
        journal = payment_line.journal_id
        if journal.immediate_settlement_policy_override:
            return {
                "max_days": journal.immediate_settlement_max_days,
                "max_deviation": (journal.immediate_settlement_max_rate_deviation),
            }
        return {
            "max_days": self.company_id.immediate_settlement_max_days,
            "max_deviation": (self.company_id.immediate_settlement_max_rate_deviation),
        }

    def _immediate_settlement_economic_lines(self):
        self.ensure_one()
        excluded_accounts = self.company_id.immediate_settlement_fee_account_ids
        lines = self.line_ids.filtered(
            lambda line: (
                line.display_type in ECONOMIC_DISPLAY_TYPES
                and not line.tax_line_id
                and line.account_id.account_type not in EXCLUDED_ACCOUNT_TYPES
                and line.account_id not in excluded_accounts
                and not self.company_currency_id.is_zero(line.balance)
            ),
        )
        if not lines:
            return lines
        signs = {1 if line.balance > 0 else -1 for line in lines}
        return lines if len(signs) == 1 else self.env["account.move.line"]

    def _immediate_settlement_document_allocation(
        self,
        source_foreign_amount,
    ):
        self.ensure_one()
        term_lines = self.line_ids.filtered(
            lambda line: (
                line.account_id.account_type
                in ("asset_receivable", "liability_payable")
                and not line.reconciled
                and line.currency_id == self.currency_id
                and not self.currency_id.is_zero(
                    line.amount_residual_currency,
                )
            ),
        )
        if not term_lines or len(term_lines.account_id) != 1:
            return False

        available = abs(source_foreign_amount)
        total = sum(abs(line.amount_residual_currency) for line in term_lines)
        if self.currency_id.compare_amounts(available, total) >= 0:
            selected_lines = term_lines
            foreign_amount = total
        elif len(term_lines) == 1:
            selected_lines = term_lines
            foreign_amount = available
        else:
            matching_terms = term_lines.filtered(
                lambda line: (
                    self.currency_id.compare_amounts(
                        abs(line.amount_residual_currency),
                        available,
                    )
                    == 0
                ),
            )
            if len(matching_terms) != 1:
                return False
            selected_lines = matching_terms
            foreign_amount = available

        reference_company_amount = 0.0
        remaining_foreign = foreign_amount
        for line in selected_lines:
            line_foreign = abs(line.amount_residual_currency)
            allocated_foreign = min(line_foreign, remaining_foreign)
            reference_company_amount += (
                abs(line.amount_residual) * allocated_foreign / line_foreign
            )
            remaining_foreign -= allocated_foreign
        return {
            "lines": selected_lines,
            "account": selected_lines.account_id,
            "foreign_amount": foreign_amount,
            "reference_company_amount": (
                self.company_currency_id.round(reference_company_amount)
            ),
        }

    def _immediate_settlement_fee_lines_are_explained(
        self,
        payment_line,
        statement_line,
    ):
        self.ensure_one()
        if statement_line:
            _liquidity, _suspense, other_lines = statement_line._seek_for_lines()
            other_lines -= payment_line
        elif payment_line.payment_id:
            _liquidity, counterpart_lines, writeoff_lines = (
                payment_line.payment_id._seek_for_lines()
            )
            other_lines = writeoff_lines + counterpart_lines - payment_line
        else:
            other_lines = (payment_line.move_id.line_ids - payment_line).filtered(
                lambda line: (
                    line.account_id.account_type
                    not in ("asset_cash", "liability_credit_card")
                    and not self.company_currency_id.is_zero(line.balance)
                ),
            )
        if not other_lines:
            return True
        excluded_accounts = self.company_id.immediate_settlement_fee_account_ids
        return bool(excluded_accounts) and all(
            line.account_id in excluded_accounts for line in other_lines
        )

    def _get_immediate_settlement_eligibility(
        self,
        payment_line,
        *,
        raise_exception=False,
    ):
        self.ensure_one()

        def blocked(reason):
            if raise_exception:
                raise UserError(reason)
            return {"eligible": False, "reason": reason}

        if not self.env.user.has_group("account.group_account_user"):
            return blocked(
                _("Only accountants can settle at the payment rate."),
            )
        if (
            self.state != "posted"
            or not self.is_invoice(include_receipts=True)
            or self.payment_state not in ("not_paid", "partial")
        ):
            return blocked(_("The document must be posted and still open."))
        if not payment_line.exists() or payment_line.parent_state != "posted":
            return blocked(_("The selected payment is no longer posted."))
        if payment_line.company_id != self.company_id:
            return blocked(
                _("The document and payment must belong to the same company."),
            )
        if payment_line.reconciled:
            return blocked(_("The selected payment is already reconciled."))
        if self.currency_id == self.company_currency_id:
            return blocked(
                _("Immediate settlement is only available in foreign currency."),
            )
        if not self.company_id.immediate_settlement_journal_id:
            return blocked(
                _(
                    "Configure an Immediate Settlements journal in Accounting "
                    "Settings before using Settle.",
                ),
            )

        facts = self._get_immediate_settlement_source_facts(payment_line)
        currency = facts.get("currency")
        foreign_amount = facts.get("foreign_amount")
        company_amount = facts.get("company_amount")
        statement_line = facts.get("statement_line")
        if currency != self.currency_id:
            return blocked(
                _("The document and payment must use the same foreign currency."),
            )
        if (
            not foreign_amount
            or not company_amount
            or currency.is_zero(foreign_amount)
            or self.company_currency_id.is_zero(company_amount)
        ):
            return blocked(
                _(
                    "The payment must contain both the exact foreign amount "
                    "and a real company-currency amount.",
                ),
            )

        allocation = self._immediate_settlement_document_allocation(
            foreign_amount,
        )
        if not allocation:
            return blocked(
                _(
                    "The payment amount does not identify one document "
                    "residual or payment term unambiguously.",
                ),
            )
        target_account = allocation["account"]
        source_is_target = payment_line.account_id == target_account
        source_is_suspense = bool(
            statement_line
            and payment_line.account_id == statement_line.journal_id.suspense_account_id
            and payment_line.account_id.reconcile,
        )
        if not source_is_target and not source_is_suspense:
            return blocked(
                _(
                    "The payment must be on the document account or the bank "
                    "journal's reconcilable suspense account.",
                ),
            )

        signed_document_foreign = sum(
            line.amount_residual_currency for line in allocation["lines"]
        )
        signed_document_company = sum(
            line.amount_residual for line in allocation["lines"]
        )
        if (
            signed_document_foreign * foreign_amount >= 0
            or signed_document_company * company_amount >= 0
        ):
            return blocked(
                _("The payment direction does not match this document."),
            )

        foreign_to_allocate = allocation["foreign_amount"]
        source_foreign_available = abs(foreign_amount)
        source_company_amount = self.company_currency_id.round(
            abs(company_amount) * foreign_to_allocate / source_foreign_available,
        )
        if self.company_currency_id.is_zero(source_company_amount):
            return blocked(_("The allocated company amount rounds to zero."))
        reference_company_amount = allocation["reference_company_amount"]
        executed_rate = source_company_amount / foreign_to_allocate
        reference_rate = reference_company_amount / foreign_to_allocate
        if not reference_rate:
            return blocked(_("The document reference rate is unavailable."))
        deviation = abs(executed_rate / reference_rate - 1.0) * 100.0

        policy = self._get_immediate_settlement_policy(payment_line)
        if deviation > policy["max_deviation"]:
            return blocked(
                _(
                    "The executed rate differs from the reference rate by "
                    "%(deviation).2f%%, above the %(maximum).2f%% policy.",
                    deviation=deviation,
                    maximum=policy["max_deviation"],
                ),
            )
        document_date = self.invoice_date or self.date
        payment_date = payment_line.date
        date_distance = abs((payment_date - document_date).days)
        if not facts.get("trusted") and date_distance > policy["max_days"]:
            return blocked(
                _(
                    "The payment is %(days)s days from the document, above "
                    "the %(maximum)s-day immediate-settlement policy.",
                    days=date_distance,
                    maximum=policy["max_days"],
                ),
            )
        if not self._immediate_settlement_fee_lines_are_explained(
            payment_line,
            statement_line,
        ):
            return blocked(
                _(
                    "The bank transaction contains an unexplained amount or "
                    "fee. Review it in Bank Matching.",
                ),
            )

        economic_lines = self._immediate_settlement_economic_lines()
        if not economic_lines:
            return blocked(
                _(
                    "This document's economic lines cannot be adjusted safely. "
                    "Use Add for standard reconciliation.",
                ),
            )
        settlement_date = max(document_date, payment_date)
        violations = []
        for accounting_date in {
            document_date,
            payment_date,
            settlement_date,
        }:
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
                    locks=self.company_id._format_lock_dates(violations),
                ),
            )

        signed_foreign_amount = (
            foreign_to_allocate if foreign_amount > 0 else -foreign_to_allocate
        )
        signed_company_amount = (
            source_company_amount if company_amount > 0 else -source_company_amount
        )
        signed_reference_company_amount = (
            reference_company_amount
            if signed_document_company > 0
            else -reference_company_amount
        )
        return {
            "eligible": True,
            "reason": _(
                "Match this immediate payment at the bank's actual rate. "
                "No exchange gain or loss will be created.",
            ),
            "confidence": (
                "high"
                if not facts.get("trusted") and date_distance <= 1
                else "trusted"
                if facts.get("trusted")
                else "normal"
            ),
            "facts": facts,
            "allocation": allocation,
            "economic_lines": economic_lines,
            "source_is_suspense": source_is_suspense,
            "foreign_amount": signed_foreign_amount,
            "company_amount": signed_company_amount,
            "reference_company_amount": signed_reference_company_amount,
            "executed_rate": executed_rate,
            "reference_rate": reference_rate,
            "rate_deviation": deviation,
            "document_date": document_date,
            "payment_date": payment_date,
            "settlement_date": settlement_date,
        }

    def _prepare_immediate_settlement_move_vals(
        self,
        settlement,
        eligibility,
    ):
        self.ensure_one()
        company_currency = self.company_currency_id
        currency = self.currency_id
        payment_line = settlement.payment_line_id
        allocation = eligibility["allocation"]
        document_company = eligibility["reference_company_amount"]
        payment_company = eligibility["company_amount"]
        valuation_balance = company_currency.round(
            -(document_company + payment_company),
        )
        line_vals = []
        if eligibility["source_is_suspense"]:
            line_vals.extend(
                [
                    Command.create(
                        {
                            "name": _("Immediate settlement suspense clearing"),
                            "account_id": payment_line.account_id.id,
                            "partner_id": payment_line.partner_id.id,
                            "currency_id": currency.id,
                            "balance": -payment_company,
                            "amount_currency": -eligibility["foreign_amount"],
                            "immediate_settlement_id": settlement.id,
                            "immediate_settlement_role": "suspense_clear",
                        },
                    ),
                    Command.create(
                        {
                            "name": _("Immediate settlement payment bridge"),
                            "account_id": allocation["account"].id,
                            "partner_id": self.commercial_partner_id.id,
                            "currency_id": currency.id,
                            "balance": payment_company,
                            "amount_currency": eligibility["foreign_amount"],
                            "immediate_settlement_id": settlement.id,
                            "immediate_settlement_role": "payment_bridge",
                        },
                    ),
                ],
            )
        if not company_currency.is_zero(valuation_balance):
            line_vals.append(
                Command.create(
                    {
                        "name": _("Immediate settlement document valuation"),
                        "account_id": allocation["account"].id,
                        "partner_id": self.commercial_partner_id.id,
                        "currency_id": currency.id,
                        "balance": valuation_balance,
                        "amount_currency": 0.0,
                        "immediate_settlement_id": settlement.id,
                        "immediate_settlement_role": "valuation",
                    },
                ),
            )

            economic_lines = eligibility["economic_lines"]
            total_weight = sum(abs(line.balance) for line in economic_lines)
            remaining = -valuation_balance
            for index, original_line in enumerate(economic_lines):
                if index == len(economic_lines) - 1:
                    balance = remaining
                else:
                    balance = company_currency.round(
                        -valuation_balance * abs(original_line.balance) / total_weight,
                    )
                    remaining -= balance
                line_vals.append(
                    Command.create(
                        {
                            "name": _(
                                "Immediate settlement allocation: %(line)s",
                                line=original_line.name or self.display_name,
                            ),
                            "account_id": original_line.account_id.id,
                            "partner_id": original_line.partner_id.id,
                            "currency_id": company_currency.id,
                            "balance": balance,
                            "amount_currency": balance,
                            "analytic_distribution": (
                                original_line.analytic_distribution
                            ),
                            "immediate_settlement_id": settlement.id,
                            "immediate_settlement_role": "economic",
                        },
                    ),
                )
        return {
            "move_type": "entry",
            "journal_id": self.company_id.immediate_settlement_journal_id.id,
            "company_id": self.company_id.id,
            "date": eligibility["settlement_date"],
            "ref": _(
                "Immediate settlement %(document)s at payment rate",
                document=self.display_name,
            ),
            "immediate_settlement_adjustment_id": settlement.id,
            "line_ids": line_vals,
        }

    def _link_immediate_settlement_allocations(
        self,
        settlement,
        adjustment_move,
        eligibility,
    ):
        economic_adjustments = adjustment_move.line_ids.filtered(
            lambda line: line.immediate_settlement_role == "economic",
        )
        values = []
        total_weight = sum(abs(line.balance) for line in eligibility["economic_lines"])
        for original_line, adjustment_line in zip(
            eligibility["economic_lines"],
            economic_adjustments,
        ):
            values.append(
                {
                    "settlement_id": settlement.id,
                    "original_line_id": original_line.id,
                    "adjustment_line_id": adjustment_line.id,
                    "company_amount": adjustment_line.balance,
                    "proportion": abs(original_line.balance) / total_weight,
                    "analytic_distribution_snapshot": (
                        original_line.analytic_distribution
                    ),
                },
            )
        if values:
            self.env["account.immediate.settlement.allocation"].create(values)

    def js_settle_outstanding_line(self, line_id):
        self.ensure_one()
        if not self.env.user.has_group("account.group_account_user"):
            raise AccessError(
                _("Only accountants can settle at the payment rate."),
            )
        self.check_access("write")
        self.env["account.move"].check_access("create")

        payment_line = self.env["account.move.line"].browse(line_id).exists()
        if not payment_line:
            raise UserError(
                _("This payment no longer exists. Refresh the document."),
            )
        move_ids = tuple({self.id, payment_line.move_id.id})
        self.env.cr.execute(
            "SELECT id FROM account_move WHERE id IN %s FOR UPDATE",
            [move_ids],
        )
        self.env.cr.execute(
            "SELECT id FROM account_move_line WHERE id IN %s FOR UPDATE",
            [(payment_line.id,)],
        )
        self.invalidate_recordset()
        payment_line.invalidate_recordset()

        existing = self.env["account.immediate.settlement"].search(
            [
                ("document_id", "=", self.id),
                ("payment_line_id", "=", payment_line.id),
                ("state", "=", "settled"),
            ],
            limit=1,
        )
        if existing:
            return {"settlement_id": existing.id}

        eligibility = self._get_immediate_settlement_eligibility(
            payment_line,
            raise_exception=True,
        )
        settlement = self.env["account.immediate.settlement"].create(
            {
                "name": self.env["ir.sequence"].next_by_code(
                    "account.immediate.settlement",
                )
                or _("New"),
                "company_id": self.company_id.id,
                "currency_id": self.currency_id.id,
                "document_id": self.id,
                "payment_line_id": payment_line.id,
                "statement_line_id": eligibility["facts"]["statement_line"].id,
                "document_line_ids": [
                    Command.set(eligibility["allocation"]["lines"].ids),
                ],
                "foreign_amount": abs(eligibility["foreign_amount"]),
                "company_amount": abs(eligibility["company_amount"]),
                "reference_company_amount": abs(
                    eligibility["reference_company_amount"],
                ),
                "executed_rate": eligibility["executed_rate"],
                "reference_rate": eligibility["reference_rate"],
                "rate_deviation": eligibility["rate_deviation"],
                "document_date": eligibility["document_date"],
                "payment_date": eligibility["payment_date"],
                "settlement_date": eligibility["settlement_date"],
                "provenance": eligibility["facts"]["provenance"],
                "provenance_details": eligibility["facts"]["details"],
                "trusted_source": eligibility["facts"]["trusted"],
                "user_id": self.env.user.id,
            },
        )
        move_vals = self._prepare_immediate_settlement_move_vals(
            settlement,
            eligibility,
        )
        adjustment_move = self.env["account.move"]
        if move_vals["line_ids"]:
            adjustment_move = self.env["account.move"].create(move_vals)
            adjustment_move.action_post()
            settlement.sudo().with_context(
                immediate_settlement_internal=True,
            ).write({"adjustment_move_id": adjustment_move.id})
            self._link_immediate_settlement_allocations(
                settlement,
                adjustment_move,
                eligibility,
            )

        reconciliation_lines = (
            eligibility["allocation"]["lines"]
            + payment_line
            + adjustment_move.line_ids.filtered(
                lambda line: (
                    line.immediate_settlement_role
                    in ("suspense_clear", "payment_bridge", "valuation")
                ),
            )
        )
        before_partial_ids = set(
            (
                reconciliation_lines.matched_debit_ids
                + reconciliation_lines.matched_credit_ids
            ).ids,
        )
        if eligibility["source_is_suspense"]:
            suspense_clear = adjustment_move.line_ids.filtered(
                lambda line: line.immediate_settlement_role == "suspense_clear",
            )
            (payment_line + suspense_clear).with_context(
                no_exchange_difference=True,
            ).reconcile()
            payment_lines = adjustment_move.line_ids.filtered(
                lambda line: (
                    line.immediate_settlement_role in ("payment_bridge", "valuation")
                ),
            )
        else:
            payment_lines = payment_line + adjustment_move.line_ids.filtered(
                lambda line: line.immediate_settlement_role == "valuation",
            )
        (eligibility["allocation"]["lines"] + payment_lines).with_context(
            no_exchange_difference=True,
        ).reconcile()
        new_partials = (
            reconciliation_lines.matched_debit_ids
            + reconciliation_lines.matched_credit_ids
        ).filtered(lambda partial: partial.id not in before_partial_ids)
        new_partials.sudo().write(
            {"immediate_settlement_id": settlement.id},
        )

        self.message_post(
            body=_(
                "Settled at payment rate: %(foreign).2f %(currency)s = "
                "%(company).2f %(company_currency)s. Accounting trace: "
                "%(trace)s.",
                foreign=settlement.foreign_amount,
                currency=settlement.currency_id.name,
                company=settlement.company_amount,
                company_currency=settlement.company_currency_id.name,
                trace=(
                    adjustment_move.display_name if adjustment_move else settlement.name
                ),
            ),
        )
        if settlement.payment_move_id != self:
            settlement.payment_move_id.message_post(
                body=_(
                    "Payment used for immediate settlement %(name)s of %(document)s.",
                    name=settlement.name,
                    document=self.display_name,
                ),
            )
        return {"settlement_id": settlement.id}

    def action_open_immediate_settlement(self):
        self.ensure_one()
        settlement = (
            self.immediate_settlement_adjustment_id or self.immediate_settlement_ids[:1]
        )
        if not settlement:
            raise UserError(_("No immediate settlement is linked to this entry."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Immediate Settlement"),
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
                item["show_immediate_settlement_reason"] = bool(
                    payment_line.currency_id == move.currency_id
                    and move.currency_id != move.company_currency_id
                    and not move.currency_id.is_zero(
                        payment_line.amount_residual_currency,
                    ),
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
            for settlement in active_settlements:
                partial_ids = set(settlement.partial_reconcile_ids.ids)
                matching = [
                    item for item in content if item.get("partial_id") in partial_ids
                ]
                if not matching:
                    continue
                content = [
                    item
                    for item in content
                    if item.get("partial_id") not in partial_ids
                ]
                content.append(
                    {
                        "name": _("Settled at payment rate"),
                        "journal_name": (
                            settlement.adjustment_move_id.journal_id.name
                            or settlement.payment_move_id.journal_id.name
                        ),
                        "amount": settlement.foreign_amount,
                        "currency_id": settlement.currency_id.id,
                        "date": settlement.settlement_date,
                        "partial_id": settlement.partial_reconcile_ids[:1].id,
                        "move_id": (
                            settlement.adjustment_move_id.id
                            or settlement.payment_move_id.id
                        ),
                        "ref": settlement.name,
                        "is_exchange": False,
                        "is_refund": False,
                        "is_immediate_settlement": True,
                        "immediate_settlement_id": settlement.id,
                        "executed_pair": _(
                            "%(foreign).2f %(currency)s = %(company).2f "
                            "%(company_currency)s",
                            foreign=settlement.foreign_amount,
                            currency=settlement.currency_id.name,
                            company=settlement.company_amount,
                            company_currency=(settlement.company_currency_id.name),
                        ),
                        "executed_rate": settlement.executed_rate,
                        "reference_rate": settlement.reference_rate,
                        "rate_deviation": settlement.rate_deviation,
                        "provenance": settlement.provenance,
                    },
                )
            widget = dict(widget)
            widget["content"] = sorted(
                content,
                key=lambda item: item.get("date") or fields.Date.today(),
            )
            move.invoice_payments_widget = widget

    def button_draft(self):
        protected = self.filtered("immediate_settlement_adjustment_id")
        if protected and not self.env.context.get("immediate_settlement_reversal"):
            raise UserError(
                _(
                    "Reverse the linked immediate settlement instead of "
                    "resetting its adjustment to draft.",
                ),
            )
        return super().button_draft()

    def button_cancel(self):
        protected = self.filtered("immediate_settlement_adjustment_id")
        if protected and not self.env.context.get("immediate_settlement_reversal"):
            raise UserError(
                _(
                    "Reverse the linked immediate settlement instead of "
                    "cancelling its adjustment.",
                ),
            )
        return super().button_cancel()

    @api.ondelete(at_uninstall=False)
    def _unlink_immediate_settlement_adjustment(self):
        if self.immediate_settlement_adjustment_id and not self.env.context.get(
            "immediate_settlement_reversal",
        ):
            raise UserError(
                _(
                    "Immediate settlement adjustments cannot be deleted. "
                    "Reverse the settlement instead.",
                ),
            )
