from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class UslPlatformBillingBankImportWizard(models.TransientModel):
    _name = "usl.platform.billing.bank.import.wizard"
    _description = "Select Platform Bank Transactions"

    session_id = fields.Many2one(
        "usl.platform.billing.session",
        required=True,
        readonly=True,
    )
    candidate_ids = fields.One2many(
        "usl.platform.billing.bank.import.wizard.line",
        "wizard_id",
        string="Candidates",
    )
    candidate_count = fields.Integer(compute="_compute_counts")
    selected_count = fields.Integer(compute="_compute_counts")

    @api.depends("candidate_ids", "candidate_ids.selected")
    def _compute_counts(self):
        for wizard in self:
            wizard.candidate_count = len(wizard.candidate_ids)
            wizard.selected_count = len(wizard.candidate_ids.filtered("selected"))

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

    def _detect_platform(self, bank_line, platforms):
        label = self._line_label(bank_line)
        normalized = label.casefold()
        matches = []
        for platform in platforms:
            if platform.bank_journal_id and platform.bank_journal_id != bank_line.journal_id:
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
                matches.append((70, platform, False, _("Known platform partner")))
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
                    ),
                )
        if not matches:
            return self.env["usl.platform.billing.platform"], False, 0, _("No platform match"), "none"
        best_score = max(score for score, _platform, _ref, _reason in matches)
        best = [match for match in matches if match[0] == best_score]
        if len(best) > 1:
            return (
                self.env["usl.platform.billing.platform"],
                False,
                best_score,
                _("Ambiguous between: %s", ", ".join(item[1].name for item in best)),
                "ambiguous",
            )
        score, platform, reference, reason = best[0]
        confidence = "high" if score >= 100 else "medium" if score >= 70 else "low"
        return platform, reference, score, reason, confidence

    def _match_payout(self, bank_line, platform, reference):
        base_domain = [
            ("session_id", "=", self.session_id.id),
            ("platform_id", "=", platform.id),
            ("bank_statement_line_id", "=", False),
            ("state", "in", ("draft", "generated", "posted")),
        ]
        domain = list(base_domain)
        if reference:
            domain.append(("platform_reference", "=", reference))
        payouts = self.env["usl.platform.billing.payout"].search(domain)
        linked_payouts = self.env["usl.platform.billing.payout"].search(
            [("bank_statement_line_id", "=", bank_line.id)],
        )
        if (
            reference
            and not payouts
            and reference in linked_payouts.mapped("platform_reference")
        ):
            payouts = self.env["usl.platform.billing.payout"].search(
                base_domain,
            )
        remaining_bank_amount = bank_line.currency_id.round(
            bank_line.amount
            - sum(linked_payouts.mapped("bank_received_amount")),
        )
        if remaining_bank_amount <= 0:
            return self.env["usl.platform.billing.payout"], 0.0, 0, False
        ranked = []
        for payout in payouts:
            date_difference = abs((bank_line.date - payout.payout_date).days)
            delayed_posted_receipt = (
                self.session_id.state in {"posted", "paid"}
                and bank_line.date >= payout.payout_date
            )
            if (
                not delayed_posted_receipt
                and date_difference > platform.bank_match_days_tolerance
            ):
                continue
            expected_bank_amount = payout.bank_received_amount
            if (
                not expected_bank_amount
                and payout.platform_currency_id == payout.bank_currency_id
            ):
                expected_bank_amount = payout.net_platform_amount
            amount_difference = (
                abs(remaining_bank_amount - expected_bank_amount)
                if expected_bank_amount
                else 0.0
            )
            if (
                expected_bank_amount
                and expected_bank_amount - remaining_bank_amount
                > platform.bank_match_amount_tolerance
            ):
                continue
            amount_bonus = (
                max(
                    0,
                    20
                    - int(
                        amount_difference
                        / max(platform.bank_match_amount_tolerance, 0.01)
                        * 20,
                    ),
                )
                if expected_bank_amount
                else 0
            )
            ranked.append(
                (
                    amount_bonus
                    + max(0, platform.bank_match_days_tolerance - date_difference),
                    payout,
                    amount_difference,
                    date_difference,
                ),
            )
        if not ranked:
            return self.env["usl.platform.billing.payout"], 0.0, 0, False
        ranked.sort(key=lambda item: (-item[0], item[1].id))
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            return self.env["usl.platform.billing.payout"], 0.0, 0, True
        _bonus, payout, amount_difference, date_difference = ranked[0]
        return payout, amount_difference, date_difference, False

    def _populate_candidates(self):
        self.ensure_one()
        session = self.session_id
        start, end = session._period_bounds()
        platforms = self.env["usl.platform.billing.platform"].search(
            [
                ("company_id", "=", session.company_id.id),
                ("active", "=", True),
            ],
        )
        tolerance = max(platforms.mapped("bank_match_days_tolerance") or [15])
        bank_domain = [
            ("company_id", "=", session.company_id.id),
            ("date", ">=", start - relativedelta(days=tolerance)),
            ("amount", ">", 0),
            ("is_reconciled", "=", False),
        ]
        if session.state not in {"posted", "paid"}:
            bank_domain.append(
                ("date", "<=", end + relativedelta(days=tolerance)),
            )
        bank_lines = self.env["account.bank.statement.line"].search(
            bank_domain,
            order="date, id",
        )
        candidate_values = []
        for bank_line in bank_lines:
            platform, reference, score, reason, confidence = self._detect_platform(
                bank_line,
                platforms,
            )
            if not platform:
                continue
            (
                existing_payout,
                amount_difference,
                date_difference,
                ambiguous_payout,
            ) = self._match_payout(bank_line, platform, reference)
            if ambiguous_payout:
                continue
            linked_payouts = self.env["usl.platform.billing.payout"].search(
                [("bank_statement_line_id", "=", bank_line.id)],
            )
            if (
                reference
                and not existing_payout
                and reference not in linked_payouts.mapped("platform_reference")
            ):
                # A reference extracted from a configured regex must identify a
                # payout inside the configured tolerances.
                continue
            remaining_bank_amount = bank_line.currency_id.round(
                bank_line.amount
                - sum(linked_payouts.mapped("bank_received_amount")),
            )
            if remaining_bank_amount <= 0:
                continue
            if existing_payout:
                score += max(
                    0,
                    platform.bank_match_days_tolerance - date_difference,
                )
                if existing_payout.bank_received_amount:
                    score += max(
                        0,
                        20
                        - int(
                            amount_difference
                            / max(platform.bank_match_amount_tolerance, 0.01)
                            * 20,
                        ),
                    )
            else:
                date_difference = abs((bank_line.date - start).days)
            allocated_amount = remaining_bank_amount
            if existing_payout:
                if existing_payout.bank_received_amount:
                    allocated_amount = existing_payout.bank_received_amount
                elif existing_payout.platform_currency_id == self.session_id.bank_currency_id:
                    allocated_amount = min(
                        existing_payout.net_platform_amount,
                        remaining_bank_amount,
                    )
                elif (
                    bank_line.foreign_currency_id
                    == existing_payout.platform_currency_id
                    and bank_line.amount_currency
                ):
                    allocated_amount = min(
                        remaining_bank_amount,
                        bank_line.currency_id.round(
                            bank_line.amount
                            / bank_line.amount_currency
                            * existing_payout.net_platform_amount,
                        ),
                    )
            candidate_values.append(
                {
                    "selected": confidence in {"high", "medium"},
                    "bank_statement_line_id": bank_line.id,
                    "platform_id": platform.id,
                    "existing_payout_id": existing_payout.id,
                    "extracted_reference": reference,
                    "confidence": confidence,
                    "score": score,
                    "detection_reason": reason,
                    "allocated_bank_amount": allocated_amount,
                    "amount_difference": amount_difference,
                    "date_difference": date_difference,
                },
            )
        reserved_payout_ids = set()
        for values in sorted(
            candidate_values,
            key=lambda item: (-item["score"], item["bank_statement_line_id"]),
        ):
            payout_id = values["existing_payout_id"]
            if payout_id and payout_id in reserved_payout_ids:
                values.update(
                    {
                        "selected": False,
                        "confidence": "ambiguous",
                        "detection_reason": _(
                            "Another higher-ranked bank transaction already "
                            "targets this payout.",
                        ),
                    },
                )
            elif payout_id and values["selected"]:
                reserved_payout_ids.add(payout_id)
        commands = [Command.clear()] + [
            Command.create(values) for values in candidate_values
        ]
        self.candidate_ids = commands

    def action_import(self):
        self.ensure_one()
        self.session_id._check_operator()
        if self.session_id.state not in {"draft", "ready", "posted"}:
            raise UserError(
                _("Bank transactions can only be linked to draft, ready or posted sessions."),
            )
        selected = self.candidate_ids.filtered("selected")
        if not selected:
            raise UserError(_("Select at least one unambiguous bank transaction."))
        imported = self.env["usl.platform.billing.payout"]
        for candidate in selected:
            if candidate.confidence == "ambiguous" or not candidate.platform_id:
                raise UserError(
                    _("Resolve ambiguous platform candidates before importing."),
                )
            bank_line = candidate.bank_statement_line_id
            payout = candidate.existing_payout_id
            self.env.cr.execute(
                """
                SELECT id
                  FROM account_bank_statement_line
                 WHERE id = %s
                   FOR UPDATE
                """,
                [bank_line.id],
            )
            bank_line.invalidate_recordset()
            if bank_line.is_reconciled:
                raise UserError(
                    _(
                        "The bank transaction %(label)s was reconciled while "
                        "this selection was open.",
                        label=bank_line.display_name,
                    ),
                )
            linked_payouts = self.env["usl.platform.billing.payout"].search(
                [("bank_statement_line_id", "=", bank_line.id)],
            )
            remaining_bank_amount = bank_line.currency_id.round(
                bank_line.amount
                - sum(linked_payouts.mapped("bank_received_amount")),
            )
            if (
                candidate.allocated_bank_amount <= 0
                or bank_line.currency_id.compare_amounts(
                    candidate.allocated_bank_amount,
                    remaining_bank_amount,
                )
                > 0
            ):
                raise UserError(
                    _(
                        "The allocation for %(label)s must be positive and "
                        "cannot exceed the bank transaction's unallocated amount.",
                        label=bank_line.display_name,
                    ),
                )
            values = {
                "bank_statement_line_id": bank_line.id,
                "bank_received_amount": candidate.allocated_bank_amount,
                "bank_match_status": "selected",
                "bank_match_score": candidate.score,
                "bank_amount_difference": candidate.amount_difference,
                "bank_date_difference": candidate.date_difference,
                "bank_detection_reason": candidate.detection_reason,
            }
            if payout:
                payout._workflow_write(values)
            else:
                reference = candidate.extracted_reference or f"BANK-{bank_line.id}"
                platform_amount = (
                    bank_line.amount_currency
                    if bank_line.foreign_currency_id == candidate.platform_id.currency_id
                    else bank_line.amount
                    if candidate.platform_id.currency_id == self.session_id.bank_currency_id
                    else 0.0
                )
                if not platform_amount:
                    raise UserError(
                        _(
                            "Enter the foreign-currency payout amount before importing %(label)s.",
                            label=bank_line.display_name,
                        ),
                    )
                payout = self.env["usl.platform.billing.payout"].create(
                    {
                        **values,
                        "session_id": self.session_id.id,
                        "platform_id": candidate.platform_id.id,
                        "platform_reference": reference,
                        "payout_date": bank_line.date,
                        "net_platform_amount": platform_amount,
                    },
                )
            imported |= payout
        self.session_id.message_post(
            body=_("%s bank transaction(s) linked to platform payouts.", len(imported)),
            subtype_xmlid="mail.mt_note",
        )
        return {"type": "ir.actions.act_window_close"}


