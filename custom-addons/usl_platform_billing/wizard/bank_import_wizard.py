from collections import Counter

from psycopg2 import IntegrityError

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare


SETTLEMENT_ACCOUNT_TYPES = ("asset_receivable", "liability_payable")
# How far from a payout's documents a bank transaction's reconciliation may be
# traced: one hop is a partial reconciliation plus the sibling settlement lines
# of the move it reaches.
RECONCILIATION_HOPS = 4


class UslPlatformBillingBankImportWizard(models.TransientModel):
    _name = "usl.platform.billing.bank.import.wizard"
    _description = "Select Platform Bank Transactions"

    session_id = fields.Many2one(
        "usl.platform.billing.session",
        required=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        related="session_id.company_id",
        readonly=True,
    )
    bank_currency_id = fields.Many2one(
        related="session_id.bank_currency_id",
        readonly=True,
    )
    mode = fields.Selection(
        [
            ("create", "Create payouts from bank transactions"),
            ("link", "Link bank transactions to registered payouts"),
        ],
        required=True,
        default="link",
    )
    candidate_scope = fields.Selection(
        [
            ("all", "All open"),
            ("recommended", "Suggested only"),
            ("reconciled", "Already reconciled in Accounting"),
        ],
        required=True,
        default="all",
        help=(
            "All open keeps every eligible incoming transaction available. "
            "Suggested only applies the configured recognition rules. "
            "Already reconciled in Accounting lists, for the selected payouts, "
            "the incoming transactions that Accounting has already reconciled "
            "against their invoices or compensation entries, directly or through "
            "intermediate entries, so they can be linked as bank evidence "
            "without changing any entry. Select the payouts first. The search "
            "follows reconciliations of receivable lines, within 4 steps; a "
            "transaction reconciled through a payment's outstanding receipts "
            "account is not found."
        ),
    )
    payout_candidate_ids = fields.One2many(
        "usl.platform.billing.bank.import.wizard.payout.line",
        "wizard_id",
        string="Open Payouts",
    )
    candidate_ids = fields.One2many(
        "usl.platform.billing.bank.import.wizard.line",
        "wizard_id",
        string="Bank Transactions",
    )

    @api.onchange("mode")
    def _onchange_mode(self):
        for wizard in self:
            if wizard.mode != "link" and wizard.candidate_scope == "reconciled":
                wizard.candidate_scope = "all"
            wizard._populate_payout_candidates()
            wizard._populate_candidates()

    @api.onchange("candidate_scope")
    def _onchange_candidate_scope(self):
        for wizard in self:
            wizard._populate_payout_candidates(keep_selection=True)
            wizard._populate_candidates()

    def _adopts_reconciled_lines(self):
        """Whether this wizard links bank lines Accounting already reconciled."""
        self.ensure_one()
        return self.mode == "link" and self.candidate_scope == "reconciled"

    def _linkable_payout_states(self):
        """Payout states the wizard may attach bank evidence to.

        A payout whose bank transaction was reconciled in Accounting first is
        already paid, so adopting that evidence has to reach paid payouts too.
        """
        self.ensure_one()
        states = ("draft", "generated", "posted")
        return states + ("paid",) if self._adopts_reconciled_lines() else states

    @staticmethod
    def _line_label(bank_line):
        return " ".join(
            value
            for value in (
                bank_line.payment_ref,
                bank_line.name,
                bank_line.partner_name,
            )
            if value
        )

    def _active_platforms(self):
        self.ensure_one()
        return self.env["usl.platform.billing.platform"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("active", "=", True),
            ],
        )

    def _detect_platform(self, bank_line, platforms):
        label = self._line_label(bank_line)
        normalized = label.casefold()
        matches = []
        for platform in platforms:
            if (
                platform.bank_journal_id
                and platform.bank_journal_id != bank_line.journal_id
            ):
                continue
            regex = platform._bank_label_regex()
            regex_match = regex.search(label) if regex else False
            if regex_match:
                matches.append(
                    (
                        100,
                        platform,
                        regex_match.groupdict().get("ref"),
                        _("Configured label pattern"),
                        "regex",
                    ),
                )
                continue
            partners = (
                platform.partner_id
                | platform.customer_partner_id
                | platform.supplier_partner_id
            ).commercial_partner_id
            if (
                bank_line.partner_id
                and bank_line.partner_id.commercial_partner_id in partners
            ):
                matches.append(
                    (
                        70,
                        platform,
                        False,
                        _("Known platform partner"),
                        "partner",
                    ),
                )
                continue
            matching_keywords = [
                keyword
                for keyword in platform._bank_keywords()
                if keyword in normalized
            ]
            if matching_keywords:
                matches.append(
                    (
                        40,
                        platform,
                        False,
                        _("Keyword: %s", ", ".join(matching_keywords)),
                        "keyword",
                    ),
                )
        if not matches:
            return (
                self.env["usl.platform.billing.platform"],
                False,
                0,
                _("No configured recognition rule matched"),
                "none",
                "none",
            )
        best_score = max(score for score, *_rest in matches)
        best = [match for match in matches if match[0] == best_score]
        if len(best) > 1:
            return (
                self.env["usl.platform.billing.platform"],
                False,
                best_score,
                _("Ambiguous between: %s", ", ".join(item[1].name for item in best)),
                "ambiguous",
                "ambiguous",
            )
        score, platform, reference, reason, rule = best[0]
        confidence = "high" if score >= 100 else "medium" if score >= 70 else "low"
        return platform, reference, score, reason, confidence, rule

    def _populate_payout_candidates(self, keep_selection=False):
        self.ensure_one()
        if self.mode != "link":
            self.payout_candidate_ids = [Command.clear()]
            return
        previous = {
            line.payout_id.id: line
            for line in self.payout_candidate_ids
            if keep_selection
        }
        payouts = self.env["usl.platform.billing.payout"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("state", "in", self._linkable_payout_states()),
            ],
            order="payout_date, id",
        ).filtered(
            lambda payout: (
                payout.platform_currency_id
                and not payout.platform_currency_id.is_zero(
                    payout.remaining_platform_amount,
                )
            ),
        )
        self.payout_candidate_ids = [Command.clear()] + [
            Command.create(
                {
                    "selected": (
                        previous[payout.id].selected
                        if payout.id in previous
                        else payout.session_id == self.session_id
                    ),
                    "payout_id": payout.id,
                    "allocated_payout_amount": (
                        min(
                            previous[payout.id].allocated_payout_amount,
                            payout.remaining_platform_amount,
                        )
                        if payout.id in previous
                        else payout.remaining_platform_amount
                    ),
                },
            )
            for payout in payouts
        ]

    @staticmethod
    def _ledger_counterpart_lines(moves):
        return moves.line_ids.filtered(
            lambda line: line.account_id.account_type in SETTLEMENT_ACCOUNT_TYPES,
        )

    def _payout_reconciliation_reach(self, payouts):
        """Receivable and payable lines reconciled with each payout's documents.

        Returns ``{payout id: account.move.line}``. The walk starts from the
        settlement lines of the payout's customer invoices and compensation
        moves, including the other payouts of the same compensation (the pool
        ``_reconcile_bank_transaction`` reconciles as one). From there it
        follows partial reconciliations and, inside each reached move, the
        other settlement lines, for a small fixed number of hops. A bank
        transaction reconciled through an intermediate entry is therefore
        found, while one reconciled against unrelated documents is not.
        """
        Partial = self.env["account.partial.reconcile"]
        reach = {}
        for payout in payouts:
            pool = payout
            for compensation in payout.compensation_move_id:
                pool |= compensation.platform_billing_payout_ids
            reached = self._ledger_counterpart_lines(
                pool.customer_invoice_id | pool.compensation_move_id,
            )
            frontier = reached
            for _hop in range(RECONCILIATION_HOPS):
                if not frontier:
                    break
                partials = Partial.search(
                    [
                        "|",
                        ("debit_move_id", "in", frontier.ids),
                        ("credit_move_id", "in", frontier.ids),
                    ],
                )
                neighbours = partials.debit_move_id | partials.credit_move_id
                neighbours |= self._ledger_counterpart_lines(neighbours.move_id)
                frontier = neighbours - reached
                reached |= frontier
            reach[payout.id] = reached
        return reach

    def _line_connected_to_payout(self, bank_line, payout, reach):
        """Whether the bank line was reconciled against the payout's documents."""
        connected = reach.get(payout.id)
        return bool(
            connected
            and (self._ledger_counterpart_lines(bank_line.move_id) & connected),
        )

    def _eligible_bank_lines(self, restrict_to=None, reach=None):
        self.ensure_one()
        platforms = self._active_platforms()
        configured_journals = platforms.bank_journal_id
        restrict_journals = bool(platforms) and all(
            platform.bank_journal_id for platform in platforms
        )
        domain = [
            ("company_id", "=", self.company_id.id),
            ("currency_id", "=", self.bank_currency_id.id),
            ("amount", ">", 0),
            ("is_reconciled", "=", self._adopts_reconciled_lines()),
            ("move_id.state", "=", "posted"),
            ("journal_id.type", "=", "bank"),
        ]
        if restrict_journals:
            domain.append(("journal_id", "in", configured_journals.ids))
        adopting = self._adopts_reconciled_lines()
        if adopting:
            # Build the list outward from the selected payouts' documents, not
            # from every reconciled line of the journals.
            if reach is None:
                reach = self._payout_reconciliation_reach(
                    self._selected_payout_lines().payout_id,
                )
            reached = self.env["account.move.line"].union(*reach.values())
            domain.append(("move_id", "in", reached.move_id.ids))
            if restrict_to is not None:
                domain.append(("id", "in", restrict_to.ids))
        bank_lines = self.env["account.bank.statement.line"].search(
            domain,
            order="date desc, id desc",
        )
        allocations = self.env["usl.platform.billing.bank.allocation"].search(
            [("bank_statement_line_id", "in", bank_lines.ids)],
        )
        allocated_by_line = Counter()
        for allocation in allocations:
            allocated_by_line[allocation.bank_statement_line_id.id] += (
                allocation.bank_amount
            )
        eligible = self.env["account.bank.statement.line"]
        remaining_by_line = {}
        fully_allocated = 0
        for bank_line in bank_lines:
            remaining = bank_line.currency_id.round(
                bank_line.amount - allocated_by_line[bank_line.id],
            )
            if remaining <= 0:
                fully_allocated += 1
                continue
            eligible |= bank_line
            remaining_by_line[bank_line.id] = remaining
        if adopting:
            return eligible, remaining_by_line, {}
        excluded = {
            "outgoing": self.env["account.bank.statement.line"].search_count(
                [
                    ("company_id", "=", self.company_id.id),
                    ("amount", "<=", 0),
                    ("is_reconciled", "=", False),
                ],
            ),
            "reconciled": self.env["account.bank.statement.line"].search_count(
                [
                    ("company_id", "=", self.company_id.id),
                    ("amount", ">", 0),
                    ("is_reconciled", "=", True),
                ],
            ),
            "unposted": self.env["account.bank.statement.line"].search_count(
                [
                    ("company_id", "=", self.company_id.id),
                    ("amount", ">", 0),
                    ("is_reconciled", "=", False),
                    ("move_id.state", "!=", "posted"),
                ],
            ),
            "fully_allocated": fully_allocated,
        }
        if restrict_journals:
            excluded["other_journal"] = (
                self.env["account.bank.statement.line"].search_count(
                    [
                        ("company_id", "=", self.company_id.id),
                        ("amount", ">", 0),
                        ("is_reconciled", "=", False),
                        ("move_id.state", "=", "posted"),
                        ("journal_id.type", "=", "bank"),
                        ("journal_id", "not in", configured_journals.ids),
                    ],
                )
            )
        return eligible, remaining_by_line, excluded

    def _selected_payout_lines(self):
        self.ensure_one()
        return self.payout_candidate_ids.filtered("selected")

    def _candidate_match_values(
        self,
        bank_line,
        remaining_bank_amount,
        platform,
        score,
        selected_payout_lines,
    ):
        selected_payouts = selected_payout_lines.payout_id
        amount_difference = 0.0
        date_difference = abs((bank_line.date - self.session_id.period_month).days)
        currency_reason = _("No registered payout selected")
        currency_compatible = True
        if selected_payouts:
            date_difference = min(
                abs((bank_line.date - payout.payout_date).days)
                for payout in selected_payouts
            )
            payout_currencies = selected_payouts.platform_currency_id
            selected_amount = sum(
                selected_payout_lines.mapped("allocated_payout_amount"),
            )
            if len(payout_currencies) != 1:
                currency_compatible = False
                currency_reason = _("Selected payouts use several currencies")
            elif payout_currencies == bank_line.currency_id:
                amount_difference = abs(remaining_bank_amount - selected_amount)
                currency_reason = _("Bank and payout currencies are identical")
            elif (
                bank_line.foreign_currency_id == payout_currencies
                and bank_line.amount_currency
                and bank_line.amount
            ):
                remaining_foreign = payout_currencies.round(
                    bank_line.amount_currency
                    * remaining_bank_amount
                    / bank_line.amount,
                )
                foreign_difference = abs(remaining_foreign - selected_amount)
                amount_difference = bank_line.currency_id.round(
                    abs(bank_line.amount) * foreign_difference
                    / abs(bank_line.amount_currency),
                )
                currency_reason = _(
                    "Bank transaction carries %(currency)s %(amount).2f",
                    currency=payout_currencies.name,
                    amount=remaining_foreign,
                )
            else:
                currency_compatible = False
                currency_reason = _(
                    "No bank countervalue is available for %s",
                    payout_currencies.name,
                )
            selected_platforms = selected_payouts.platform_id
            if platform and platform in selected_platforms:
                score += 20
            elif platform and platform not in selected_platforms:
                score = max(0, score - 20)
            if currency_compatible:
                score += max(0, 30 - int(amount_difference))
            score += max(0, 20 - date_difference)
        tolerance = (
            platform.bank_match_amount_tolerance
            if platform
            else max(
                self._active_platforms().mapped("bank_match_amount_tolerance")
                or [1.0],
            )
        )
        recommended = bool(
            platform
            and currency_compatible
            and (
                not selected_payouts
                or amount_difference <= tolerance
            ),
        )
        return {
            "score": score,
            "recommended": recommended,
            "amount_difference": amount_difference,
            "date_difference": date_difference,
            "currency_reason": currency_reason,
        }

    def _populate_candidates(self):
        self.ensure_one()
        selected_bank_ids = set(
            self.candidate_ids.filtered("selected").bank_statement_line_id.ids,
        )
        platforms = self._active_platforms()
        bank_lines, remaining_by_line, excluded = self._eligible_bank_lines()
        selected_payout_lines = self._selected_payout_lines()
        values_list = []
        if self.mode != "link" and self.candidate_scope == "reconciled":
            bank_lines = bank_lines.browse()
        for bank_line in bank_lines:
            (
                platform,
                reference,
                score,
                reason,
                confidence,
                rule,
            ) = self._detect_platform(bank_line, platforms)
            remaining = remaining_by_line[bank_line.id]
            match_values = self._candidate_match_values(
                bank_line,
                remaining,
                platform,
                score,
                selected_payout_lines,
            )
            if (
                self.candidate_scope == "recommended"
                and not match_values["recommended"]
            ):
                continue
            values_list.append(
                {
                    "selected": bank_line.id in selected_bank_ids,
                    "bank_statement_line_id": bank_line.id,
                    "platform_id": platform.id,
                    "extracted_reference": reference,
                    "confidence": confidence,
                    "match_rule": rule,
                    "detection_reason": reason,
                    "allocated_bank_amount": remaining,
                    "remaining_bank_amount": remaining,
                    **match_values,
                },
            )
        values_list.sort(
            key=lambda values: (
                not values["recommended"],
                -values["score"],
                -values["bank_statement_line_id"],
            ),
        )
        self.candidate_ids = [Command.clear()] + [
            Command.create(values) for values in values_list
        ]
        del excluded

    def _revalidate_candidate(self, candidate, reach=None):
        bank_line = candidate.bank_statement_line_id
        if self._adopts_reconciled_lines():
            # Only this candidate's own line is looked at.
            eligible, remaining_by_line, _excluded = self._eligible_bank_lines(
                restrict_to=bank_line,
                reach=reach,
            )
        else:
            eligible, remaining_by_line, _excluded = self._eligible_bank_lines()
        if bank_line not in eligible:
            if self._adopts_reconciled_lines():
                raise UserError(
                    _(
                        "%s is not a bank transaction already reconciled in "
                        "Accounting against the selected payouts, or it has no "
                        "amount left to link.",
                        bank_line.display_name,
                    ),
                )
            raise UserError(
                _(
                    "%s is no longer an eligible open incoming bank transaction.",
                    bank_line.display_name,
                ),
            )
        remaining = remaining_by_line[bank_line.id]
        if (
            candidate.allocated_bank_amount <= 0
            or bank_line.currency_id.compare_amounts(
                candidate.allocated_bank_amount,
                remaining,
            )
            > 0
        ):
            raise UserError(
                _(
                    "The allocation for %(label)s must be positive and cannot "
                    "exceed the remaining %(amount)s.",
                    label=bank_line.display_name,
                    amount=remaining,
                ),
            )
        return remaining

    @staticmethod
    def _failure_text(bank_line, error):
        return f"{bank_line.display_name}: {error}"

    def _session_action(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.session_id.display_name,
            "res_model": self.session_id._name,
            "res_id": self.session_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def _wizard_action(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Import Bank Transactions"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _result_action(self, imported, failures, adopted_count=0):
        self.ensure_one()
        affected_sessions = imported.session_id
        if affected_sessions:
            affected_sessions._refresh_state()
            body = _(
                "%(count)s bank allocation(s) saved.",
                count=len(imported.bank_allocation_ids),
            )
            if adopted_count:
                body += "<br/>" + _(
                    "%(count)s bank transaction(s) adopted from existing "
                    "Accounting reconciliations. No entry, payment or "
                    "reconciliation was created or changed.",
                    count=adopted_count,
                )
            if failures:
                body += "<br/>" + "<br/>".join(failures)
            for session in affected_sessions:
                session.message_post(body=body, subtype_xmlid="mail.mt_note")
        if failures:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Some bank transactions could not be linked"),
                    "message": "\n".join(failures),
                    "type": "warning",
                    "sticky": True,
                    "next": self._session_action(),
                },
            }
        return self._session_action()

    def _prepare_payout_creation(self, candidate):
        self._revalidate_candidate(candidate)
        bank_line = candidate.bank_statement_line_id
        platform = candidate.platform_id
        reference = candidate.extracted_reference or f"BANK-{bank_line.id}"
        payout = self.env["usl.platform.billing.payout"]
        if platform:
            payout = payout.search(
                [
                    ("company_id", "=", self.company_id.id),
                    ("platform_id", "=", platform.id),
                    ("platform_reference", "=", reference),
                ],
                limit=1,
            )
        if payout and payout.session_id != self.session_id:
            raise UserError(
                _(
                    "Payout reference %(reference)s already belongs "
                    "to session %(session)s.",
                    reference=reference,
                    session=payout.session_id.display_name,
                ),
            )
        return platform, reference, payout

    def action_create_payouts(self):
        self.ensure_one()
        self.session_id._check_operator()
        if self.mode != "create":
            raise UserError(_("Switch to payout creation mode first."))
        if self.candidate_scope == "reconciled":
            raise UserError(
                _(
                    "Transactions already reconciled in Accounting can only be "
                    "linked to registered payouts.",
                ),
            )
        if self.session_id.state not in {"draft", "ready"}:
            raise UserError(
                _("New payouts can only be imported into a draft session."),
            )
        selected = self.candidate_ids.filtered("selected")
        if not selected:
            raise UserError(_("Select at least one bank transaction."))
        imported = self.env["usl.platform.billing.payout"]
        failures = []
        for candidate in selected:
            bank_line = candidate.bank_statement_line_id
            try:
                with self.env.cr.savepoint():
                    platform, reference, payout = self._prepare_payout_creation(
                        candidate,
                    )
                    created_from_bank = not payout
                    if not payout:
                        values = {
                            "session_id": self.session_id.id,
                            "platform_reference": reference,
                            "payout_date": bank_line.date,
                            "net_platform_amount": 0.0,
                        }
                        if platform:
                            values["platform_id"] = platform.id
                        payout = self.env["usl.platform.billing.payout"].create(values)
                    allocation = self.env[
                        "usl.platform.billing.bank.allocation"
                    ].search(
                        [
                            ("payout_id", "=", payout.id),
                            ("bank_statement_line_id", "=", bank_line.id),
                        ],
                        limit=1,
                    )
                    if not allocation:
                        self.env[
                            "usl.platform.billing.bank.allocation"
                        ]._action_create(
                            {
                                "payout_id": payout.id,
                                "bank_statement_line_id": bank_line.id,
                                "bank_amount": candidate.allocated_bank_amount,
                                "payout_amount": 0.0,
                                "score": candidate.score,
                                "amount_difference": candidate.amount_difference,
                                "date_difference": candidate.date_difference,
                                "detection_reason": candidate.detection_reason,
                            },
                        )
                    if (
                        created_from_bank
                        and bank_line.currency_id == self.company_id.currency_id
                    ):
                        payout._workflow_write(
                            {"currency_valuation_method": "bank"},
                        )
                    imported |= payout
            except (IntegrityError, UserError, ValidationError) as error:
                failures.append(self._failure_text(bank_line, error))
        if not imported and failures:
            raise UserError("\n".join(failures))
        return self._result_action(imported, failures)

    def _selected_link_inputs(self):
        payout_lines = self.payout_candidate_ids.filtered("selected")
        candidates = self.candidate_ids.filtered("selected")
        if not payout_lines:
            raise UserError(_("Select at least one registered payout."))
        if not candidates:
            raise UserError(_("Select at least one bank transaction."))
        for payout_line in payout_lines:
            payout = payout_line.payout_id
            if (
                payout.company_id != self.company_id
                or payout.state not in self._linkable_payout_states()
            ):
                raise UserError(
                    _("%s is no longer an open payout.", payout.display_name),
                )
            if (
                payout_line.allocated_payout_amount <= 0
                or payout.platform_currency_id.compare_amounts(
                    payout_line.allocated_payout_amount,
                    payout.remaining_platform_amount,
                )
                > 0
            ):
                raise UserError(
                    _(
                        "The amount selected for %(payout)s must be positive "
                        "and cannot exceed %(remaining)s.",
                        payout=payout.display_name,
                        remaining=payout.remaining_platform_amount,
                    ),
                )
        reach = (
            self._payout_reconciliation_reach(payout_lines.payout_id)
            if self._adopts_reconciled_lines()
            else None
        )
        for candidate in candidates:
            self._revalidate_candidate(candidate, reach=reach)
        return payout_lines, candidates, reach

    def _candidate_payout_capacity(self, candidate, payout_currency):
        bank_line = candidate.bank_statement_line_id
        if payout_currency == bank_line.currency_id:
            return payout_currency.round(candidate.allocated_bank_amount)
        if (
            bank_line.foreign_currency_id == payout_currency
            and bank_line.amount_currency
            and bank_line.amount
        ):
            return payout_currency.round(
                bank_line.amount_currency
                * candidate.allocated_bank_amount
                / bank_line.amount,
            )
        raise UserError(
            _(
                "%(label)s has no %(currency)s countervalue. Link this mixed "
                "currency case separately or reconcile it in Accounting.",
                label=bank_line.display_name,
                currency=payout_currency.name,
            ),
        )

    def _allocation_plan(self, payout_lines, candidates):
        payout_currencies = payout_lines.payout_currency_id
        if len(payout_currencies) != 1:
            raise UserError(
                _(
                    "Selected payouts use several currencies. Link each currency "
                    "separately; the saved links can then be reconciled in Accounting.",
                ),
            )
        payout_currency = payout_currencies
        ordered_payouts = payout_lines.sorted(
            key=lambda line: (line.payout_date, line.payout_id.id),
        )
        requested = {
            line.id: payout_currency.round(line.allocated_payout_amount)
            for line in ordered_payouts
        }
        ordered_candidates = candidates.sorted(
            key=lambda line: (line.bank_date, line.bank_statement_line_id.id),
        )
        capacities = {
            candidate.id: self._candidate_payout_capacity(
                candidate,
                payout_currency,
            )
            for candidate in ordered_candidates
        }
        requested_total = payout_currency.round(sum(requested.values()))
        capacity_total = payout_currency.round(sum(capacities.values()))
        if float_compare(
            requested_total,
            capacity_total,
            precision_rounding=payout_currency.rounding,
        ):
            raise UserError(
                _(
                    "Selected payout amounts total %(payout)s %(currency)s, "
                    "but the selected bank transactions represent %(bank)s "
                    "%(currency)s. Adjust the selected amounts so they match.",
                    payout=requested_total,
                    bank=capacity_total,
                    currency=payout_currency.name,
                ),
            )
        plan = {}
        payout_index = 0
        for candidate in ordered_candidates:
            capacity = capacities[candidate.id]
            bank_remaining = candidate.allocated_bank_amount
            rows = []
            while not payout_currency.is_zero(capacity):
                if payout_index >= len(ordered_payouts):
                    raise UserError(_("The payout allocation plan is incomplete."))
                payout_line = ordered_payouts[payout_index]
                payout_remaining = requested[payout_line.id]
                payout_part = min(capacity, payout_remaining)
                if payout_currency == candidate.bank_currency_id:
                    bank_part = payout_part
                elif payout_currency.compare_amounts(payout_part, capacity) == 0:
                    bank_part = bank_remaining
                else:
                    bank_part = candidate.bank_currency_id.round(
                        candidate.allocated_bank_amount
                        * payout_part
                        / capacities[candidate.id],
                    )
                rows.append(
                    {
                        "payout_id": payout_line.payout_id.id,
                        "bank_statement_line_id": (
                            candidate.bank_statement_line_id.id
                        ),
                        "bank_amount": bank_part,
                        "payout_amount": payout_part,
                        "score": candidate.score,
                        "amount_difference": candidate.amount_difference,
                        "date_difference": candidate.date_difference,
                        "detection_reason": candidate.detection_reason,
                    },
                )
                capacity = payout_currency.round(capacity - payout_part)
                bank_remaining = candidate.bank_currency_id.round(
                    bank_remaining - bank_part,
                )
                requested[payout_line.id] = payout_currency.round(
                    payout_remaining - payout_part,
                )
                if payout_currency.is_zero(requested[payout_line.id]):
                    payout_index += 1
            if not candidate.bank_currency_id.is_zero(bank_remaining):
                rows[-1]["bank_amount"] = candidate.bank_currency_id.round(
                    rows[-1]["bank_amount"] + bank_remaining,
                )
            plan[candidate.id] = rows
        return plan

    def _get_or_create_allocation(self, values):
        Allocation = self.env["usl.platform.billing.bank.allocation"]
        existing = Allocation.search(
            [
                ("payout_id", "=", values["payout_id"]),
                (
                    "bank_statement_line_id",
                    "=",
                    values["bank_statement_line_id"],
                ),
            ],
            limit=1,
        )
        if not existing:
            return Allocation._action_create(values), True
        if (
            existing.bank_currency_id.compare_amounts(
                existing.bank_amount,
                values["bank_amount"],
            )
            or existing.payout_currency_id.compare_amounts(
                existing.payout_amount,
                values["payout_amount"],
            )
        ):
            raise UserError(
                _(
                    "An allocation already exists with different amounts.",
                ),
            )
        return existing, False

    def _completed_existing_link_plan(self):
        """Return payouts when a repeated request is already fully applied."""
        payout_lines = self.payout_candidate_ids.filtered("selected")
        candidates = self.candidate_ids.filtered("selected")
        if not payout_lines or not candidates:
            return self.env["usl.platform.billing.payout"]
        allocations = self.env[
            "usl.platform.billing.bank.allocation"
        ].search(
            [
                ("payout_id", "in", payout_lines.payout_id.ids),
                (
                    "bank_statement_line_id",
                    "in",
                    candidates.bank_statement_line_id.ids,
                ),
            ],
        )
        if not allocations:
            return self.env["usl.platform.billing.payout"]
        for payout_line in payout_lines:
            payout_allocations = allocations.filtered(
                lambda allocation, payout=payout_line.payout_id: (
                    allocation.payout_id == payout
                ),
            )
            if payout_line.payout_currency_id.compare_amounts(
                sum(payout_allocations.mapped("payout_amount")),
                payout_line.allocated_payout_amount,
            ):
                return self.env["usl.platform.billing.payout"]
        for candidate in candidates:
            bank_allocations = allocations.filtered(
                lambda allocation, bank_line=candidate.bank_statement_line_id: (
                    allocation.bank_statement_line_id == bank_line
                ),
            )
            if candidate.bank_currency_id.compare_amounts(
                sum(bank_allocations.mapped("bank_amount")),
                candidate.allocated_bank_amount,
            ):
                return self.env["usl.platform.billing.payout"]
        return allocations.payout_id

    def action_link_payouts(self):
        self.ensure_one()
        self.session_id._check_operator()
        if self.mode != "link":
            raise UserError(_("Switch to registered-payout linking mode first."))
        completed_payouts = self._completed_existing_link_plan()
        if completed_payouts:
            completed_payouts.session_id._refresh_state()
            return self._session_action()
        payout_lines, candidates, reach = self._selected_link_inputs()
        plan = self._allocation_plan(payout_lines, candidates)
        adopting = reach is not None
        if adopting:
            # An adopted allocation cannot be removed afterwards, so every
            # planned row is checked before any allocation is created.
            self._check_plan_connections(plan, candidates, reach)
        imported = self.env["usl.platform.billing.payout"]
        failures = []
        adopted_count = 0
        # Adopting is all or nothing: the outer savepoint undoes the rows of
        # earlier bank lines when a later one fails.
        with self.env.cr.savepoint():
            for candidate in candidates:
                bank_line = candidate.bank_statement_line_id
                try:
                    with self.env.cr.savepoint():
                        self._revalidate_candidate(candidate, reach=reach)
                        created_any = False
                        for values in plan[candidate.id]:
                            allocation, created = self._get_or_create_allocation(
                                values,
                            )
                            created_any = created_any or created
                            imported |= allocation.payout_id
                        if adopting and created_any:
                            adopted_count += 1
                except (IntegrityError, UserError, ValidationError) as error:
                    if adopting:
                        raise UserError(self._failure_text(bank_line, error)) from error
                    failures.append(self._failure_text(bank_line, error))
        if not imported and failures:
            raise UserError("\n".join(failures))
        return self._result_action(imported, failures, adopted_count)

    def _check_plan_connections(self, plan, candidates, reach):
        """Refuse the whole request if any planned row is not connected."""
        payouts = self.env["usl.platform.billing.payout"]
        for candidate in candidates:
            bank_line = candidate.bank_statement_line_id
            for values in plan[candidate.id]:
                payout = payouts.browse(values["payout_id"])
                if not self._line_connected_to_payout(bank_line, payout, reach):
                    raise UserError(
                        _(
                            "%(bank)s is not connected to the Accounting "
                            "documents of %(payout)s.",
                            bank=bank_line.display_name,
                            payout=payout.display_name,
                        ),
                    )


