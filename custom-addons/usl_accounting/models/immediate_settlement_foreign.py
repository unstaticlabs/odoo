"""Foreign-currency and payment-rate settlement: context, eligibility, snapshots, locking and execution."""

from odoo import (
    Command,
    _,
    api,
    models,
)
from odoo.exceptions import AccessError, UserError

from .immediate_settlement import (
    _ECONOMIC_DISPLAY_TYPES,
    _INTERNAL_SETTLEMENT_TOKEN,
    _SAFE_ECONOMIC_ACCOUNT_TYPES,
    _as_settlement_service,
)


class AccountMove(models.Model):
    _inherit = "account.move"

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
