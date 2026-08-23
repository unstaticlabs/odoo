import hashlib
from collections import Counter, defaultdict
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .bank_partner_suggestion import _normalize_text

_CANDIDATE_DATE_WINDOW_DAYS = 10
_CANDIDATE_LIMIT = 5
_CANDIDATE_AMOUNT_PERCENT = 0.02
_CANDIDATE_AMOUNT_CAP = 25.0
_GENERIC_EXPENSE_TOKENS = {
    "achat",
    "carte",
    "depense",
    "expense",
    "facture",
    "invoice",
    "note",
    "paiement",
    "payment",
    "receipt",
    "recu",
}


class UslExpenseBankMatchCandidate(models.Model):
    _name = "usl.expense.bank.match.candidate"
    _description = "Expense Bank Match Candidate"
    _order = "state, rank, id"
    _rec_name = "name"

    _expense_bank_line_unique = models.Constraint(
        "UNIQUE(expense_id, bank_statement_line_id)",
        "A bank transaction can only appear once for an expense.",
    )

    name = fields.Char(required=True, readonly=True)
    expense_id = fields.Many2one(
        "hr.expense",
        required=True,
        index=True,
        ondelete="cascade",
        check_company=True,
        readonly=True,
    )
    bank_statement_line_id = fields.Many2one(
        "account.bank.statement.line",
        string="Bank Transaction",
        required=True,
        index=True,
        ondelete="restrict",
        check_company=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        related="expense_id.company_id",
        store=True,
        index=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="expense_id.currency_id",
        store=True,
        readonly=True,
    )
    rank = fields.Integer(required=True, readonly=True)
    score = fields.Integer(readonly=True)
    match_label = fields.Selection(
        [
            ("best", "Best match"),
            ("alternative", "Alternative"),
        ],
        required=True,
        readonly=True,
    )
    state = fields.Selection(
        [
            ("available", "Available"),
            ("accepted", "Accepted"),
            ("unavailable", "No longer available"),
        ],
        required=True,
        default="available",
        index=True,
        readonly=True,
    )
    fingerprint = fields.Char(required=True, index=True, readonly=True)
    evidence_summary = fields.Char(string="Why suggested", readonly=True)
    unavailable_reason = fields.Char(readonly=True)
    expense_amount = fields.Monetary(readonly=True)
    bank_amount = fields.Monetary(
        string="Bank Amount",
        currency_field="currency_id",
        readonly=True,
    )
    amount_difference = fields.Monetary(
        currency_field="currency_id",
        readonly=True,
    )
    amount_is_exact = fields.Boolean(readonly=True)
    expense_date = fields.Date(readonly=True)
    bank_date = fields.Date(readonly=True)
    date_difference = fields.Integer(readonly=True)
    journal_id = fields.Many2one(
        "account.journal",
        readonly=True,
        check_company=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        readonly=True,
        check_company=True,
    )
    bank_label = fields.Char(readonly=True)
    competing_expense_count = fields.Integer(readonly=True)
    refreshed_at = fields.Datetime(required=True, readonly=True)
    accepted_at = fields.Datetime(readonly=True)
    accepted_by_id = fields.Many2one("res.users", readonly=True)

    def _check_manager(self):
        if not (
            self.env.su
            or self.env.user.has_group("account.group_account_manager")
        ):
            raise AccessError(_(
                "Only an Accounting Manager can refresh or use expense bank "
                "matches.",
            ))

    def action_open_confirmation(self):
        self.ensure_one()
        self._check_manager()
        self._validate_available(exact_required=True)
        wizard = self.env["usl.expense.bank.match.wizard"].create({
            "candidate_id": self.id,
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Use bank transaction"),
            "res_model": "usl.expense.bank.match.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
        }

    def _validate_available(self, *, exact_required):
        self.ensure_one()
        expense = self.expense_id.exists()
        bank_line = self.bank_statement_line_id.exists()
        if (
            self.state != "available"
            or not expense
            or not bank_line
            or bank_line.is_reconciled
        ):
            raise UserError(_(
                "This bank transaction is no longer available. Refresh the "
                "expense and review the current suggestions.",
            ))
        if not expense._usl_bank_match_is_eligible():
            raise UserError(expense._usl_bank_match_ineligible_reason())
        current_values = expense._usl_bank_match_candidate_values(bank_line)
        if (
            not current_values
            or current_values["fingerprint"] != self.fingerprint
        ):
            raise UserError(_(
                "The expense or bank transaction changed after this "
                "suggestion was prepared. Refresh the suggestions before "
                "continuing.",
            ))
        if exact_required and not current_values["amount_is_exact"]:
            raise UserError(_(
                "The amounts are close but not equal. Correct the expense or "
                "review the transaction in Bank Matching before continuing.",
            ))
        return expense, bank_line, current_values

    def _expense_outstanding_line(self, expense):
        payment = expense.account_move_id.origin_payment_id
        if not payment or not payment.move_id:
            raise UserError(_(
                "Odoo did not create the expected company payment for this "
                "expense. Review the expense and its journal entry.",
            ))
        outstanding_line = payment.move_id.line_ids.filtered(
            lambda line: (
                line.account_id == payment.outstanding_account_id
                and not line.reconciled
                and not line.currency_id.is_zero(
                    line.amount_residual_currency,
                )
            ),
        )
        if len(outstanding_line) != 1:
            raise UserError(_(
                "The expense payment does not contain one available "
                "outstanding line. Review its journal entry before matching.",
            ))
        return outstanding_line

    def _prepare_oca_reconciliation_data(self, bank_line, outstanding_line):
        """Persist the same structured data as OCA's add-line form onchange.

        ``_add_account_move_line`` is a form-oriented helper: its non-stored
        result is persisted by the web client's save call.  This server-side
        action builds that result through the same OCA line/data helpers and
        writes only OCA's stored reconciliation payload.  It never mutates an
        account or move line directly.
        """
        bank_line.clean_reconcile()
        reconcile_info = bank_line.reconcile_data_info
        if outstanding_line.id in reconcile_info.get("counterparts", []):
            bank_line.reconcile_data = reconcile_info
            bank_line.invalidate_recordset(
                ["reconcile_data_info", "can_reconcile"],
            )
            return reconcile_info
        data = [
            line
            for line in reconcile_info["data"]
            if line["kind"] != "suspense"
        ]
        currency = bank_line._get_reconcile_currency()
        pending_amount = sum(
            bank_line._get_amount_currency(line, currency)
            for line in data
        )
        reconcile_auxiliary_id, lines = bank_line._get_reconcile_line(
            outstanding_line,
            "other",
            is_counterpart=True,
            max_amount=currency.round(pending_amount),
            reconcile_auxiliary_id=reconcile_info[
                "reconcile_auxiliary_id"
            ],
            move=True,
        )
        data.extend(lines)
        prepared = bank_line._recompute_suspense_line(
            data,
            reconcile_auxiliary_id,
            reconcile_info["manual_reference"],
        )
        bank_line.reconcile_data = prepared
        bank_line.invalidate_recordset(["reconcile_data_info", "can_reconcile"])
        return prepared

    def _apply_match(self):
        self.ensure_one()
        self._check_manager()
        expense, bank_line, _values = self._validate_available(
            exact_required=True,
        )

        payment_method = expense._usl_bank_match_payment_method(bank_line)
        previous_vendor = expense.vendor_id
        target_vendor = bank_line.partner_id or expense.vendor_id
        expense.write({
            "payment_mode": "company_account",
            "payment_method_line_id": payment_method.id,
            "vendor_id": target_vendor.id if target_vendor else False,
        })

        if expense.state == "draft":
            expense.action_submit()
            expense.invalidate_recordset(["state", "approval_state"])
        if expense.state == "submitted":
            approval_action = expense.with_context(
                validate_analytic=True,
            ).action_approve()
            if approval_action:
                raise UserError(_(
                    "Odoo requires an explicit duplicate-expense review. "
                    "Complete that native review, then use this bank "
                    "transaction again.",
                ))
            expense.invalidate_recordset(["state", "approval_state"])
        if expense.state == "approved":
            post_action = expense.with_context(
                validate_analytic=True,
            ).action_post()
            if post_action:
                raise UserError(_(
                    "Odoo requires an additional posting step for this "
                    "expense. Complete it, then use this bank transaction "
                    "again.",
                ))
            expense.invalidate_recordset(["state", "account_move_id"])
        if (
            not expense.account_move_id
            or not expense.account_move_id.origin_payment_id
        ):
            raise UserError(_(
                "The expense was not posted as a native company payment. "
                "No bank reconciliation was performed.",
            ))

        outstanding_line = self._expense_outstanding_line(expense)
        reconcile_data = self._prepare_oca_reconciliation_data(
            bank_line,
            outstanding_line,
        )
        if not reconcile_data["can_reconcile"]:
            raise UserError(_(
                "The selected transaction and expense payment do not fully "
                "balance. Review them in Bank Matching.",
            ))
        bank_line.reconcile_bank_line()
        bank_line.invalidate_recordset(["is_reconciled", "amount_residual"])
        outstanding_line.invalidate_recordset([
            "reconciled",
            "amount_residual",
            "amount_residual_currency",
        ])
        if not bank_line.is_reconciled or not outstanding_line.reconciled:
            raise UserError(_(
                "Odoo did not confirm the expected reconciliation. No "
                "expense match was accepted.",
            ))

        now = fields.Datetime.now()
        self.write({
            "state": "accepted",
            "accepted_at": now,
            "accepted_by_id": self.env.user.id,
            "unavailable_reason": False,
        })
        competing_candidates = self.search([
            ("bank_statement_line_id", "=", bank_line.id),
            ("id", "!=", self.id),
            ("state", "=", "available"),
        ])
        competing_candidates.write({
            "state": "unavailable",
            "unavailable_reason": _(
                "The bank transaction was matched to %(expense)s.",
                expense=expense.display_name,
            ),
        })
        other_candidates = self.search([
            ("expense_id", "=", expense.id),
            ("id", "!=", self.id),
            ("state", "=", "available"),
        ])
        other_candidates.write({
            "state": "unavailable",
            "unavailable_reason": _("Another suggestion was accepted."),
        })

        vendor_change = ""
        if previous_vendor != target_vendor:
            vendor_change = _(
                " Vendor: %(before)s → %(after)s.",
                before=previous_vendor.display_name or _("Unassigned"),
                after=target_vendor.display_name or _("Unassigned"),
            )
        audit_message = _(
            "Matched company-paid expense %(expense)s to bank transaction "
            "%(transaction)s using %(evidence)s.%(vendor_change)s",
            expense=expense.display_name,
            transaction=bank_line.display_name,
            evidence=self.evidence_summary,
            vendor_change=vendor_change,
        )
        expense.message_post(body=Markup.escape(audit_message))
        bank_line.move_id.message_post(body=Markup.escape(audit_message))
        return {
            "type": "ir.actions.act_window",
            "res_model": "hr.expense",
            "res_id": expense.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }


class HrExpense(models.Model):
    _inherit = "hr.expense"

    usl_bank_match_candidate_ids = fields.One2many(
        "usl.expense.bank.match.candidate",
        "expense_id",
        string="Company Payment Matches",
        groups="account.group_account_readonly,account.group_account_manager",
        copy=False,
    )
    usl_bank_match_available_count = fields.Integer(
        compute="_compute_usl_bank_match_status",
        groups="account.group_account_readonly,account.group_account_manager",
    )
    usl_bank_match_accepted_count = fields.Integer(
        compute="_compute_usl_bank_match_status",
        groups="account.group_account_readonly,account.group_account_manager",
    )
    usl_bank_match_last_refreshed_at = fields.Datetime(
        compute="_compute_usl_bank_match_status",
        groups="account.group_account_readonly,account.group_account_manager",
    )
    usl_bank_match_eligible = fields.Boolean(
        compute="_compute_usl_bank_match_eligible",
        groups="account.group_account_readonly,account.group_account_manager",
    )
    usl_bank_match_guidance = fields.Char(
        compute="_compute_usl_bank_match_eligible",
        groups="account.group_account_readonly,account.group_account_manager",
    )

    @api.depends(
        "usl_bank_match_candidate_ids.state",
        "usl_bank_match_candidate_ids.refreshed_at",
    )
    def _compute_usl_bank_match_status(self):
        for expense in self:
            available = expense.usl_bank_match_candidate_ids.filtered(
                lambda candidate: candidate.state == "available",
            )
            accepted = expense.usl_bank_match_candidate_ids.filtered(
                lambda candidate: candidate.state == "accepted",
            )
            expense.usl_bank_match_available_count = len(available)
            expense.usl_bank_match_accepted_count = len(accepted)
            expense.usl_bank_match_last_refreshed_at = max(
                available.mapped("refreshed_at"),
                default=False,
            )

    @api.depends("state", "payment_mode", "account_move_id.state")
    def _compute_usl_bank_match_eligible(self):
        for expense in self:
            expense.usl_bank_match_eligible = (
                expense._usl_bank_match_is_eligible()
            )
            expense.usl_bank_match_guidance = (
                _("Find the company bank debit before posting this expense.")
                if expense.usl_bank_match_eligible
                else expense._usl_bank_match_ineligible_reason()
            )

    def _usl_bank_match_is_eligible(self):
        self.ensure_one()
        if self.state not in ("draft", "submitted", "approved"):
            return False
        return not (
            self.payment_mode == "own_account"
            and self.account_move_id
            and self.account_move_id.state == "posted"
        )

    def _usl_bank_match_ineligible_reason(self):
        self.ensure_one()
        accepted_count = len(self.usl_bank_match_candidate_ids.filtered(
            lambda candidate: candidate.state == "accepted",
        ))
        if accepted_count:
            return _("The company payment is already matched.")
        if self.account_move_id and self.payment_mode == "own_account":
            return _(
                "This expense was already posted as employee-paid. Reset or "
                "correct it through the native accounting workflow before "
                "matching a company bank transaction.",
            )
        if self.state in ("posted", "in_payment", "paid"):
            return _("This expense is already posted or settled.")
        if self.state == "refused":
            return _("A refused expense cannot be matched.")
        return _("Complete the expense amount and date before finding a match.")

    def _usl_bank_match_check_manager(self):
        if not (
            self.env.su
            or self.env.user.has_group("account.group_account_manager")
        ):
            raise AccessError(_(
                "Only an Accounting Manager can refresh expense bank matches.",
            ))

    def _usl_bank_match_amount(self):
        self.ensure_one()
        return abs(self.total_amount_currency)

    def _usl_bank_match_bank_amount(self, bank_line):
        self.ensure_one()
        if bank_line.foreign_currency_id == self.currency_id:
            return abs(bank_line.amount_currency)
        bank_currency = bank_line.currency_id
        amount = abs(bank_line.amount)
        if bank_currency == self.currency_id:
            return amount
        return bank_currency._convert(
            amount,
            self.currency_id,
            self.company_id,
            bank_line.date,
        )

    def _usl_bank_match_tokens(self):
        self.ensure_one()
        values = [
            self.name,
            self.description,
            self.vendor_id.display_name,
            self.product_id.display_name,
        ]
        return {
            token
            for token in _normalize_text(" ".join(filter(None, values))).split()
            if len(token) >= 4 and token not in _GENERIC_EXPENSE_TOKENS
        }

    def _usl_bank_match_fingerprint(self, bank_line):
        self.ensure_one()
        values = (
            self.id,
            self.company_id.id,
            self.currency_id.id,
            fields.Date.to_string(self.date),
            self._usl_bank_match_amount(),
            self.vendor_id.id,
            bank_line.id,
            fields.Date.to_string(bank_line.date),
            bank_line.amount,
            bank_line.amount_currency,
            bank_line.foreign_currency_id.id,
            bank_line.partner_id.id,
            bank_line.is_reconciled,
        )
        return hashlib.sha256(
            "|".join(str(value or "") for value in values).encode(),
        ).hexdigest()

    def _usl_bank_match_candidate_values(
        self,
        bank_line,
        *,
        competition_count=0,
    ):
        self.ensure_one()
        if (
            not self.date
            or not self._usl_bank_match_amount()
            or bank_line.company_id != self.company_id
            or bank_line.amount >= 0
            or bank_line.is_reconciled
        ):
            return False
        date_difference = abs((bank_line.date - self.date).days)
        if date_difference > _CANDIDATE_DATE_WINDOW_DAYS:
            return False
        expense_amount = self._usl_bank_match_amount()
        bank_amount = self._usl_bank_match_bank_amount(bank_line)
        amount_difference = abs(bank_amount - expense_amount)
        tolerance = max(
            self.currency_id.rounding,
            min(
                expense_amount * _CANDIDATE_AMOUNT_PERCENT,
                _CANDIDATE_AMOUNT_CAP,
            ),
        )
        if amount_difference > tolerance:
            return False
        amount_is_exact = (
            self.currency_id.compare_amounts(
                bank_amount,
                expense_amount,
            )
            == 0
        )

        evidence = []
        score = 0
        if amount_is_exact:
            score += 200
            evidence.append(_("Exact amount"))
        else:
            score += 60
            evidence.append(_(
                "%(difference)s amount difference",
                difference=self.currency_id.format(amount_difference),
            ))
        if date_difference == 0:
            score += 120
            evidence.append(_("Same day"))
        elif date_difference == 1:
            score += 80
            evidence.append(_("1 day apart"))
        elif date_difference <= 3:
            score += 40
            evidence.append(_("%(days)s days apart", days=date_difference))
        else:
            score += 10
            evidence.append(_("%(days)s days apart", days=date_difference))

        expense_tokens = self._usl_bank_match_tokens()
        bank_text = " ".join(filter(None, (
            bank_line.payment_ref,
            bank_line.ref,
            bank_line.partner_name,
            bank_line.partner_id.display_name,
        )))
        common_tokens = sorted(
            expense_tokens.intersection(_normalize_text(bank_text).split()),
        )
        if common_tokens:
            score += min(len(common_tokens) * 10, 40)
            evidence.append(_(
                "Text match: %(terms)s",
                terms=", ".join(common_tokens[:3]),
            ))
        if (
            self.vendor_id
            and bank_line.partner_id.commercial_partner_id
            == self.vendor_id.commercial_partner_id
        ):
            score += 50
            evidence.append(_("Vendor matches"))
        elif bank_line.partner_id:
            evidence.append(_(
                "Bank partner: %(partner)s",
                partner=bank_line.partner_id.display_name,
            ))
        if competition_count:
            evidence.append(_(
                "%(count)s other expense(s) may match this transaction",
                count=competition_count,
            ))

        label = (
            bank_line.payment_ref
            or bank_line.ref
            or bank_line.name
            or bank_line.display_name
        )
        return {
            "name": _(
                "%(date)s · %(amount)s · %(label)s",
                date=fields.Date.to_string(bank_line.date),
                amount=self.currency_id.format(bank_amount),
                label=label,
            ),
            "expense_id": self.id,
            "bank_statement_line_id": bank_line.id,
            "score": score,
            "fingerprint": self._usl_bank_match_fingerprint(bank_line),
            "evidence_summary": " · ".join(evidence),
            "expense_amount": expense_amount,
            "bank_amount": bank_amount,
            "amount_difference": amount_difference,
            "amount_is_exact": amount_is_exact,
            "expense_date": self.date,
            "bank_date": bank_line.date,
            "date_difference": date_difference,
            "journal_id": bank_line.journal_id.id,
            "partner_id": bank_line.partner_id.id,
            "bank_label": label,
            "competing_expense_count": competition_count,
            "refreshed_at": fields.Datetime.now(),
        }

    def _usl_bank_match_payment_method(self, bank_line):
        self.ensure_one()
        methods = self.selectable_payment_method_line_ids.filtered(
            lambda method: (
                method.journal_id == bank_line.journal_id
                and method.payment_type == "outbound"
            ),
        )
        method = methods.filtered(
            lambda candidate: candidate.code == "manual",
        )[:1]
        method = method or methods[:1]
        if not method:
            raise UserError(_(
                "The %(journal)s journal has no outbound payment method "
                "allowed for company-paid expenses.",
                journal=bank_line.journal_id.display_name,
            ))
        return method

    def _usl_refresh_bank_match_candidates(self):
        self._usl_bank_match_check_manager()
        if not self:
            return True
        Candidate = self.env["usl.expense.bank.match.candidate"]
        for expense in self:
            if not expense._usl_bank_match_is_eligible():
                raise UserError(expense._usl_bank_match_ineligible_reason())
            amount = expense._usl_bank_match_amount()
            if not expense.date or expense.currency_id.is_zero(amount):
                raise UserError(_(
                    "Enter the expense date and a non-zero amount before "
                    "finding bank transactions.",
                ))

        date_from = min(self.mapped("date")) - timedelta(
            days=_CANDIDATE_DATE_WINDOW_DAYS,
        )
        date_to = max(self.mapped("date")) + timedelta(
            days=_CANDIDATE_DATE_WINDOW_DAYS,
        )
        bank_lines = self.env["account.bank.statement.line"].search([
            ("company_id", "in", self.company_id.ids),
            ("date", ">=", date_from),
            ("date", "<=", date_to),
            ("amount", "<", 0),
            ("is_reconciled", "=", False),
        ])
        comparison_expenses = self.search([
            ("company_id", "in", self.company_id.ids),
            ("date", ">=", date_from),
            ("date", "<=", date_to),
            ("state", "in", ("draft", "submitted", "approved")),
        ])
        preliminary_by_expense = defaultdict(dict)
        expense_count_by_line = Counter()
        for expense in comparison_expenses:
            expense_date_from = expense.date - timedelta(
                days=_CANDIDATE_DATE_WINDOW_DAYS,
            )
            expense_date_to = expense.date + timedelta(
                days=_CANDIDATE_DATE_WINDOW_DAYS,
            )
            applicable_lines = bank_lines.filtered(
                lambda bank_line: (
                    bank_line.company_id == expense.company_id
                    and expense_date_from
                    <= bank_line.date
                    <= expense_date_to
                ),
            )
            for bank_line in applicable_lines:
                values = expense._usl_bank_match_candidate_values(bank_line)
                if values:
                    preliminary_by_expense[expense.id][bank_line.id] = values
                    expense_count_by_line[bank_line.id] += 1

        existing_candidates = Candidate.search([
            ("expense_id", "in", self.ids),
        ])
        existing_by_pair = {
            (
                candidate.expense_id.id,
                candidate.bank_statement_line_id.id,
            ): candidate
            for candidate in existing_candidates
        }
        for expense in self:
            values_by_line = preliminary_by_expense[expense.id]
            for bank_line_id in list(values_by_line):
                competition_count = expense_count_by_line[bank_line_id] - 1
                if competition_count:
                    values_by_line[bank_line_id] = (
                        expense._usl_bank_match_candidate_values(
                            self.env[
                                "account.bank.statement.line"
                            ].browse(bank_line_id),
                            competition_count=competition_count,
                        )
                    )
            ranked = sorted(
                values_by_line.values(),
                key=lambda values: (
                    -values["score"],
                    values["date_difference"],
                    values["amount_difference"],
                    values["bank_statement_line_id"],
                ),
            )[:_CANDIDATE_LIMIT]
            selected_line_ids = {
                values["bank_statement_line_id"] for values in ranked
            }
            previous_available = existing_candidates.filtered(
                lambda candidate: (
                    candidate.expense_id == expense
                    and candidate.state == "available"
                ),
            )
            previous_available.filtered(
                lambda candidate: (
                    candidate.bank_statement_line_id.id
                    not in selected_line_ids
                ),
            ).write({
                "state": "unavailable",
                "unavailable_reason": _(
                    "It is no longer among the current suggestions.",
                ),
            })
            for rank, values in enumerate(ranked, start=1):
                values.update({
                    "rank": rank,
                    "match_label": "best" if rank == 1 else "alternative",
                    "state": "available",
                    "unavailable_reason": False,
                })
                candidate = existing_by_pair.get((
                    expense.id,
                    values["bank_statement_line_id"],
                ))
                if candidate:
                    if candidate.state != "accepted":
                        candidate.write(values)
                else:
                    candidate = Candidate.create(values)
                    existing_by_pair[
                        expense.id,
                        values["bank_statement_line_id"],
                    ] = candidate
        return True

    def action_refresh_bank_match_candidates(self):
        self.ensure_one()
        self._usl_refresh_bank_match_candidates()
        self.invalidate_recordset([
            "usl_bank_match_candidate_ids",
            "usl_bank_match_available_count",
            "usl_bank_match_last_refreshed_at",
        ])
        count = self.usl_bank_match_available_count
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if count else "info",
                "message": (
                    _("%(count)s bank suggestion(s) found.", count=count)
                    if count
                    else _("No matching bank transaction was found.")
                ),
                "next": {
                    "type": "ir.actions.act_window",
                    "res_model": "hr.expense",
                    "res_id": self.id,
                    "view_mode": "form",
                    "views": [(False, "form")],
                    "target": "current",
                },
            },
        }