class UslPlatformBillingBankImportWizardPayoutLine(models.TransientModel):
    _name = "usl.platform.billing.bank.import.wizard.payout.line"
    _description = "Open Platform Payout Candidate"
    _order = "payout_date, id"

    wizard_id = fields.Many2one(
        "usl.platform.billing.bank.import.wizard",
        required=True,
        ondelete="cascade",
    )
    selected = fields.Boolean()
    payout_id = fields.Many2one(
        "usl.platform.billing.payout",
        required=True,
        readonly=True,
    )
    session_id = fields.Many2one(related="payout_id.session_id", readonly=True)
    platform_id = fields.Many2one(related="payout_id.platform_id", readonly=True)
    payout_date = fields.Date(related="payout_id.payout_date", readonly=True)
    platform_reference = fields.Char(
        related="payout_id.platform_reference",
        readonly=True,
    )
    validation_status = fields.Selection(
        related="payout_id.validation_status",
        readonly=True,
    )
    bank_match_status = fields.Selection(
        related="payout_id.bank_match_status",
        readonly=True,
    )
    payout_currency_id = fields.Many2one(
        related="payout_id.platform_currency_id",
        readonly=True,
    )
    remaining_payout_amount = fields.Monetary(
        related="payout_id.remaining_platform_amount",
        currency_field="payout_currency_id",
        readonly=True,
    )
    allocated_payout_amount = fields.Monetary(
        string="Amount to Settle",
        currency_field="payout_currency_id",
        required=True,
    )

    def action_select(self):
        self.ensure_one()
        self.wizard_id.session_id._check_operator()
        self.selected = True
        self.wizard_id._populate_candidates()
        return self.wizard_id._wizard_action()

    def action_unselect(self):
        self.ensure_one()
        self.wizard_id.session_id._check_operator()
        self.selected = False
        self.wizard_id._populate_candidates()
        return self.wizard_id._wizard_action()


