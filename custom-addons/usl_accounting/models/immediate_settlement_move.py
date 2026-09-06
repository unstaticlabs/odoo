"""Native journal, statement and reconciliation extensions that drive immediate settlement."""

from odoo import (
    _,
    api,
    fields,
    models,
)
from odoo.exceptions import AccessError, UserError
from odoo.tools import formatLang

from .immediate_settlement import (
    _INTERNAL_SETTLEMENT_TOKEN,
    _has_internal_settlement_token,
)


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
