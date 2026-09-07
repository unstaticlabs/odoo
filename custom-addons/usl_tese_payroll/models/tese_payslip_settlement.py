"""Bank candidate ranking, settlement bridges and reconciliation of salary and TESE payments."""

import hashlib
from datetime import timedelta

from odoo import Command, _, models
from odoo.exceptions import UserError

from .constants import TESE_INTERNAL_WRITE_TOKEN
from .tese_payslip import _normalized


class UslTesePayslip(models.Model):
    _inherit = "usl.tese.payslip"

    def _debt_lines(self, kind):
        self.ensure_one()
        roles = {"salary"} if kind == "salary" else {"social", "income_tax"}
        account_ids = self.component_line_ids.filtered(
            lambda component: component.role in roles,
        ).account_id.ids
        return self.move_id.line_ids.filtered(
            lambda line: (
                line.account_id.id in account_ids
                and line.credit > 0
            ),
        )

    def _residual_status(self):
        self.ensure_one()
        salary_lines = self._tracked_liability_lines("salary")
        tese_lines = self._tracked_liability_lines("tese")
        salary_open = sum(abs(line.amount_residual) for line in salary_lines)
        tese_open = sum(abs(line.amount_residual) for line in tese_lines)
        salary_ok = bool(self._debt_lines("salary")) and self.currency_id.is_zero(
            salary_open,
        )
        tese_ok = bool(self._debt_lines("tese")) and self.currency_id.is_zero(
            tese_open,
        )
        return salary_ok, tese_ok, salary_open, tese_open

    def _candidate_label(self, line):
        return " ".join(filter(None, [
            line.name,
            line.ref,
            line.move_id.ref,
            line.move_id.name,
            line.partner_id.display_name,
        ]))

    def _candidate_is_safe(self, kind, line, expected_date):
        label = _normalized(self._candidate_label(line))
        if kind == "salary":
            partner = self.employee_partner_snapshot_id
            identity_tokens = {
                _normalized(self.employee_snapshot_name),
                _normalized(self.tese_reference),
            }
            max_days = 45
        else:
            partner = self.collector_partner_id
            identity_tokens = {
                "tese",
                "urssaf",
                _normalized(self.tese_reference),
            }
            max_days = 90
        identity_match = (
            bool(partner and line.partner_id == partner)
            or any(token and token in label for token in identity_tokens)
        )
        return (
            identity_match
            and bool(line.date and expected_date)
            and abs((line.date - expected_date).days) <= max_days
        )

    def _rank_candidates(self, kind):
        self.ensure_one()
        if kind == "salary":
            expected = self.net_paid
            detailed_total = expected
            expected_date = self.payment_date
            partner = self.employee_partner_snapshot_id
            tokens = {_normalized(self.employee_snapshot_name)}
        else:
            detailed_total = self.tese_detailed_total
            expected = self.tese_bank_amount or detailed_total
            expected_date = self.tese_payment_date
            partner = self.collector_partner_id
            tokens = {"tese", "urssaf"}
        if not expected_date or self.currency_id.is_zero(expected):
            return []
        date_from = expected_date - timedelta(days=90)
        date_to = expected_date + timedelta(days=90)
        lines = self.env["account.move.line"].search([
            ("company_id", "=", self.company_id.id),
            ("move_id.state", "=", "posted"),
            ("journal_id.type", "in", ("bank", "cash")),
            ("date", ">=", date_from),
            ("date", "<=", date_to),
            ("balance", ">", 0),
            ("reconciled", "=", False),
            ("account_id.reconcile", "=", True),
            ("move_id", "!=", self.move_id.id),
        ])
        ranked = []
        for line in lines:
            if line.currency_id and line.currency_id != self.currency_id:
                continue
            residual = abs(line.amount_residual)
            difference = residual - expected
            settlement_difference = residual - detailed_total
            absolute_difference = abs(difference)
            if (
                absolute_difference > 5.0
                or (
                    kind == "tese"
                    and abs(settlement_difference) > 5.0
                )
            ):
                continue
            exact = self.currency_id.is_zero(difference)
            score = 100 if exact else 60 if absolute_difference <= 1 else 20
            day_difference = abs((line.date - expected_date).days)
            if day_difference <= 3:
                score += 50
            elif day_difference <= 10:
                score += 30
            elif day_difference <= 31:
                score += 10
            else:
                score -= 20
            label = _normalized(self._candidate_label(line))
            if partner and line.partner_id == partner:
                score += 60
            elif any(token and token in label for token in tokens):
                score += 30
            if _normalized(self.tese_reference) in label:
                score += 20
            fingerprint = hashlib.sha256(
                "|".join(map(str, [
                    line.id,
                    line.write_date,
                    residual,
                    line.partner_id.id,
                    expected,
                    expected_date,
                ])).encode(),
            ).hexdigest()
            ranked.append({
                "line": line,
                "amount": residual,
                "difference": difference,
                "settlement_difference": settlement_difference,
                "exact": exact,
                "safe": (
                    (exact or kind == "tese")
                    and self._candidate_is_safe(
                        kind,
                        line,
                        expected_date,
                    )
                ),
                "score": score,
                "fingerprint": fingerprint,
            })
        return sorted(
            ranked,
            key=lambda candidate: (
                -candidate["score"],
                abs(candidate["difference"]),
                candidate["line"].date,
                candidate["line"].id,
            ),
        )

    def _candidate_values(self, kind, ranked):
        prefix = "salary_payment" if kind == "salary" else "tese_payment"
        best = ranked[0] if ranked else False
        values = {
            f"{prefix}_candidate_count": len(ranked),
            f"{prefix}_match_score": best["score"] if best else 0,
            f"{prefix}_best_line_id": best["line"].id if best else False,
            f"{prefix}_candidate_date": best["line"].date if best else False,
            f"{prefix}_candidate_amount": best["amount"] if best else 0,
            f"{prefix}_candidate_difference": (
                best["settlement_difference"]
                if best and kind == "tese"
                else best["difference"] if best else 0
            ),
            f"{prefix}_candidate_label": (
                self._candidate_label(best["line"]) if best else False
            ),
        }
        safe = [candidate for candidate in ranked if candidate["safe"]]
        if not ranked:
            message = _(
                "No candidate found. Next: check the expected date and amount, "
                "or open Bank Matching.",
            )
        elif len(safe) > 1:
            message = _(
                "%(count)s plausible candidates found. Review them in Bank Matching.",
                count=len(safe),
            )
        elif len(safe) != 1:
            message = _(
                "The best candidate is not a unique safe match. Review it "
                "in Bank Matching.",
            )
        elif (
            kind == "tese"
            and not self.currency_id.is_zero(
                safe[0]["settlement_difference"],
            )
        ):
            message = _(
                "One URSSAF debit is ready. Its %(difference).2f difference "
                "will be carried forward on 431000. Next: check the bank "
                "transaction, then match it.",
                difference=abs(safe[0]["settlement_difference"]),
            )
        else:
            message = _(
                "One unique exact safe candidate is available. Next: check it, "
                "then use the matching button above.",
            )
        values[f"{prefix}_match_message"] = message
        return values

    def action_refresh_candidates(self):
        self.ensure_one()
        self._check_workflow_access()
        if not self.move_id or self.move_id.state != "posted":
            raise UserError(_("Post the payroll journal entry first."))
        salary_ok, tese_ok, _salary_open, tese_open = self._residual_status()
        carryover_only = self._is_tese_carryover_only(tese_open)
        tese_settled = tese_ok or carryover_only
        salary_ranked = [] if salary_ok else self._rank_candidates("salary")
        tese_ranked = [] if tese_settled else self._rank_candidates("tese")
        values = {
            **self._candidate_values("salary", salary_ranked),
            **self._candidate_values("tese", tese_ranked),
            "salary_payment_reconciled": salary_ok,
            "tese_payment_reconciled": tese_settled,
            "state": "paid" if salary_ok and tese_settled else "to_reconcile",
        }
        if salary_ok:
            values["salary_payment_match_message"] = _("Salary payment matched.")
        if tese_settled:
            values["tese_payment_match_message"] = _("URSSAF debit matched.")
        if (
            salary_ok
            and carryover_only
        ):
            values["bank_reconcile_message"] = self._tese_carryover_message()
        else:
            values["bank_reconcile_message"] = _(
                "Payments refreshed. Salary: %(salary)s URSSAF: %(tese)s",
                salary=values["salary_payment_match_message"],
                tese=values["tese_payment_match_message"],
            )
        self.with_context(
            _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
        ).write(values)
        return self._notify(values["bank_reconcile_message"])

    def _unique_safe_candidate(self, kind):
        self.ensure_one()
        ranked = self._rank_candidates(kind)
        safe = [
            candidate
            for candidate in ranked
            if candidate["safe"] and (candidate["exact"] or kind == "tese")
        ]
        if len(safe) != 1:
            raise UserError(_(
                "Automatic reconciliation requires one unique safe candidate. "
                "Refresh suggestions and use Bank Matching for ambiguity or "
                "larger differences.",
            ))
        candidate = safe[0]
        line = candidate["line"].exists()
        if (
            not line
            or line.reconciled
            or line.move_id.state != "posted"
            or not line.account_id.reconcile
            or not self.currency_id.is_zero(
                abs(line.amount_residual) - candidate["amount"],
            )
        ):
            raise UserError(_(
                "The candidate changed or was reconciled. Refresh suggestions.",
            ))
        return candidate

    def _create_settlement_bridge(self, kind, candidate, debt_lines):
        self.ensure_one()
        line = candidate["line"]
        journal = self.company_id.tese_payroll_journal_id
        if not journal:
            raise UserError(_("Configure the TESE Payroll Journal first."))
        expected = self.net_paid if kind == "salary" else candidate["amount"]
        if not all(
            component.account_id.reconcile
            for component in self.component_line_ids.filtered(
                lambda component: (
                    component.role == "salary"
                    if kind == "salary"
                    else component.role in {"social", "income_tax"}
                ),
            )
        ):
            raise UserError(_("Every settled liability account must be reconcilable."))
        debt_residual = sum(abs(item.amount_residual) for item in debt_lines)
        declared = self.net_paid if kind == "salary" else self.tese_detailed_total
        if not self.currency_id.is_zero(debt_residual - declared):
            raise UserError(_(
                "The current liability residual no longer equals the expected "
                "amount. Refresh and review the ledger.",
            ))
        rounding_difference = expected - declared
        if kind == "tese" and abs(rounding_difference) > 5.0:
            raise UserError(_(
                "The URSSAF difference exceeds the €5 safety limit. Use Bank "
                "Matching.",
            ))
        label = _(
            "%(kind)s settlement — %(payroll)s",
            kind=_("Salary") if kind == "salary" else _("TESE"),
            payroll=self.name,
        )
        commands = []
        rounding_account = self.component_line_ids.filtered(
            lambda component: component.code == "431000",
        ).account_id
        for debt_line in debt_lines:
            amount = abs(debt_line.amount_residual)
            if (
                kind == "tese"
                and debt_line.account_id == rounding_account
                and rounding_difference < 0
            ):
                amount += rounding_difference
                if amount < 0:
                    raise UserError(_(
                        "The negative URSSAF difference exceeds the 431000 "
                        "liability. Use Bank Matching.",
                    ))
            if self.currency_id.is_zero(amount):
                continue
            commands.append(Command.create({
                "name": label,
                "account_id": debt_line.account_id.id,
                "partner_id": debt_line.partner_id.id,
                "debit": amount,
                "credit": 0.0,
            }))
        if kind == "tese" and rounding_difference > 0:
            if not rounding_account:
                raise UserError(_("The 431000 payroll account is missing."))
            commands.append(Command.create({
                "name": _("URSSAF rounding to clear — %(payroll)s", payroll=self.name),
                "account_id": rounding_account.id,
                "partner_id": self.collector_partner_id.id,
                "debit": rounding_difference,
                "credit": 0.0,
            }))
        commands.append(Command.create({
            "name": label,
            "account_id": line.account_id.id,
            "partner_id": line.partner_id.id,
            "debit": 0.0,
            "credit": expected,
        }))
        move = self.env["account.move"].create({
            "move_type": "entry",
            "company_id": self.company_id.id,
            "journal_id": journal.id,
            "date": line.date,
            "ref": label,
            "tese_payslip_id": self.id,
            "tese_move_role": (
                "salary_settlement" if kind == "salary" else "tese_settlement"
            ),
            "line_ids": commands,
        })
        move.action_post()
        bridge_credit = move.line_ids.filtered(
            lambda item: item.account_id == line.account_id and item.credit > 0,
        )
        if len(bridge_credit) != 1:
            raise UserError(_("The settlement bridge suspense line is invalid."))
        (line + bridge_credit).reconcile()
        for account in debt_lines.account_id:
            original = debt_lines.filtered(lambda item: item.account_id == account)
            bridge = move.line_ids.filtered(
                lambda item: item.account_id == account and item.debit > 0,
            )
            (original + bridge).reconcile()
        return move

    def _reconcile_candidate(self, kind):
        self.ensure_one()
        self._check_workflow_access()
        if not self.move_id or self.move_id.state != "posted":
            raise UserError(_("Post the payroll journal entry first."))
        candidate = self._unique_safe_candidate(kind)
        debt_lines = self._debt_lines(kind).filtered(
            lambda line: (
                not line.reconciled
                and not self.currency_id.is_zero(line.amount_residual)
            ),
        )
        if not debt_lines:
            raise UserError(_("The selected liability is already settled."))
        if kind == "tese":
            bank_difference = (
                candidate["amount"] - self.tese_detailed_total
            )
            self.with_context(
                _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
            ).write({
                "tese_bank_amount": candidate["amount"],
                "tese_bank_difference": bank_difference,
            })
        line = candidate["line"]
        if (
            kind == "salary"
            and len(debt_lines) == 1
            and line.account_id == debt_lines.account_id
        ):
            (debt_lines + line).reconcile()
            settlement_move = False
        else:
            settlement_move = self._create_settlement_bridge(
                kind,
                candidate,
                debt_lines,
            )
        field_name = (
            "salary_settlement_move_id"
            if kind == "salary"
            else "tese_settlement_move_id"
        )
        if settlement_move:
            self.with_context(
                _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
            ).write({
                field_name: settlement_move.id,
            })
        self.action_refresh_candidates()
        self.action_finalize(notify=False)
        if not self.currency_id.is_zero(self.rounding_open_amount):
            next_step = self._tese_carryover_message()
        elif self.state == "paid":
            next_step = _("The payroll is settled; no further action is required.")
        else:
            next_step = _("Next: match the remaining open payment.")
        return self._notify(
            _(
                "The %(kind)s payment was reconciled. %(next_step)s",
                kind=kind.upper(),
                next_step=next_step,
            ),
        )

    def action_reconcile_salary(self):
        return self._reconcile_candidate("salary")

    def action_reconcile_tese(self):
        return self._reconcile_candidate("tese")

    def action_open_matching_lines(self):
        self.ensure_one()
        ids = (
            self.move_id.line_ids
            | self.salary_payment_best_line_id
            | self.tese_payment_best_line_id
            | self.salary_settlement_move_id.line_ids
            | self.tese_settlement_move_id.line_ids
        ).ids
        return {
            "type": "ir.actions.act_window",
            "name": _("TESE Payroll Matching Items"),
            "res_model": "account.move.line",
            "view_mode": "list,form",
            "domain": [("id", "in", ids)],
            "context": {"create": False},
        }

    def action_open_bank_matching(self):
        self.ensure_one()
        best = self.tese_payment_best_line_id or self.salary_payment_best_line_id
        journal = best.journal_id if best else False
        statement_line = best.statement_line_id
        action = self.env["ir.actions.actions"]._for_xml_id(
            "account_reconcile_oca.action_bank_statement_line_reconcile",
        )
        if statement_line:
            domain = [("id", "=", statement_line.id)]
        elif journal:
            domain = [("journal_id", "=", journal.id)]
        else:
            domain = [("company_id", "=", self.company_id.id)]
        context = {
            "allowed_company_ids": [self.company_id.id],
            "create": False,
            "search_default_not_reconciled": True,
            "view_ref": (
                "account_reconcile_oca."
                "bank_statement_line_form_reconcile_view"
            ),
        }
        if journal:
            context.update({
                "active_id": journal.id,
                "active_ids": journal.ids,
                "active_model": journal._name,
                "default_journal_id": journal.id,
            })
        action.update({
            "name": _("Bank Matching — %(payroll)s", payroll=self.name),
            "domain": domain,
            "context": context,
        })
        return action