class UslPlatformBillingBankImportWizardLine(models.TransientModel):
    _name = "usl.platform.billing.bank.import.wizard.line"
    _description = "Platform Bank Transaction Candidate"
    _order = "recommended desc, score desc, bank_date desc, id"

    wizard_id = fields.Many2one(
        "usl.platform.billing.bank.import.wizard",
        required=True,
        ondelete="cascade",
    )
    selected = fields.Boolean()
    bank_statement_line_id = fields.Many2one(
        "account.bank.statement.line",
        required=True,
        readonly=True,
    )
    bank_date = fields.Date(
        related="bank_statement_line_id.date",
        readonly=True,
    )
    bank_journal_id = fields.Many2one(
        related="bank_statement_line_id.journal_id",
        readonly=True,
    )
    bank_currency_id = fields.Many2one(
        related="bank_statement_line_id.currency_id",
        readonly=True,
    )
    bank_amount = fields.Monetary(
        related="bank_statement_line_id.amount",
        currency_field="bank_currency_id",
        readonly=True,
    )
    remaining_bank_amount = fields.Monetary(
        string="Available",
        currency_field="bank_currency_id",
        readonly=True,
    )
    allocated_bank_amount = fields.Monetary(
        string="Amount to Allocate",
        currency_field="bank_currency_id",
        required=True,
    )
    bank_foreign_currency_id = fields.Many2one(
        related="bank_statement_line_id.foreign_currency_id",
        readonly=True,
    )
    bank_foreign_amount = fields.Monetary(
        related="bank_statement_line_id.amount_currency",
        currency_field="bank_foreign_currency_id",
        readonly=True,
    )
    bank_partner_id = fields.Many2one(
        "res.partner",
        related="bank_statement_line_id.partner_id",
        readonly=True,
    )
    bank_label = fields.Char(
        compute="_compute_bank_label",
    )
    platform_id = fields.Many2one(
        "usl.platform.billing.platform",
        string="Suggested Platform",
    )
    platform_currency_id = fields.Many2one(
        related="platform_id.currency_id",
        readonly=True,
    )
    extracted_reference = fields.Char()
    recommendation_marker = fields.Char(
        string="Match",
        compute="_compute_recommendation_marker",
        help="A green dot marks a transaction suggested by the configured recognition rules.",
    )
    confidence = fields.Selection(
        [
            ("high", "High"),
            ("medium", "Medium"),
            ("low", "Low"),
            ("ambiguous", "Ambiguous"),
            ("none", "None"),
        ],
        readonly=True,
    )
    match_rule = fields.Selection(
        [
            ("regex", "Configured pattern"),
            ("partner", "Known partner"),
            ("keyword", "Keyword"),
            ("ambiguous", "Ambiguous"),
            ("none", "No rule"),
        ],
        readonly=True,
    )
    recommended = fields.Boolean(readonly=True)
    score = fields.Integer(readonly=True)
    detection_reason = fields.Text(readonly=True)
    currency_reason = fields.Text(readonly=True)
    amount_difference = fields.Monetary(
        currency_field="bank_currency_id",
        readonly=True,
    )
    date_difference = fields.Integer(readonly=True)

    @api.depends("bank_statement_line_id.payment_ref", "bank_statement_line_id.name")
    def _compute_bank_label(self):
        for candidate in self:
            candidate.bank_label = (
                candidate.bank_statement_line_id.payment_ref
                or candidate.bank_statement_line_id.name
            )

    @api.depends("recommended")
    def _compute_recommendation_marker(self):
        for candidate in self:
            candidate.recommendation_marker = "●" if candidate.recommended else False
