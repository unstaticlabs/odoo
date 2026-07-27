import re
from datetime import date, timedelta

from odoo import _, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    @staticmethod
    def _rebuild_normalize_payment_reference(value):
        return re.sub(r"[^A-Z0-9]", "", (value or "").upper())

    def _rebuild_payment_candidate_amount(self, line):
        self.ensure_one()
        if line.currency_id == self.currency_id:
            return abs(line.amount_residual_currency)
        return line.company_currency_id._convert(
            abs(line.amount_residual),
            self.currency_id,
            self.company_id,
            line.date,
        )

    def _rebuild_payment_date_facts(self, line):
        self.ensure_one()
        reference_dates = [
            (label, reference_date)
            for label, reference_date in (
                (_("due date"), self.invoice_date_due),
                (_("bill date"), self.invoice_date or self.date),
            )
            if reference_date
        ]
        if not line.date or not reference_dates:
            return {"distance": False, "label": False, "date": False}
        label, reference_date = min(
            reference_dates,
            key=lambda item: abs((line.date - item[1]).days),
        )
        return {
            "distance": abs((line.date - reference_date).days),
            "label": label,
            "date": fields.Date.to_string(reference_date),
        }

    def _rebuild_payment_partner_facts(self, line):
        self.ensure_one()
        bill_partner = self.commercial_partner_id
        statement_line = line.move_id.statement_line_id
        assigned_partner = (
            line.partner_id or statement_line.partner_id
        ).commercial_partner_id
        suggested_partner = (
            statement_line.rebuild_partner_suggestion_id.commercial_partner_id
            if statement_line
            else self.env["res.partner"]
        )
        suggestion_confidence = (
            statement_line.rebuild_partner_suggestion_confidence
            if statement_line
            else 0
        )
        suggestion_source = (
            statement_line.rebuild_partner_suggestion_source
            if statement_line
            else False
        )
        suggestion_reason = (
            statement_line.rebuild_partner_suggestion_reason
            if statement_line
            else False
        )

        status = "missing"
        score = 0
        reassignment_required = not assigned_partner
        if assigned_partner == bill_partner:
            status = (
                "inferred"
                if statement_line.rebuild_partner_auto_assigned
                else "same"
            )
            score = (
                min(20, 8 + suggestion_confidence // 10)
                if status == "inferred"
                else 20
            )
            reassignment_required = False
        elif assigned_partner:
            status = "different"
            score = -20
            reassignment_required = True
        elif suggested_partner == bill_partner:
            status = "suggested"
            score = min(15, 5 + suggestion_confidence // 10)
        elif suggested_partner:
            status = "different_suggestion"
            score = -12

        if status == "inferred":
            evidence = _(
                "Partner inferred automatically: %(partner)s "
                "(%(confidence)s%%)",
                partner=bill_partner.display_name,
                confidence=suggestion_confidence,
            )
        elif status == "same":
            evidence = _(
                "Assigned partner matches %(partner)s",
                partner=bill_partner.display_name,
            )
        elif status == "suggested":
            evidence = _(
                "Partner suggested from bank evidence: %(partner)s "
                "(%(confidence)s%%)",
                partner=bill_partner.display_name,
                confidence=suggestion_confidence,
            )
        elif status == "different":
            evidence = _(
                "Assigned partner %(candidate)s differs from bill supplier "
                "%(supplier)s",
                candidate=assigned_partner.display_name,
                supplier=bill_partner.display_name,
            )
        elif status == "different_suggestion":
            evidence = _(
                "Bank evidence suggests %(candidate)s (%(confidence)s%%), "
                "not bill supplier %(supplier)s",
                candidate=suggested_partner.display_name,
                confidence=suggestion_confidence,
                supplier=bill_partner.display_name,
            )
        else:
            evidence = _(
                "No partner assigned; selecting this match will set %(partner)s",
                partner=bill_partner.display_name,
            )

        return {
            "assigned_partner_id": assigned_partner.id,
            "assigned_partner_name": assigned_partner.display_name,
            "suggested_partner_id": suggested_partner.id,
            "suggested_partner_name": suggested_partner.display_name,
            "partner_match_status": status,
            "partner_reassignment_required": reassignment_required,
            "partner_score": score,
            "partner_evidence": evidence,
            "partner_suggestion_confidence": suggestion_confidence,
            "partner_suggestion_source": suggestion_source,
            "partner_suggestion_reason": suggestion_reason,
        }

    def _rebuild_payment_suggestion_score(self, line, candidate, target_amount):
        self.ensure_one()
        score = 0
        reasons = []

        bill_references = {
            self._rebuild_normalize_payment_reference(value)
            for value in (self.ref, self.payment_reference)
            if self._rebuild_normalize_payment_reference(value)
        }
        candidate_references = {
            self._rebuild_normalize_payment_reference(value)
            for value in (
                line.ref,
                line.move_id.ref,
                line.move_id.name,
                line.payment_id.memo,
                line.move_id.statement_line_id.payment_ref,
            )
            if self._rebuild_normalize_payment_reference(value)
        }
        reference_match = any(
            bill_ref == candidate_ref
            or (
                len(bill_ref) >= 5
                and len(candidate_ref) >= 5
                and (bill_ref in candidate_ref or candidate_ref in bill_ref)
            )
            for bill_ref in bill_references
            for candidate_ref in candidate_references
        )
        if reference_match:
            score += 60
            reasons.append(_("Reference match"))

        candidate_amount = candidate["amount"]
        if self.currency_id.compare_amounts(candidate_amount, target_amount) == 0:
            score += 40
            reasons.append(_("Exact amount"))
        elif self.currency_id.compare_amounts(candidate_amount, target_amount) < 0:
            score += 12
            reasons.append(_("Possible partial payment"))
        else:
            score += 6
            reasons.append(_("Larger available payment"))

        if line.currency_id == self.currency_id:
            score += 10
            reasons.append(_("Same currency"))

        date_facts = self._rebuild_payment_date_facts(line)
        distance = date_facts["distance"]
        if distance is not False:
            if distance <= 7:
                score += 15
                reasons.append(_(
                    "Date %(distance)s day(s) from %(reference)s",
                    distance=distance,
                    reference=date_facts["label"],
                ))
            elif distance <= 31:
                score += 8
                reasons.append(_(
                    "Date within 31 days of %(reference)s",
                    reference=date_facts["label"],
                ))
            elif distance <= 45:
                score += 4
                reasons.append(_(
                    "Date within 45 days of %(reference)s",
                    reference=date_facts["label"],
                ))

        if line.payment_id or line.move_id.statement_line_id:
            score += 5
            reasons.append(
                _("Bank transaction")
                if line.move_id.statement_line_id
                else _("Native payment")
            )

        partner_facts = self._rebuild_payment_partner_facts(line)
        score += partner_facts["partner_score"]
        reasons.append(partner_facts["partner_evidence"])
        if partner_facts["partner_suggestion_reason"]:
            reasons.append(partner_facts["partner_suggestion_reason"])

        confidence = "high" if score >= 70 else "medium" if score >= 35 else "low"
        return score, confidence, " · ".join(reasons)

    def _rebuild_close_bank_payment_candidates(self, target_amount):
        self.ensure_one()
        payment_term_lines = self.line_ids.filtered(
            lambda line: (
                line.account_id.account_type
                in ("asset_receivable", "liability_payable")
            )
        )
        target_account = payment_term_lines.account_id[:1]
        if not target_account or not target_amount:
            return []

        reference_dates = [
            reference_date
            for reference_date in (
                self.invoice_date_due,
                self.invoice_date or self.date,
            )
            if reference_date
        ]
        if not reference_dates:
            return []
        date_from = min(reference_dates) - timedelta(days=45)
        date_to = max(reference_dates) + timedelta(days=45)
        direction_operator = "<" if self.is_inbound() else ">"
        candidate_lines = self.env["account.move.line"].search([
            ("move_id.statement_line_id", "!=", False),
            ("parent_state", "=", "posted"),
            *self._check_company_domain(self.company_id),
            ("reconciled", "=", False),
            ("balance", direction_operator, 0.0),
            ("date", ">=", date_from),
            ("date", "<=", date_to),
            "|",
            ("amount_residual", "!=", 0.0),
            ("amount_residual_currency", "!=", 0.0),
        ], order="date desc, id desc", limit=200)
        candidate_lines.invalidate_recordset([
            "amount_residual",
            "amount_residual_currency",
            "reconciled",
        ])

        candidates = []
        for line in candidate_lines:
            statement_line = line.move_id.statement_line_id
            if (
                line.account_id != target_account
                and line.account_id != statement_line.journal_id.suspense_account_id
            ):
                continue
            if not line.account_id.reconcile:
                continue

            amount = self._rebuild_payment_candidate_amount(line)
            if self.currency_id.is_zero(amount):
                continue
            date_distance = self._rebuild_payment_date_facts(line)["distance"]
            if date_distance is False or date_distance > 45:
                continue
            tolerance = max(
                self.currency_id.rounding,
                min(target_amount * 0.05, 25.0),
            )
            if abs(amount - target_amount) > tolerance:
                continue

            candidates.append({
                "move_name": line.move_id.name,
                "amount": amount,
                "currency_id": self.currency_id.id,
                "id": line.id,
                "move_id": line.move_id.id,
                "date": fields.Date.to_string(line.date),
                "account_payment_id": line.payment_id.id,
                "ref": line.ref or statement_line.payment_ref or "",
                "is_bank_statement_candidate": True,
                "bank_statement_line_id": statement_line.id,
                "target_account_id": target_account.id,
                "target_account_name": target_account.display_name,
                "source_account_id": line.account_id.id,
                "source_account_name": line.account_id.display_name,
                "account_reassignment_required": (
                    line.account_id != target_account
                ),
            })
        return candidates

    def _compute_payments_widget_to_reconcile_info(self):
        super()._compute_payments_widget_to_reconcile_info()

        for move in self.filtered(
            lambda item: (
                item.move_type in ("in_invoice", "in_refund", "in_receipt")
                and item.state in ("draft", "posted")
                and item.payment_state in ("not_paid", "partial")
            )
        ):
            widget = dict(move.invoice_outstanding_credits_debits_widget or {
                "outstanding": True,
                "content": [],
                "move_id": move.id,
            })
            content = [dict(candidate) for candidate in widget.get("content", [])]
            target_amount = (
                abs(move.amount_residual)
                if move.state == "posted"
                else abs(move.amount_total)
            )
            existing_line_ids = {candidate["id"] for candidate in content}
            content.extend(
                candidate
                for candidate in move._rebuild_close_bank_payment_candidates(
                    target_amount,
                )
                if candidate["id"] not in existing_line_ids
            )
            lines_by_id = {
                line.id: line
                for line in self.env["account.move.line"].browse(
                    [candidate["id"] for candidate in content]
                ).exists()
            }
            for candidate in content:
                line = lines_by_id.get(candidate["id"])
                if not line:
                    continue
                score, confidence, reason = move._rebuild_payment_suggestion_score(
                    line,
                    candidate,
                    target_amount,
                )
                partner_facts = move._rebuild_payment_partner_facts(line)
                candidate.update({
                    "can_assign": move.state == "posted",
                    "match_confidence": confidence,
                    "match_reason": reason,
                    "match_score": score,
                    **partner_facts,
                })

            content.sort(
                key=lambda candidate: (
                    -candidate.get("match_score", 0),
                    candidate.get("date") or date.max.isoformat(),
                    candidate["id"],
                )
            )
            content = [
                candidate
                for candidate in content
                if candidate.get("match_confidence") != "low"
            ][:6]
            if not content:
                move.invoice_outstanding_credits_debits_widget = False
                continue
            content[0]["is_best_match"] = True
            widget["content"] = content
            widget["title"] = _("Suggested existing payments")
            widget["draft_suggestions"] = move.state == "draft"
            move.invoice_outstanding_credits_debits_widget = widget

    def js_assign_outstanding_line(self, line_id):
        self.ensure_one()
        candidate = False
        if self.is_invoice(include_receipts=True):
            if self.state != "posted":
                raise UserError(
                    _("Post the bill before matching an existing payment.")
                )
            candidates_by_id = {
                item["id"]: item
                for item in (
                    self.invoice_outstanding_credits_debits_widget or {}
                ).get("content", [])
            }
            candidate = candidates_by_id.get(line_id)
            if not candidate:
                raise UserError(
                    _(
                        "This payment is no longer an eligible suggestion. "
                        "Refresh the bill and review the remaining amount."
                    )
                )

        line = self.env["account.move.line"].browse(line_id).exists()
        statement_line = line.move_id.statement_line_id
        reassignment_note = False
        if candidate and candidate.get("is_bank_statement_candidate"):
            if (
                not statement_line
                or statement_line.is_reconciled
                or line.reconciled
            ):
                raise UserError(_(
                    "This bank transaction is no longer available. Refresh "
                    "the bill and review the current suggestions."
                ))
            target_account = self.env["account.account"].browse(
                candidate["target_account_id"],
            ).exists()
            bill_partner = self.commercial_partner_id
            if not target_account or not bill_partner:
                raise UserError(_(
                    "The bill supplier or payable account is no longer "
                    "available. Refresh the bill before matching."
                ))
            if (
                line.account_id != target_account
                and line.account_id
                != statement_line.journal_id.suspense_account_id
            ):
                raise UserError(_(
                    "The bank transaction has already been categorized to "
                    "another account. Review it in Bank Matching."
                ))

            previous_partner = (
                line.partner_id or statement_line.partner_id
            ).commercial_partner_id
            previous_account = line.account_id
            if statement_line.partner_id.commercial_partner_id != bill_partner:
                statement_line.with_context(
                    rebuild_skip_partner_inference=True,
                ).write({"partner_id": bill_partner.id})
            if line.account_id != target_account:
                line.write({
                    "account_id": target_account.id,
                    "partner_id": bill_partner.id,
                })
            elif line.partner_id.commercial_partner_id != bill_partner:
                line.write({"partner_id": bill_partner.id})

            reassignment_note = _(
                "Matched bank transaction %(transaction)s to %(bill)s. "
                "Partner: %(old_partner)s → %(new_partner)s. "
                "Account: %(old_account)s → %(new_account)s. "
                "The recommendation was based on %(evidence)s.",
                transaction=statement_line.display_name,
                bill=self.display_name,
                old_partner=previous_partner.display_name or _("Unassigned"),
                new_partner=bill_partner.display_name,
                old_account=previous_account.display_name,
                new_account=target_account.display_name,
                evidence=candidate.get("match_reason") or _("matching facts"),
            )

        result = super().js_assign_outstanding_line(line_id)
        if reassignment_note:
            self.message_post(body=reassignment_note)
            statement_line.move_id.message_post(body=reassignment_note)
        return result
