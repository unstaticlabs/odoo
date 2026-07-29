import re
import unicodedata
from collections import defaultdict

from odoo import _, fields, models

from odoo.addons.account.tools import normalize_account_number


_GENERIC_BANK_LABEL_TOKENS = {
    "achat",
    "card",
    "carte",
    "creditor",
    "debtor",
    "facture",
    "invoice",
    "name",
    "paiement",
    "payment",
    "refund",
    "remboursement",
    "sepa",
    "transfer",
    "virement",
}
_AUTO_ASSIGN_CONFIDENCE = 90


def _normalize_text(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(
        character
        for character in value
        if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def _stable_label(value):
    tokens = [
        token
        for token in _normalize_text(value).split()
        if (
            len(token) >= 4
            and not any(character.isdigit() for character in token)
            and token not in _GENERIC_BANK_LABEL_TOKENS
        )
    ]
    return " ".join(tokens)


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    rebuild_partner_suggestion_id = fields.Many2one(
        "res.partner",
        string="Suggested partner",
        check_company=True,
        copy=False,
        readonly=True,
        help=(
            "Partner suggested from exact bank details, an exact declared "
            "counterparty name, or consistent reconciled transaction history."
        ),
    )
    rebuild_partner_suggestion_confidence = fields.Integer(
        string="Confidence",
        copy=False,
        readonly=True,
    )
    rebuild_partner_suggestion_source = fields.Selection(
        selection=[
            ("bank_account", "Exact bank account"),
            ("partner_name", "Declared partner name"),
            ("reconciled_label", "Reconciled transaction label"),
            ("reconciled_pattern", "Reconciled transaction pattern"),
        ],
        string="Suggestion source",
        copy=False,
        readonly=True,
    )
    rebuild_partner_suggestion_reason = fields.Char(
        string="Partner matching evidence",
        copy=False,
        readonly=True,
    )
    rebuild_partner_auto_assigned = fields.Boolean(
        string="Partner inferred automatically",
        copy=False,
        readonly=True,
    )

    def _rebuild_partner_inference_cache(self):
        company_ids = self.company_id.ids
        Partner = self.env["res.partner"].sudo().with_context(active_test=False)
        partners = Partner.search([
            ("active", "=", True),
            ("company_id", "in", [False, *company_ids]),
        ])
        partner_names = defaultdict(set)
        for partner in partners:
            commercial_partner = partner.commercial_partner_id
            if (
                commercial_partner.company_id
                and commercial_partner.company_id not in self.company_id
            ):
                continue
            for name in {
                partner.name,
                partner.complete_name,
                commercial_partner.name,
                commercial_partner.complete_name,
            }:
                if normalized_name := _normalize_text(name):
                    partner_names[
                        (
                            commercial_partner.company_id.id or False,
                            normalized_name,
                        )
                    ].add(commercial_partner.id)

        bank_accounts = defaultdict(set)
        for bank_account in self.env["res.partner.bank"].sudo().search([
            ("active", "=", True),
            ("company_id", "in", [False, *company_ids]),
        ]):
            commercial_partner = bank_account.partner_id.commercial_partner_id
            if (
                commercial_partner.active
                and (
                    not commercial_partner.company_id
                    or commercial_partner.company_id in self.company_id
                )
            ):
                normalized_account = normalize_account_number(
                    bank_account.sanitized_account_number
                    or bank_account.account_number,
                )
                if normalized_account:
                    bank_accounts[
                        (bank_account.company_id.id or False, normalized_account)
                    ].add(commercial_partner.id)

        history_exact = defaultdict(list)
        history_pattern = defaultdict(list)
        history = self.search([
            ("company_id", "in", company_ids),
            ("is_reconciled", "=", True),
            ("partner_id", "!=", False),
            ("payment_ref", "!=", False),
        ])
        for historical_line in history:
            partner_id = historical_line.partner_id.commercial_partner_id.id
            exact_label = _normalize_text(historical_line.payment_ref)
            stable_label = _stable_label(historical_line.payment_ref)
            if exact_label:
                history_exact[
                    (historical_line.company_id.id, exact_label)
                ].append(partner_id)
            if stable_label:
                history_pattern[
                    (historical_line.company_id.id, stable_label)
                ].append(partner_id)

        return {
            "partner_names": partner_names,
            "bank_accounts": bank_accounts,
            "history_exact": history_exact,
            "history_pattern": history_pattern,
        }

    def _rebuild_partner_signals(self, cache):
        self.ensure_one()
        signals = []

        normalized_account = normalize_account_number(self.account_number)
        if normalized_account:
            partner_ids = set()
            for company_key in (False, self.company_id.id):
                partner_ids.update(
                    cache["bank_accounts"].get(
                        (company_key, normalized_account),
                        set(),
                    ),
                )
            if len(partner_ids) == 1:
                signals.append({
                    "partner_id": partner_ids.pop(),
                    "confidence": 100,
                    "source": "bank_account",
                    "reason": _("Exact active bank-account ownership"),
                })

        normalized_partner_name = _normalize_text(self.partner_name)
        if normalized_partner_name:
            exact_partner_ids = set()
            for company_key in (False, self.company_id.id):
                exact_partner_ids.update(
                    cache["partner_names"].get(
                        (company_key, normalized_partner_name),
                        set(),
                    ),
                )
            if len(exact_partner_ids) == 1:
                signals.append({
                    "partner_id": next(iter(exact_partner_ids)),
                    "confidence": 98,
                    "source": "partner_name",
                    "reason": _("Exact declared counterparty name"),
                })
            elif len(normalized_partner_name) >= 4:
                partial_partner_ids = set()
                for (
                    company_key,
                    known_name,
                ), partner_ids in cache["partner_names"].items():
                    if (
                        company_key in (False, self.company_id.id)
                        and (
                            normalized_partner_name in known_name
                            or known_name in normalized_partner_name
                        )
                    ):
                        partial_partner_ids.update(partner_ids)
                if len(partial_partner_ids) == 1:
                    signals.append({
                        "partner_id": partial_partner_ids.pop(),
                        "confidence": 75,
                        "source": "partner_name",
                        "reason": _("Unique partial counterparty-name match"),
                    })

        exact_label = _normalize_text(self.payment_ref)
        exact_history = cache["history_exact"].get(
            (self.company_id.id, exact_label),
            [],
        )
        exact_partner_ids = set(exact_history)
        if exact_label and len(exact_partner_ids) == 1:
            history_count = len(exact_history)
            signals.append({
                "partner_id": next(iter(exact_partner_ids)),
                "confidence": 96 if history_count >= 2 else 82,
                "source": "reconciled_label",
                "reason": _(
                    "Same label matched %(count)s time(s) to one partner",
                    count=history_count,
                ),
            })

        stable_label = _stable_label(self.payment_ref)
        pattern_history = cache["history_pattern"].get(
            (self.company_id.id, stable_label),
            [],
        )
        pattern_partner_ids = set(pattern_history)
        if (
            stable_label
            and len(pattern_history) >= 2
            and len(pattern_partner_ids) == 1
        ):
            history_count = len(pattern_history)
            signals.append({
                "partner_id": next(iter(pattern_partner_ids)),
                "confidence": 92 if history_count >= 3 else 84,
                "source": "reconciled_pattern",
                "reason": _(
                    "Stable label pattern matched %(count)s time(s) to one partner",
                    count=history_count,
                ),
            })

        return signals

    def _rebuild_best_partner_suggestion(self, cache):
        self.ensure_one()
        signals = self._rebuild_partner_signals(cache)
        if not signals:
            return False

        by_partner = defaultdict(list)
        for signal in signals:
            by_partner[signal["partner_id"]].append(signal)
        ranked = sorted(
            (
                (
                    max(signal["confidence"] for signal in partner_signals),
                    partner_id,
                    partner_signals,
                )
                for partner_id, partner_signals in by_partner.items()
            ),
            reverse=True,
        )
        confidence, partner_id, partner_signals = ranked[0]
        if len(ranked) > 1 and ranked[1][0] == confidence:
            return False

        conflicting_reliable_signal = any(
            other_partner_id != partner_id
            and other_confidence >= _AUTO_ASSIGN_CONFIDENCE
            for other_confidence, other_partner_id, _other_signals in ranked
        )
        if conflicting_reliable_signal:
            confidence = min(confidence, _AUTO_ASSIGN_CONFIDENCE - 1)

        strongest_signal = max(
            partner_signals,
            key=lambda signal: signal["confidence"],
        )
        reasons = list(dict.fromkeys(
            signal["reason"]
            for signal in sorted(
                partner_signals,
                key=lambda signal: signal["confidence"],
                reverse=True,
            )
        ))
        return {
            "partner_id": partner_id,
            "confidence": confidence,
            "source": strongest_signal["source"],
            "reason": _(
                "%(confidence)s%% — %(evidence)s",
                confidence=confidence,
                evidence="; ".join(reasons),
            ),
            "auto_assign": confidence >= _AUTO_ASSIGN_CONFIDENCE,
        }

    def _rebuild_refresh_partner_suggestions(self):
        eligible = self.filtered(
            lambda line: not line.is_reconciled and not line.partner_id
        )
        stats = {"reviewed": len(self), "suggested": 0, "assigned": 0}
        if not eligible:
            return stats

        cache = eligible._rebuild_partner_inference_cache()
        for line in eligible:
            suggestion = line._rebuild_best_partner_suggestion(cache)
            values = {
                "rebuild_partner_suggestion_id": False,
                "rebuild_partner_suggestion_confidence": 0,
                "rebuild_partner_suggestion_source": False,
                "rebuild_partner_suggestion_reason": False,
                "rebuild_partner_auto_assigned": False,
            }
            if suggestion:
                stats["suggested"] += 1
                values.update({
                    "rebuild_partner_suggestion_id": suggestion["partner_id"],
                    "rebuild_partner_suggestion_confidence": (
                        suggestion["confidence"]
                    ),
                    "rebuild_partner_suggestion_source": suggestion["source"],
                    "rebuild_partner_suggestion_reason": suggestion["reason"],
                    "rebuild_partner_auto_assigned": suggestion["auto_assign"],
                })
                if suggestion["auto_assign"]:
                    values["partner_id"] = suggestion["partner_id"]
                    stats["assigned"] += 1
            line.with_context(
                rebuild_skip_partner_inference=True,
            ).write(values)
        return stats

    def _retrieve_partner(self):
        if self.env.context.get("skip_retrieve_partner"):
            return
        return self._rebuild_refresh_partner_suggestions()

    def action_rebuild_refresh_partner_suggestions(self):
        lines = self
        if not lines:
            lines = self.search([
                ("company_id", "in", self.env.companies.ids),
                ("is_reconciled", "=", False),
                ("partner_id", "=", False),
            ])
        stats = lines._rebuild_refresh_partner_suggestions()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Partner suggestions refreshed"),
                "message": _(
                    "%(reviewed)s transaction(s) reviewed: "
                    "%(assigned)s reliable partner(s) inferred automatically "
                    "from %(suggested)s evidence-backed suggestion(s).",
                    **stats,
                ),
                "type": "success",
                "sticky": False,
            },
        }

    def action_rebuild_apply_partner_suggestion(self):
        for line in self.filtered(
            lambda candidate: (
                not candidate.is_reconciled
                and not candidate.partner_id
                and candidate.rebuild_partner_suggestion_id
            ),
        ):
            line.with_context(
                rebuild_skip_partner_inference=True,
            ).write({
                "partner_id": line.rebuild_partner_suggestion_id.id,
                "rebuild_partner_auto_assigned": False,
            })
        return True

    def write(self, values):
        if self.env.context.get("rebuild_skip_partner_inference"):
            return super().write(values)

        manual_partner_change = "partner_id" in values
        identity_change = bool(
            {"account_number", "company_id", "partner_name", "payment_ref"}
            & values.keys()
        )
        result = super().write(values)
        if manual_partner_change:
            self.with_context(
                rebuild_skip_partner_inference=True,
            ).write({
                "rebuild_partner_suggestion_id": False,
                "rebuild_partner_suggestion_confidence": 0,
                "rebuild_partner_suggestion_source": False,
                "rebuild_partner_suggestion_reason": False,
                "rebuild_partner_auto_assigned": False,
            })
        elif identity_change:
            self._rebuild_refresh_partner_suggestions()
        return result


class AccountJournal(models.Model):
    _inherit = "account.journal"

    def _rebuild_statement_line_action(self, *, matching=False):
        if matching:
            self.env["account.bank.statement.line"].search([
                ("journal_id", "in", self.ids),
                ("is_reconciled", "=", False),
                ("partner_id", "=", False),
            ])._rebuild_refresh_partner_suggestions()
        return super()._rebuild_statement_line_action(matching=matching)
