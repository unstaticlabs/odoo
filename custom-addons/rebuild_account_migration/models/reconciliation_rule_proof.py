"""Evidence-backed native proposals. Analysis never applies bank matching."""
import hashlib
import json
from collections import defaultdict
from datetime import timedelta

from markupsafe import escape

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import SQL

from .reconciliation_rule_patterns import infer_pattern, shape_key, shape_witness

_PROOF = object()
_PROTECTED = frozenset({
    "rebuild_generated_proof", "rebuild_proof_digest", "rebuild_pattern_shape",
    "rebuild_evidence_statement_line_ids", "rebuild_proposal_key",
    "rebuild_overlap_warning", "rebuild_replay_summary", "rebuild_overlap_rule_ids",
})


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _relation_ids(commands):
    ids = set()
    for command in commands or []:
        if isinstance(command, int):
            ids.add(command)
        elif command[0] == Command.SET:
            ids = set(command[2])
        elif command[0] == Command.LINK:
            ids.add(command[1])
        elif command[0] in (Command.UNLINK, Command.DELETE):
            ids.discard(command[1])
        elif command[0] == Command.CLEAR:
            ids.clear()
    return sorted(ids)


class AccountReconcileModel(models.Model):
    _inherit = "account.reconcile.model"

    rebuild_evidence_count = fields.Integer(string="Supporting transactions", compute="_compute_rebuild_evidence_count")
    rebuild_generated_proof = fields.Boolean(copy=False, readonly=True, index=True)
    rebuild_proof_digest = fields.Char(copy=False, readonly=True)
    rebuild_pattern_shape = fields.Json(copy=False, readonly=True)
    rebuild_overlap_warning = fields.Text(string="Overlap review", copy=False, readonly=True)
    rebuild_overlap_rule_ids = fields.Many2many(
        "account.reconcile.model", "rebuild_rule_overlap_review_rel", "proposal_id", "other_rule_id",
        string="Rules to review", copy=False, readonly=True,
    )
    rebuild_replay_summary = fields.Text(string="Evidence check", copy=False, readonly=True)

    @api.depends("rebuild_evidence_statement_line_ids")
    def _compute_rebuild_evidence_count(self):
        for rule in self:
            rule.rebuild_evidence_count = len(rule.rebuild_evidence_statement_line_ids)

    def _rebuild_proof_scope(self):
        return self.with_context(_rebuild_proof=_PROOF)

    def _rebuild_has_proof_scope(self):
        return self.env.context.get("_rebuild_proof") is _PROOF

    @api.model_create_multi
    def create(self, vals_list):
        if not self._rebuild_has_proof_scope():
            defaults = self.default_get(list(_PROTECTED))
            if any(set(vals) & _PROTECTED for vals in vals_list) or any(defaults.values()):
                raise AccessError(_("Generated evidence is prepared by Find or Recheck evidence."))
        return super().create(vals_list)

    def write(self, vals):
        if not self._rebuild_has_proof_scope():
            if set(vals) & _PROTECTED:
                raise AccessError(_("Generated evidence cannot be replaced through the rule editor."))
            if self.filtered("rebuild_generated_proof"):
                self._rebuild_check_generated_actor()
                if vals.get("rebuild_is_proposal") is False:
                    raise AccessError(_("Use Approve Rule to activate an evidence-backed suggestion."))
        replay_fields = {"company_id", "match_label", "match_label_param", "match_amount",
            "match_amount_min", "match_amount_max", "match_journal_ids", "match_partner_ids", "line_ids"}
        if not self._rebuild_has_proof_scope() and set(vals) & replay_fields and self.filtered(
                lambda rule: rule.rebuild_generated_proof and rule.rebuild_is_proposal):
            vals = dict(vals, rebuild_proof_digest=False,
                        rebuild_replay_summary=_("Settings changed. Recheck evidence before approval."))
        return super().write(vals)

    def _rebuild_invalidate_pending_line_proof(self):
        if not self._rebuild_has_proof_scope():
            pending = self.filtered(lambda rule: rule.rebuild_generated_proof and rule.rebuild_is_proposal)
            if pending:
                pending._rebuild_proof_scope().write({"rebuild_proof_digest": False,
                    "rebuild_replay_summary": _("Settings changed. Recheck evidence before approval.")})

    def copy(self, default=None):
        self.ensure_one()
        if not self.rebuild_generated_proof:
            return super().copy(default=default)
        self._rebuild_check_generated_actor()
        defaults = dict(default or {})
        if set(defaults) & (_PROTECTED | {"rebuild_is_proposal"}):
            raise AccessError(_("A copied generated rule remains a suggestion until its evidence is rechecked and approved."))
        defaults.update({
            "rebuild_generated_proof": True, "rebuild_is_proposal": True,
            "rebuild_proof_digest": False, "rebuild_proposal_key": False,
            "rebuild_pattern_shape": self.rebuild_pattern_shape,
            "rebuild_evidence_statement_line_ids": [Command.set(self.rebuild_evidence_statement_line_ids.ids)],
            "rebuild_proposal_source": self.rebuild_proposal_source,
            "rebuild_proposal_evidence": self.rebuild_proposal_evidence,
            "rebuild_replay_summary": _("Copied suggestion. Recheck evidence before approval."),
        })
        return super(AccountReconcileModel, self._rebuild_proof_scope()).copy(default=defaults).with_env(self.env)

    def _rebuild_check_generated_actor(self):
        self._rebuild_check_rule_manager()
        self.check_access("read")
        if any(rule.company_id != self.env.company for rule in self):
            raise AccessError(_("Switch to the rule's company before reviewing its evidence."))

    def _rebuild_proposal_query_scope(self):
        scope = self.env.context.get("_rebuild_probe_proposals")
        if isinstance(scope, tuple) and len(scope) == 2 and scope[0] is _PROOF and scope[1]:
            return SQL("(%s IS NOT TRUE OR %s IN %s)",
                       SQL.identifier(self._table, "rebuild_is_proposal"),
                       SQL.identifier(self._table, "id"), tuple(scope[1]))
        return SQL("%s IS NOT TRUE", SQL.identifier(self._table, "rebuild_is_proposal"))

    def _rebuild_evidence_lines(self, statement):
        liquidity, suspense, counterparts = statement._seek_for_lines()
        if (statement.company_id != self.env.company or statement.move_id.state != "posted"
                or not statement.is_reconciled or suspense or not liquidity
                or statement.move_id.reversed_entry_id or statement.move_id.reversal_move_ids):
            raise UserError(_("The supporting bank transaction is no longer a surviving reconciled entry. Recheck evidence."))
        if not counterparts or any(line.account_id.account_type in (
                "asset_receivable", "liability_payable", "off_balance")
                or line.matched_debit_ids or line.matched_credit_ids for line in counterparts):
            raise UserError(_("This transaction needs native manual rule configuration; its settlement effect cannot be inferred."))
        return liquidity, counterparts

    def _rebuild_lock_evidence(self, statements):
        statements.check_access("read")
        statements.move_id.check_access("read")
        statements.move_id.line_ids.check_access("read")
        if any(line.company_id != self.env.company for line in statements):
            raise AccessError(_("Supporting transactions must belong to the current company."))
        # Native row locks either protect the snapshot until commit or raise a
        # native serialization failure if a financial writer already changed it.
        statements.sorted("id").lock_for_update()
        statements.move_id.sorted("id").lock_for_update()
        statements.move_id.line_ids.sorted("id").lock_for_update()

    def _rebuild_evidence_snapshot(self, statements):
        rows = []
        for statement in statements.sorted("id"):
            _liquidity, counterparts = self._rebuild_evidence_lines(statement)
            rows.append({
                "statement": statement.read(["date", "amount", "amount_currency", "foreign_currency_id",
                    "payment_ref", "transaction_details", "partner_id", "journal_id", "company_id"], load=None)[0],
                "move": statement.move_id.read(["state", "date", "narration", "reversed_entry_id", "reversal_move_ids"], load=None)[0],
                "lines": statement.move_id.line_ids.sorted("id").read([
                    "account_id", "partner_id", "balance", "amount_currency", "currency_id", "name",
                    "analytic_distribution", "tax_ids", "tax_tag_ids", "tax_repartition_line_id",
                    "group_tax_id", "tax_base_amount", "matched_debit_ids", "matched_credit_ids",
                ], load=None),
                "counterparts": sorted(counterparts.ids),
            })
        return rows

    def _rebuild_native_effect_matches(self, statement):
        """Compare native prospective move values with every original counterpart."""
        self.ensure_one()
        liquidity, counterparts = self._rebuild_evidence_lines(statement)
        data, next_id = [], 1
        for line in liquidity:
            next_id, values = statement._get_reconcile_line(
                line, "liquidity", reconcile_auxiliary_id=next_id, move=True,
            )
            data.extend(values)
        replay, _next_id = statement._reconcile_data_by_model(data, self, next_id)
        prospective = [statement._reconcile_move_line_vals(row) for row in replay if row["kind"] != "liquidity"]
        currency = statement.company_id.currency_id

        def canonical(values):
            balance = currency.round(values.get("balance", values.get("debit", 0) - values.get("credit", 0)))
            currency_id = values.get("currency_id") or currency.id
            line_currency = self.env["res.currency"].browse(currency_id)
            return (
                values["account_id"], values.get("partner_id") or False, balance,
                currency_id, line_currency.round(values.get("amount_currency", balance)),
                json.dumps(values.get("analytic_distribution") or {}, sort_keys=True),
                tuple(_relation_ids(values.get("tax_ids"))), tuple(_relation_ids(values.get("tax_tag_ids"))),
                values.get("tax_repartition_line_id") or False, values.get("group_tax_id") or False,
                currency.round(values.get("tax_base_amount", 0)),
            )
        actual = [canonical({
            "account_id": line.account_id.id, "partner_id": line.partner_id.id,
            "balance": line.balance, "currency_id": line.currency_id.id, "amount_currency": line.amount_currency,
            "analytic_distribution": line.analytic_distribution,
            "tax_ids": line.tax_ids.ids, "tax_tag_ids": line.tax_tag_ids.ids,
            "tax_repartition_line_id": line.tax_repartition_line_id.id, "group_tax_id": line.group_tax_id.id,
            "tax_base_amount": line.tax_base_amount,
        }) for line in counterparts]
        return sorted(map(canonical, prospective), key=repr) == sorted(actual, key=repr)

    def _rebuild_rule_configuration(self):
        self.ensure_one()
        return {
            "company": self.company_id.id, "label": (self.match_label, self.match_label_param),
            "journals": sorted(self.match_journal_ids.ids), "partners": sorted(self.match_partner_ids.ids),
            "amount": (self.match_amount, self.match_amount_min, self.match_amount_max),
            "lines": [{"account": line.account_id.id, "partner": line.partner_id.id,
                "type": line.amount_type, "amount": line.amount_string,
                "taxes": sorted(line.tax_ids.ids), "analytics": line.analytic_distribution,
                "label": line.with_context(lang=None).label} for line in self.line_ids.sorted(lambda line: (line.sequence, line.id))],
        }

    def _rebuild_native_proof_matches(self, statements, rules, trigger):
        """Pure native query preview; archived candidates remain archived."""
        if not statements or not rules:
            return {}
        statements.check_access("read")
        rules.check_access("read")
        statements.flush_recordset()
        self.flush_model()
        probe = self.with_context(_rebuild_probe_proposals=(_PROOF, frozenset(rules.ids)))
        query = probe._get_rules_query(statements, trigger=trigger)
        query.add_where(SQL("%s IN %s", SQL.identifier(self._table, "id"), tuple(rules.ids)))
        self.env.cr.execute(SQL(
            "WITH account_reconcile_model AS (SELECT id, company_id, sequence, trigger, "
            "can_be_proposed, match_label, match_label_param, match_amount, match_amount_min, "
            "match_amount_max, rebuild_is_proposal, CASE WHEN id IN %s THEN TRUE ELSE active END active "
            "FROM public.account_reconcile_model) %s",
            tuple(self.ids), query.select(SQL.identifier(self._table, "id"), SQL.identifier(statements._table, "id")),
        ))
        result = defaultdict(set)
        for rule_id, line_id in self.env.cr.fetchall():
            result[line_id].add(rule_id)
        return result

    def _rebuild_complete_evidence(self):
        self.ensure_one()
        self.check_access("read")
        self.flush_recordset(["rebuild_evidence_statement_line_ids"])
        # Native M2M reads filter inaccessible sources. Check the complete
        # topology of this authorized rule before reading any source content;
        # never turn a hidden source into a smaller, apparently proven cohort.
        self.env.cr.execute(SQL(
            "SELECT statement_line_id FROM rebuild_reconcile_model_statement_evidence_rel "
            "WHERE reconcile_model_id = %s ORDER BY statement_line_id", self.id,
        ))
        statements = self.env["account.bank.statement.line"].browse([row[0] for row in self.env.cr.fetchall()])
        if not statements.has_access("read"):
            raise AccessError(_("You can no longer read every supporting transaction. Restore the appropriate access before reviewing this suggestion."))
        return statements.exists()

    def _rebuild_current_proof(self):
        self.ensure_one()
        self._rebuild_check_generated_actor()
        statements = self._rebuild_complete_evidence()
        if len(statements) < 3:
            raise UserError(_("At least three distinct surviving supporting transactions are required. Recheck evidence."))
        self._rebuild_lock_evidence(statements)
        snapshot = self._rebuild_evidence_snapshot(statements)
        for statement in statements:
            if not self._rebuild_native_effect_matches(statement):
                raise UserError(_("The rule no longer reproduces a supporting transaction's accounting result. Recheck evidence or configure the rule manually."))
        matches = self._rebuild_native_proof_matches(statements, self, self.trigger)
        if any(self.id not in matches.get(line.id, []) for line in statements):
            raise UserError(_("The rule no longer matches every supporting transaction in the native matcher. Recheck evidence."))
        return _digest({"sources": snapshot, "configuration": self._rebuild_rule_configuration()})

    def _rebuild_scope_witness(self, other):
        if self.company_id != other.company_id:
            return False
        journals = self.match_journal_ids or self.env["account.journal"].search([
            ("company_id", "=", self.company_id.id), ("type", "in", ["bank", "cash", "credit"]),
        ])
        if other.match_journal_ids:
            journals &= other.match_journal_ids
        if not journals:
            return False
        partners = self.match_partner_ids
        if partners and other.match_partner_ids and not (partners & other.match_partner_ids):
            return False
        partner = ((partners & other.match_partner_ids) if partners and other.match_partner_ids
                   else partners or other.match_partner_ids)[:1]
        # Use the pinned native signed BETWEEN ordering, including its empty
        # intervals, rather than silently repairing native matching semantics.
        def interval(rule):
            return ((float("-inf"), rule.match_amount_max) if rule.match_amount == "lower" else
                    (rule.match_amount_min, float("inf")) if rule.match_amount == "greater" else
                    (rule.match_amount_max, rule.match_amount_min) if rule.match_amount == "between" else
                    (float("-inf"), float("inf")))
        a, b = interval(self), interval(other)
        low, high = max(a[0], b[0]), min(a[1], b[1])
        if low > high:
            return False
        amount = low if low != float("-inf") else high if high != float("inf") else -1
        return journals[:1], partner, amount

    def _rebuild_native_witness_matches(self, witness, scope, rules):
        """Replay the maintained PG query against an inert CTE, never a new bank row."""
        journals, partner, amount = scope
        statement = self.rebuild_evidence_statement_line_ids._filtered_access("read")[:1]
        if not statement:
            return set()
        rules.check_access("read")
        probe = self.with_context(_rebuild_probe_proposals=(_PROOF, frozenset(rules.ids)))
        query = probe._get_rules_query(statement, trigger=rules[:1].trigger)
        query.add_where(SQL("%s IN %s", SQL.identifier(self._table, "id"), tuple(rules.ids)))
        self.env["account.reconcile.model"].flush_model()
        self.env.cr.execute(SQL(
            "WITH account_bank_statement_line AS (SELECT %s::integer id, %s::integer move_id, "
            "%s::integer company_id, %s::numeric amount, %s::varchar payment_ref, NULL::jsonb transaction_details), "
            "account_move AS (SELECT %s::integer id, %s::integer journal_id, %s::integer partner_id, NULL::text narration) %s",
            statement.id, statement.move_id.id, self.company_id.id, amount, witness,
            statement.move_id.id, journals.id, partner.id or None,
            query.select(SQL.identifier(self._table, "id")),
        ))
        return {row[0] for row in self.env.cr.fetchall()}

    def _rebuild_overlap_assessment(self, only_rules=None):
        self.ensure_one()
        others = self.search([
            ("company_id", "=", self.company_id.id), ("active", "=", True), ("can_be_proposed", "=", True), ("id", "!=", self.id),
            "|", ("rebuild_is_proposal", "=", False), ("rebuild_generated_proof", "=", True),
        ])
        if only_rules is not None:
            others &= only_rules
        statements = self.rebuild_evidence_statement_line_ids._filtered_access("read")
        # Native matching is independent of sequence and output account. Both
        # triggers participate: a manual suggestion can overlap an automatic one.
        matches = {}
        for trigger in ("manual", "auto_reconcile"):
            for line_id, ids in self._rebuild_native_proof_matches(statements, self | others, trigger).items():
                matches.setdefault(line_id, set()).update(ids)
        warnings = []
        for other in others:
            scope = self._rebuild_scope_witness(other)
            if not scope:
                continue
            for statement in statements:
                if other.id in matches.get(statement.id, set()) and self.id in matches.get(statement.id, set()):
                    return other, statement.payment_ref, []
            witness, decided = (None, False)
            if self.rebuild_pattern_shape and other.rebuild_pattern_shape:
                witness, decided = shape_witness(self.rebuild_pattern_shape, other.rebuild_pattern_shape)
            elif not other.match_label:
                witness = statements[:1].payment_ref
            elif other.match_label == "contains":
                # A contains rule may match an unobserved member of a bounded
                # shape. Try its literal and each supported shape representative.
                witness = other.match_label_param
            if witness:
                ids = set()
                for rule in self | other:
                    ids |= self._rebuild_native_witness_matches(witness, scope, rule)
                if {self.id, other.id} <= ids:
                    return other, witness, []
            if decided and witness is None:
                warning = _("“%s”: a future transaction could match both rules through different text fields (label, details or narration).", other.display_name)
            else:
                warning = _("This suggestion could overlap with “%s”. Review its conditions and the supporting examples before approval.", other.display_name)
            warnings.append((other.id, warning))
        return self.browse(), False, warnings

    def _rebuild_overlap_warning_values(self, warnings):
        return {
            "rebuild_overlap_warning": "\n".join(message for _id, message in warnings) or False,
            "rebuild_overlap_rule_ids": [Command.set([rule_id for rule_id, _message in warnings])],
        }

    def _rebuild_validate_rule_graph(self, companies):
        if self._rebuild_has_proof_scope():
            return True
        generated = self.search([
            ("company_id", "in", companies.ids), ("rebuild_generated_proof", "=", True),
            ("rebuild_is_proposal", "=", False), ("active", "=", True),
        ])
        changed = self.exists().filtered(lambda rule: rule.active and not rule.rebuild_is_proposal)
        for rule in generated:
            if rule in self:
                # Only an edited generated rule needs its manager's evidence
                # proof. An ordinary manual author retains native permissions.
                current = rule._rebuild_current_proof()
                if current != rule.rebuild_proof_digest:
                    raise UserError(_("An active generated rule has changed evidence or settings. Archive it and Recheck evidence before activating it again."))
            conflict, witness, _warnings = rule._rebuild_overlap_assessment(
                only_rules=None if rule in self else changed,
            )
            if conflict:
                raise UserError(_("This would overlap the evidence-backed rule “%(rule)s” on “%(example)s”. Keep the rules separate before saving.", rule=rule.display_name, example=witness))
        return True

    def action_rebuild_recheck_evidence(self):
        self.ensure_one()
        self._rebuild_check_generated_actor()
        self.check_access("write")
        if not self.rebuild_is_proposal and self.active:
            raise UserError(_("Archive this rule before rechecking its evidence."))
        scoped = self._rebuild_coordinate_companies(self.company_id)._rebuild_proof_scope()
        inferred = infer_pattern(scoped._rebuild_complete_evidence().mapped("payment_ref"))
        shape = inferred[1] if inferred and scoped.match_label == "match_regex" and scoped.match_label_param == inferred[0] else False
        scoped.write({"rebuild_pattern_shape": shape})
        digest = scoped._rebuild_current_proof()
        conflict, witness, warnings = scoped._rebuild_overlap_assessment()
        if conflict:
            raise UserError(_("This rule overlaps “%(rule)s” on “%(example)s”. Review its scope before continuing.", rule=conflict.display_name, example=witness))
        scoped.write({"rebuild_generated_proof": True, "rebuild_proof_digest": digest,
            **scoped._rebuild_overlap_warning_values(warnings),
            "rebuild_replay_summary": _("All %(count)s supporting transactions match and reproduce their native accounting result.", count=len(scoped.rebuild_evidence_statement_line_ids))})
        return True

    def action_rebuild_approve_proposal(self):
        self.ensure_one()
        if not self.rebuild_generated_proof:
            # Older/manual suggestions keep their existing native manager
            # workflow; only newly generated proofs promise verified replay.
            return super().action_rebuild_approve_proposal()
        self._rebuild_check_generated_actor()
        self.check_access("write")
        if not self.rebuild_is_proposal:
            return True
        if not self.active:
            raise UserError(_("Unarchive this suggestion before approving it. Use Unarchive in the action menu."))
        if not self.rebuild_generated_proof:
            raise UserError(_("Recheck evidence before approving this suggestion."))
        scoped = self._rebuild_coordinate_companies(self.company_id)._rebuild_proof_scope()
        digest = scoped._rebuild_current_proof()
        if digest != scoped.rebuild_proof_digest:
            raise UserError(_("The supporting evidence or rule settings changed. Recheck evidence before approval."))
        conflict, witness, warnings = scoped._rebuild_overlap_assessment()
        if conflict:
            raise UserError(_("This rule overlaps “%(rule)s” on “%(example)s”. Review its scope before approval.", rule=conflict.display_name, example=witness))
        scoped.write({"rebuild_is_proposal": False, "trigger": scoped.rebuild_proposed_trigger or "manual",
                      **scoped._rebuild_overlap_warning_values(warnings)})
        scoped.message_post(body=_("Rule approved after native replay of %(count)s supporting transactions. Uncertain overlaps remain available for review.", count=len(scoped.rebuild_evidence_statement_line_ids)))
        return True

    def action_rebuild_analyze_rule_opportunities(self):
        self._rebuild_check_rule_manager()
        self._rebuild_authoring_access("create", [{"company_id": self.env.company.id}])
        scoped = self._rebuild_coordinate_companies(self.env.company)._rebuild_proof_scope()
        statements = self.env["account.bank.statement.line"].search([
            ("company_id", "=", self.env.company.id), ("move_id.state", "=", "posted"),
            ("is_reconciled", "=", True), ("date", ">=", fields.Date.today() - timedelta(days=730)),
        ], order="id")
        groups = defaultdict(list)
        for statement in statements:
            if len((statement.payment_ref or "").strip()) < 4:
                continue
            try:
                _liquidity, counterparts = scoped._rebuild_evidence_lines(statement)
            except UserError:
                continue
            bases = counterparts.filtered(lambda line: not line.tax_repartition_line_id)
            if len(bases) != 1:
                continue
            base = bases[0]
            key = (statement.journal_id.id, base.account_id.id, statement.partner_id.id,
                   -1 if statement.amount < 0 else 1, shape_key(statement.payment_ref),
                   _digest({"analytics": base.analytic_distribution, "taxes": sorted(base.tax_ids.ids)}))
            groups[key].append((statement, base))
        created = self.browse()
        skipped_rules = self.browse()
        unsupported_count = 0
        refusals = []
        for key, rows in sorted(groups.items(), key=lambda item: (-len(item[1]), repr(item[0]))):
            if len(rows) < 3 or len(created) >= 12:
                continue
            lines = self.env["account.bank.statement.line"].browse([row[0].id for row in rows])
            scoped._rebuild_lock_evidence(lines)
            pattern, shape = infer_pattern(lines.mapped("payment_ref"))
            base = rows[0][1]
            proposal_key = _digest({"company": self.env.company.id, "group": key, "pattern": pattern})
            if scoped.with_context(active_test=False).search_count([("rebuild_proposal_key", "=", proposal_key)]):
                continue
            gross = -sum(rows[0][0]._seek_for_lines()[0].mapped("balance"))
            if not gross:
                continue
            # Native tax computation remains the oracle. For excluded tax, the
            # percentage expresses the observed base-to-gross ratio; included
            # tax starts with gross and lets native compute_all extract the base.
            percent = 100 if any(base.tax_ids.mapped("price_include")) else base.balance / gross * 100
            candidate_values = {
                "name": _("Suggested: %s", lines[:1].payment_ref[:60]),
                "company_id": self.env.company.id, "active": True, "trigger": "manual",
                "match_label": "match_regex", "match_label_param": pattern,
                "match_journal_ids": [Command.set(lines.journal_id.ids)],
                "match_partner_ids": [Command.set(lines.partner_id.ids)] if lines.partner_id else [],
                "match_amount": "lower" if key[3] < 0 else "greater",
                "match_amount_max": 0, "match_amount_min": 0,
                "line_ids": [Command.create({"account_id": base.account_id.id,
                    "amount_type": "percentage", "amount_string": format(percent, ".14g"),
                    "analytic_distribution": base.analytic_distribution, "tax_ids": [Command.set(base.tax_ids.ids)],
                    "label": False})],
                "rebuild_is_proposal": True, "rebuild_generated_proof": True,
                "rebuild_proposal_source": "deterministic", "rebuild_proposed_trigger": "manual",
                "rebuild_proposal_key": proposal_key, "rebuild_proposal_confidence": 0,
                "rebuild_pattern_shape": shape,
                "rebuild_evidence_statement_line_ids": [Command.set(lines.ids)],
                "rebuild_business_purpose": _("Apply the demonstrated accounting result to this recurring bank label shape."),
                "rebuild_proposal_evidence": _("%(count)s distinct reconciled transactions. Examples: %(examples)s. The count describes support, not a probability.", count=len(lines), examples="; ".join(dict.fromkeys(lines.mapped("payment_ref")))[:600]),
            }
            candidate = scoped.new(candidate_values)
            if not all(candidate._rebuild_native_effect_matches(statement) for statement in lines):
                refusals.append(_("“%s”: the native rule cannot reproduce all observed accounting results.", lines[:1].payment_ref))
                unsupported_count += 1
                continue
            rule = scoped.create(candidate_values)
            digest = rule._rebuild_current_proof()
            conflict, witness, warnings = rule._rebuild_overlap_assessment()
            if conflict:
                refusals.append(_("“%(label)s” overlaps “%(rule)s” on “%(example)s”.", label=lines[:1].payment_ref, rule=conflict.display_name, example=witness))
                rule.write({"active": False, "rebuild_replay_summary": refusals[-1]})
                skipped_rules |= rule
                continue
            rule.write({"rebuild_proof_digest": digest, **rule._rebuild_overlap_warning_values(warnings),
                        "rebuild_replay_summary": _("Native replay reproduces all %s supporting transactions.", len(lines))})
            created |= rule
        if created:
            action = self.env["ir.actions.actions"]._for_xml_id("account.action_account_reconcile_model")
            action.update({"name": _("Rule Suggestions"), "domain": [("id", "in", created.ids)]})
            if refusals:
                action["help"] = escape(_("Some patterns were not offered: %s", "\n".join(refusals)))
                links = [{"label": rule.display_name, "url": "/odoo/reconciliation-models/%s" % rule.id}
                         for rule in skipped_rules[:3]]
                message = _("Suggestions prepared: %(count)s. Patterns not offered: %(skipped)s.",
                            count=len(created), skipped=len(refusals))
                if unsupported_count:
                    message += " " + _("Manual configuration needed (native accounting result differs): %s.", unsupported_count)
                if links:
                    message += " " + " · ".join(["%s"] * len(links))
                return {"type": "ir.actions.client", "tag": "display_notification", "params": {
                    "title": _("Rule Suggestions"), "message": message, "links": links,
                    "type": "warning", "sticky": True, "next": action,
                }}
            return action
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": _("No new suggestions"), "message": "\n".join(refusals) or _("No new supported recurring pattern was found. Existing suggestions and dismissed history are retained."), "type": "info", "sticky": bool(refusals)}}