class UslPlatformBillingBankImportWizardLine(models.TransientModel):
    _name = "usl.platform.billing.bank.import.wizard.line"
    _description = "Platform Bank Transaction Candidate"
    _order = "score desc, bank_date, id"

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
    bank_currency_id = fields.Many2one(
        "res.currency",
        related="bank_statement_line_id.currency_id",
        readonly=True,
    )
    bank_amount = fields.Monetary(
        related="bank_statement_line_id.amount",
        currency_field="bank_currency_id",
        readonly=True,
    )
    bank_partner_id = fields.Many2one(
        "res.partner",
        related="bank_statement_line_id.partner_id",
        readonly=True,
    )
    bank_label = fields.Char(
        related="bank_statement_line_id.payment_ref",
        readonly=True,
    )
    platform_id = fields.Many2one(
        "usl.platform.billing.platform",
        readonly=True,
    )
    existing_payout_id = fields.Many2one(
        "usl.platform.billing.payout",
        readonly=True,
    )
    extracted_reference = fields.Char(readonly=True)
    allocated_bank_amount = fields.Monetary(
        string="Allocated Bank Amount",
        currency_field="bank_currency_id",
        required=True,
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
    score = fields.Integer(readonly=True)
    detection_reason = fields.Text(readonly=True)
    amount_difference = fields.Monetary(
        currency_field="bank_currency_id",
        readonly=True,
    )
    date_difference = fields.Integer(readonly=True)
