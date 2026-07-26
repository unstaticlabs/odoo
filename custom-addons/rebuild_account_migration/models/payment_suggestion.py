import re
from datetime import date

from odoo import _, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    @staticmethod
    def _rebuild_normalize_payment_reference(value):
        return re.sub(r"[^A-Z0-9]", "", (value or "").upper())

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

        bill_date = self.invoice_date or self.date
        if bill_date and line.date:
            distance = abs((line.date - bill_date).days)
            if distance <= 7:
                score += 10
                reasons.append(_("Date within 7 days"))
            elif distance <= 31:
                score += 5
                reasons.append(_("Date within 31 days"))

        if line.payment_id or line.move_id.statement_line_id:
            score += 5
            reasons.append(_("Native payment"))

        confidence = "high" if score >= 70 else "medium" if score >= 30 else "low"
        return score, confidence, " · ".join(reasons)

    def _compute_payments_widget_to_reconcile_info(self):
        super()._compute_payments_widget_to_reconcile_info()

        for move in self.filtered(
            lambda item: item.move_type in ("in_invoice", "in_refund", "in_receipt")
            and item.invoice_outstanding_credits_debits_widget
        ):
            widget = dict(move.invoice_outstanding_credits_debits_widget)
            content = [dict(candidate) for candidate in widget.get("content", [])]
            lines_by_id = {
                line.id: line
                for line in self.env["account.move.line"].browse(
                    [candidate["id"] for candidate in content]
                ).exists()
            }
            target_amount = (
                abs(move.amount_residual)
                if move.state == "posted"
                else abs(move.amount_total)
            )
            for candidate in content:
                line = lines_by_id.get(candidate["id"])
                if not line:
                    continue
                score, confidence, reason = move._rebuild_payment_suggestion_score(
                    line,
                    candidate,
                    target_amount,
                )
                candidate.update({
                    "can_assign": move.state == "posted",
                    "match_confidence": confidence,
                    "match_reason": reason,
                    "match_score": score,
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
        if self.is_invoice(include_receipts=True):
            if self.state != "posted":
                raise UserError(
                    _("Post the bill before matching an existing payment.")
                )
            available_line_ids = {
                candidate["id"]
                for candidate in (
                    self.invoice_outstanding_credits_debits_widget or {}
                ).get("content", [])
            }
            if line_id not in available_line_ids:
                raise UserError(
                    _(
                        "This payment is no longer an eligible suggestion. "
                        "Refresh the bill and review the remaining amount."
                    )
                )
        return super().js_assign_outstanding_line(line_id)
