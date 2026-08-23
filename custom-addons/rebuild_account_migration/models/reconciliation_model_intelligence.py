from collections import defaultdict
from datetime import timedelta
import re

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import SQL


class AccountReconcileModel(models.Model):
    _inherit = "account.reconcile.model"

    rebuild_business_purpose = fields.Text(
        string="Business purpose",
        help=(
            "Explain the recurring accounting situation this rule handles and "
            "why the proposed result is appropriate."
        ),
    )
    rebuild_is_proposal = fields.Boolean(
        string="Rule suggestion",
        default=False,
        copy=False,
        index=True,
        tracking=True,
        help=(
            "Suggested rules are visible for review but cannot be applied by "
            "Bank Matching until an Accounting Manager approves them."
        ),
    )
    rebuild_proposal_source = fields.Selection(
        selection=[
            ("deterministic", "Deterministic analysis"),
            ("ai", "AI suggestion"),
        ],
        string="Suggestion source",
        copy=False,
        tracking=True,
    )
    rebuild_proposed_trigger = fields.Selection(
        selection=[
            ("manual", "Suggest during matching"),
            ("auto_reconcile", "Apply automatically"),
        ],
        string="Proposed behavior",
        default="manual",
        copy=False,
        tracking=True,
    )
    rebuild_proposal_confidence = fields.Integer(
        string="Confidence",
        copy=False,
        tracking=True,
    )
    rebuild_proposal_evidence = fields.Text(
        string="Suggestion evidence",
        copy=False,
        tracking=True,
    )
    rebuild_proposal_key = fields.Char(
        copy=False,
        index=True,
    )
    _unique_rebuild_proposal_key = models.UniqueIndex(
        "(rebuild_proposal_key) WHERE rebuild_proposal_key IS NOT NULL",
        "A Bank Matching Rule suggestion already uses this evidence key.",
    )
    rebuild_evidence_statement_line_ids = fields.Many2many(
        comodel_name="account.bank.statement.line",
        relation="rebuild_reconcile_model_statement_evidence_rel",
        column1="reconcile_model_id",
        column2="statement_line_id",
        string="Supporting bank transactions",
        copy=False,
        readonly=True,
    )
    rebuild_rule_type = fields.Selection(
        selection=[
            ("categorization", "Accounting rule"),
            ("partner_mapping", "Partner-only rule"),
            ("mixed", "Multi-line rule"),
            ("empty", "No result"),
        ],
        compute="_compute_rebuild_rule_intelligence",
        string="Purpose",
    )
    rebuild_origin = fields.Selection(
        selection=[
            ("standard", "Odoo standard"),
            ("custom", "Company rule"),
            ("deterministic", "System suggestion"),
            ("ai", "AI suggestion"),
        ],
        compute="_compute_rebuild_rule_intelligence",
        search="_search_rebuild_origin",
        string="Origin",
    )
    rebuild_health_state = fields.Selection(
        selection=[
            ("proven", "Used"),
            ("ready", "Ready"),
            ("redundant", "Redundant"),
            ("needs_review", "Needs review"),
            ("suggested", "Suggestion"),
            ("archived", "Archived"),
        ],
        compute="_compute_rebuild_rule_intelligence",
        search="_search_rebuild_health_state",
        string="Assessment",
    )
    rebuild_scope_summary = fields.Char(
        compute="_compute_rebuild_rule_intelligence",
        string="Applies when",
    )
    rebuild_effect_summary = fields.Char(
        compute="_compute_rebuild_rule_intelligence",
        string="Accounting result",
    )
    rebuild_guidance = fields.Text(
        compute="_compute_rebuild_rule_intelligence",
        string="Recommendation",
    )
    rebuild_has_actionable_guidance = fields.Boolean(
        compute="_compute_rebuild_rule_intelligence",
        string="Has actionable recommendation",
    )
    rebuild_historical_use_count = fields.Integer(
        compute="_compute_rebuild_usage",
        string="Historical uses",
    )
    rebuild_current_use_count = fields.Integer(
        compute="_compute_rebuild_usage",
        string="Current uses",
    )
    rebuild_total_use_count = fields.Integer(
        compute="_compute_rebuild_usage",
        string="Recorded uses",
    )
    rebuild_use_badge = fields.Char(
        compute="_compute_rebuild_usage",
        string="Uses",
    )
    rebuild_last_used_on = fields.Date(
        compute="_compute_rebuild_usage",
        string="Last used",
    )
    rebuild_open_match_count = fields.Integer(
        compute="_compute_rebuild_open_matches",
        string="Open matches",
        help="Unmatched bank transactions that currently satisfy this rule.",
    )
    rebuild_open_match_badge = fields.Char(
        compute="_compute_rebuild_open_matches",
        string="Open match badge",
    )
    rebuild_activity_badge = fields.Char(
        compute="_compute_rebuild_activity_badge",
        string="Activity",
    )
    rebuild_activity_state = fields.Selection(
        selection=[
            ("none", "No activity"),
            ("used", "Used"),
            ("open", "Open matches"),
            ("used_open", "Used with open matches"),
        ],
        compute="_compute_rebuild_activity_badge",
        string="Activity state",
    )

    @api.constrains("rebuild_proposal_confidence")
    def _check_rebuild_proposal_confidence(self):
        for rule in self:
            if not 0 <= rule.rebuild_proposal_confidence <= 100:
                raise ValidationError(_("Confidence must be between 0 and 100."))

    def _compute_rebuild_usage(self):
        usage = {}
        if self.ids:
            self.env.cr.execute(
                """
                SELECT reconcile_model_id,
                       COUNT(DISTINCT move_id),
                       MAX(date)
                  FROM account_move_line
                 WHERE reconcile_model_id = ANY(%s)
                 GROUP BY reconcile_model_id
                """,
                [self.ids],
            )
            usage = {
                rule_id: (count, last_used)
                for rule_id, count, last_used in self.env.cr.fetchall()
            }
        for rule in self:
            current_count, last_used = usage.get(rule.id, (0, False))
            rule.rebuild_historical_use_count = current_count
            rule.rebuild_current_use_count = current_count
            rule.rebuild_total_use_count = current_count
            rule.rebuild_use_badge = (
                str(current_count)
                if current_count
                else False
            )
            rule.rebuild_last_used_on = last_used

    def _rebuild_matching_open_statement_lines(self):
        companies = self.company_id or self.env.company
        return self.env["account.bank.statement.line"].search([
            ("company_id", "in", companies.ids),
            ("is_reconciled", "=", False),
            ("move_id.state", "=", "posted"),
        ])

    def _rebuild_open_matches_by_rule(self):
        counts = defaultdict(int)
        statement_lines = self._rebuild_matching_open_statement_lines()
        if not statement_lines:
            return counts
        for trigger in ("manual", "auto_reconcile"):
            rules_by_line = self._get_rules(
                statement_lines,
                trigger=trigger,
            )
            for rule_ids in rules_by_line.values():
                for rule_id in rule_ids:
                    counts[rule_id] += 1
        return counts

    def _compute_rebuild_open_matches(self):
        counts = self._rebuild_open_matches_by_rule()
        for rule in self:
            rule.rebuild_open_match_count = counts[rule.id]
            rule.rebuild_open_match_badge = (
                str(counts[rule.id]) if counts[rule.id] else False
            )

    @api.depends("rebuild_total_use_count", "rebuild_open_match_count")
    def _compute_rebuild_activity_badge(self):
        for rule in self:
            uses = rule.rebuild_total_use_count
            open_matches = rule.rebuild_open_match_count
            if uses and open_matches:
                rule.rebuild_activity_state = "used_open"
                rule.rebuild_activity_badge = _(
                    "%(uses)s used · %(matches)s open",
                    uses=uses,
                    matches=open_matches,
                )
            elif open_matches:
                rule.rebuild_activity_state = "open"
                rule.rebuild_activity_badge = _(
                    "%s open",
                    open_matches,
                )
            elif uses:
                rule.rebuild_activity_state = "used"
                rule.rebuild_activity_badge = _("%s used", uses)
            else:
                rule.rebuild_activity_state = "none"
                rule.rebuild_activity_badge = _("No activity")

    @api.depends(
        "active",
        "can_be_proposed",
        "line_ids.account_id",
        "line_ids.partner_id",
        "mapped_partner_id",
        "match_amount",
        "match_journal_ids",
        "match_label",
        "match_label_param",
        "match_partner_ids",
        "rebuild_business_purpose",
        "rebuild_is_proposal",
        "rebuild_proposal_source",
        "rebuild_proposal_confidence",
        "rebuild_open_match_count",
        "trigger",
    )
    def _compute_rebuild_rule_intelligence(self):
        external_ids = self.get_external_id()
        for rule in self:
            account_lines = rule.line_ids.filtered("account_id")
            partner_only = bool(rule.mapped_partner_id)
            if partner_only:
                rule_type = "partner_mapping"
            elif not rule.line_ids:
                rule_type = "empty"
            elif len(rule.line_ids) == 1:
                rule_type = "categorization"
            else:
                rule_type = "mixed"

            if rule.rebuild_is_proposal:
                origin = rule.rebuild_proposal_source or "deterministic"
            elif (external_ids.get(rule.id) or "").startswith("account."):
                origin = "standard"
            else:
                origin = "custom"

            if rule.rebuild_is_proposal:
                health = "suggested"
            elif not rule.active:
                health = "archived"
            elif partner_only:
                health = "redundant"
            elif not rule.line_ids or not rule.can_be_proposed:
                health = "needs_review"
            elif rule.rebuild_total_use_count:
                health = "proven"
            else:
                health = "ready"

            scope_parts = []
            if rule.match_journal_ids:
                journal_names = ", ".join(rule.match_journal_ids.mapped("name"))
                scope_parts.append(_("Journals: %s", journal_names))
            else:
                scope_parts.append(_("All bank journals"))
            if rule.match_label and rule.match_label_param:
                scope_parts.append(
                    _("Label %(operator)s “%(value)s”",
                      operator=dict(rule._fields["match_label"].selection).get(
                          rule.match_label,
                          rule.match_label,
                      ).lower(),
                      value=rule.match_label_param),
                )
            if rule.match_partner_ids:
                scope_parts.append(
                    _("Partners: %s", ", ".join(
                        rule.match_partner_ids.mapped("display_name"),
                    )),
                )
            if rule.match_amount:
                scope_parts.append(_("Amount condition"))

            if partner_only:
                effect = _(
                    "Assign partner %(partner)s only",
                    partner=rule.mapped_partner_id.display_name,
                )
            elif account_lines:
                accounts = ", ".join(
                    dict.fromkeys(account_lines.account_id.mapped("display_name")),
                )
                effect = _("Create counterpart on %s", accounts)
            elif rule.line_ids:
                effect = _("Create partner-based counterpart items")
            else:
                effect = _("No accounting result configured")

            if health == "redundant":
                guidance = _(
                    "Partner inference already covers this mapping. Archive "
                    "the rule if no external process depends on it.",
                )
            elif health == "needs_review":
                guidance = _(
                    "This rule cannot produce a reliable proposal. Add a "
                    "condition and counterpart result, or archive it.",
                )
            elif health == "suggested":
                guidance = _(
                    "Review the sample transactions and accounting result, "
                    "then approve or dismiss this suggestion.",
                )
            elif (
                health in ("ready", "proven")
                and rule.rebuild_open_match_count
            ):
                if rule.rebuild_open_match_count == 1:
                    guidance = _(
                        "1 unmatched bank transaction currently matches. "
                        "Open it to confirm the rule's result.",
                    )
                else:
                    guidance = _(
                        "%s unmatched bank transactions currently match. "
                        "Open them to confirm the rule's result.",
                        rule.rebuild_open_match_count,
                    )
            else:
                guidance = False

            rule.rebuild_rule_type = rule_type
            rule.rebuild_origin = origin
            rule.rebuild_health_state = health
            rule.rebuild_scope_summary = " · ".join(scope_parts)
            rule.rebuild_effect_summary = effect
            rule.rebuild_guidance = guidance
            rule.rebuild_has_actionable_guidance = bool(guidance)

    def _search_computed_selection(self, field_name, operator, value):
        if operator not in ("=", "!=", "in", "not in"):
            raise UserError(_("This filter only supports exact values."))
        expected = set(value) if operator in ("in", "not in") else {value}
        records = self.with_context(active_test=False).search([])
        matched_ids = records.filtered(
            lambda rule: rule[field_name] in expected,
        ).ids
        positive = operator in ("=", "in")
        return [("id", "in" if positive else "not in", matched_ids)]

    def _search_rebuild_health_state(self, operator, value):
        return self._search_computed_selection(
            "rebuild_health_state",
            operator,
            value,
        )

    def _search_rebuild_origin(self, operator, value):
        return self._search_computed_selection(
            "rebuild_origin",
            operator,
            value,
        )

    def _get_rules_query(self, bank_statement_lines, trigger="auto_reconcile"):
        query = super()._get_rules_query(
            bank_statement_lines,
            trigger=trigger,
        )
        query.add_where(
            SQL(
                "%s IS NOT TRUE",
                SQL.identifier(self._table, "rebuild_is_proposal"),
            ),
        )
        return query

    def action_rebuild_open_matches(self):
        self.ensure_one()
        statement_lines = self._rebuild_matching_open_statement_lines()
        matching_ids = []
        rules_by_line = self._get_rules(
            statement_lines,
            trigger=self.trigger,
        )
        for line_id, rule_ids in rules_by_line.items():
            if self.id in rule_ids:
                matching_ids.append(line_id)
        action = self.env["ir.actions.actions"]._for_xml_id(
            "account_reconcile_oca.action_bank_statement_line_reconcile",
        )
        action["domain"] = [("id", "in", matching_ids)]
        return action

    def _rebuild_check_rule_manager(self):
        if not self.env.user.has_group("account.group_account_manager"):
            raise AccessError(_(
                "Only an Accounting Manager can govern bank matching rules.",
            ))

    def action_rebuild_approve_proposal(self):
        self.ensure_one()
        self._rebuild_check_rule_manager()
        if not self.rebuild_is_proposal:
            return True
        if not self.match_label and not self.match_amount and not self.match_partner_ids:
            raise UserError(_(
                "Add at least one reliable matching condition before approval.",
            ))
        if not self.line_ids:
            raise UserError(_(
                "Add the expected counterpart entry before approval.",
            ))
        self.write({
            "rebuild_is_proposal": False,
            "trigger": self.rebuild_proposed_trigger or "manual",
        })
        self.message_post(body=_(
            "Rule suggestion approved by %(user)s. Evidence retained: %(evidence)s",
            user=self.env.user.display_name,
            evidence=self.rebuild_proposal_evidence or _("No evidence supplied"),
        ))
        return True

    def action_rebuild_dismiss_proposal(self):
        self.ensure_one()
        self._rebuild_check_rule_manager()
        if self.rebuild_is_proposal:
            self.message_post(body=_(
                "Rule suggestion dismissed by %s.",
                self.env.user.display_name,
            ))
            self.active = False
        return {"type": "ir.actions.act_window_close"}

    def action_rebuild_archive_rule(self):
        self.ensure_one()
        self._rebuild_check_rule_manager()
        self.active = False
        self.message_post(body=_(
            "Rule archived by %(user)s after the smart-rule assessment "
            "classified it as %(assessment)s.",
            user=self.env.user.display_name,
            assessment=dict(
                self._fields["rebuild_health_state"].selection,
            ).get(self.rebuild_health_state, self.rebuild_health_state),
        ))
        return {"type": "ir.actions.act_window_close"}

    @staticmethod
    def _rebuild_normalize_rule_label(value):
        return re.sub(r"\s+", " ", (value or "").strip()).casefold()

    def _rebuild_rule_opportunity_groups(self):
        date_from = fields.Date.today() - timedelta(days=730)
        statement_lines = self.env["account.bank.statement.line"].search([
            ("company_id", "=", self.env.company.id),
            ("move_id.state", "=", "posted"),
            ("is_reconciled", "=", True),
            ("date", ">=", date_from),
        ])
        groups = defaultdict(list)
        for statement_line in statement_lines:
            label = re.sub(r"\s+", " ", (statement_line.payment_ref or "").strip())
            normalized_label = self._rebuild_normalize_rule_label(label)
            if len(normalized_label) < 4:
                continue
            excluded_accounts = (
                statement_line.journal_id.default_account_id
                | statement_line.journal_id.suspense_account_id
            )
            counterparts = statement_line.move_id.line_ids.filtered(
                lambda line: (
                    line.account_id not in excluded_accounts
                    and line.account_id.account_type
                    not in (
                        "asset_receivable",
                        "liability_payable",
                        "off_balance",
                    )
                    and not line.tax_repartition_line_id
                ),
            )
            if len(counterparts) != 1:
                continue
            counterpart = counterparts[0]
            partner = (
                counterpart.partner_id
                or statement_line.partner_id
            ).commercial_partner_id
            key = (
                statement_line.journal_id.id,
                counterpart.account_id.id,
                partner.id,
                normalized_label,
            )
            groups[key].append((statement_line, counterpart, label))
        return groups

    def action_rebuild_analyze_rule_opportunities(self):
        """Create inert rule suggestions from repeated reconciled patterns.

        Keep this public method record-style: list-header object actions send
        the selected record IDs as their first RPC argument, even when the
        button is displayed without a selection.
        """
        self._rebuild_check_rule_manager()
        created = self.browse()
        groups = self._rebuild_rule_opportunity_groups()
        ranked_groups = sorted(
            groups.items(),
            key=lambda item: (-len(item[1]), item[0]),
        )
        for key, evidence_rows in ranked_groups:
            if len(evidence_rows) < 3 or len(created) >= 12:
                continue
            journal_id, account_id, partner_id, normalized_label = key
            statement_lines = self.env["account.bank.statement.line"].browse(
                [row[0].id for row in evidence_rows],
            )
            label = evidence_rows[0][2]
            account = self.env["account.account"].browse(account_id)
            proposal_key = (
                f"{self.env.company.id}:{journal_id}:{account_id}:"
                f"{partner_id}:{normalized_label}"
            )
            if self.with_context(active_test=False).search_count([
                ("rebuild_proposal_key", "=", proposal_key),
            ]):
                continue
            equivalent_rules = self.with_context(active_test=False).search([
                ("company_id", "=", self.env.company.id),
                ("rebuild_is_proposal", "=", False),
                ("match_label", "=", "contains"),
                ("line_ids.account_id", "=", account_id),
            ])
            if any(
                self._rebuild_normalize_rule_label(rule.match_label_param)
                == normalized_label
                and (
                    not rule.match_journal_ids
                    or journal_id in rule.match_journal_ids.ids
                )
                for rule in equivalent_rules
            ):
                continue
            first_date = min(statement_lines.mapped("date"))
            last_date = max(statement_lines.mapped("date"))
            confidence = min(95, 55 + len(evidence_rows) * 5)
            created |= self.create({
                "name": _(
                    "Suggested: %(label)s → %(account)s",
                    label=label[:60],
                    account=account.display_name,
                ),
                "company_id": self.env.company.id,
                "active": True,
                "trigger": "manual",
                "match_label": "contains",
                "match_label_param": label,
                "match_journal_ids": [Command.set([journal_id])],
                "line_ids": [Command.create({
                    "account_id": account_id,
                    "partner_id": partner_id or False,
                    "amount_type": "percentage",
                    "amount_string": "100",
                    "label": label,
                })],
                "rebuild_is_proposal": True,
                "rebuild_proposal_source": "deterministic",
                "rebuild_proposed_trigger": "manual",
                "rebuild_proposal_confidence": confidence,
                "rebuild_proposal_key": proposal_key,
                "rebuild_business_purpose": _(
                    "Categorize recurring bank transactions labelled “%s”.",
                    label,
                ),
                "rebuild_proposal_evidence": _(
                    "%(count)s reconciled transactions used the same label, "
                    "journal and counterpart account from %(first)s to "
                    "%(last)s. Review the samples before approval.",
                    count=len(evidence_rows),
                    first=fields.Date.to_string(first_date),
                    last=fields.Date.to_string(last_date),
                ),
                "rebuild_evidence_statement_line_ids": [
                    Command.set(statement_lines.ids),
                ],
            })
        if created:
            action = self.env["ir.actions.actions"]._for_xml_id(
                "account.action_account_reconcile_model",
            )
            action.update({
                "name": _("Rule Suggestions"),
                "domain": [("id", "in", created.ids)],
            })
            return action
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("No new suggestions"),
                "message": _(
                    "No repeated, consistently categorized bank pattern "
                    "without an existing rule was found.",
                ),
                "type": "info",
            },
        }
