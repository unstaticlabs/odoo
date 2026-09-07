"""Configurable closing control definitions and their evaluated control lines."""

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from .closing import (
    CLOSING_CONTROL_CATEGORIES,
    CLOSING_CONTROL_DEFINITIONS,
    CLOSING_CONTROL_IMPACT_POLICIES,
    CLOSING_CONTROL_OWNERS,
    HYGIENE_CONTROL_DEFINITIONS,
    PROFIT_AND_LOSS_ACCOUNT_TYPES,
)
from .configurable_definition import ACCOUNTING_DEFINITION_ORIGINS


class RebuildAccountClosingControlDefinition(models.Model):
    _name = "rebuild.account.closing.control.definition"
    _description = "Accounting Control Configuration"
    _inherit = ["rebuild.account.configurable.definition.mixin"]
    _order = "company_id, sequence, code"

    _unique_closing_control_definition = models.Constraint(
        "UNIQUE (company_id, code)",
        "A closing control can only be configured once per company.",
    )

    company_id = fields.Many2one(
        "res.company",
        required=True,
        index=True,
        default=lambda self: self.env.company,
    )
    sequence = fields.Integer(default=10)
    code = fields.Char(required=True, index=True, readonly=True)
    name = fields.Char(required=True, translate=True)
    category = fields.Selection(
        CLOSING_CONTROL_CATEGORIES,
        required=True,
        index=True,
    )
    description = fields.Text(
        required=True,
        help="What the control examines when a closing workspace is refreshed.",
    )
    accounting_consequence = fields.Text(
        required=True,
        help="Why an exception matters to the accounting review.",
    )
    owner = fields.Selection(
        CLOSING_CONTROL_OWNERS,
        required=True,
        default="accounting_manager",
        string="Responsible Role",
    )
    enabled = fields.Boolean(
        default=True,
        help=(
            "Enabled controls are calculated in every closing workspace for "
            "this company. Disabling a control removes it from the next "
            "refresh; it does not alter accounting records."
        ),
    )
    applies_to_hygiene = fields.Boolean(
        string="Accounting Hygiene",
        help="Run this control when Accounting Hygiene is refreshed.",
    )
    applies_to_closing = fields.Boolean(
        string="Closing Readiness",
        help="Run this control when a matching closing workspace is refreshed.",
    )
    closing_period_scope = fields.Selection(
        [
            ("all", "All Closing Periods"),
            ("month", "Month End Only"),
            ("quarter", "Quarter End Only"),
            ("annual", "Annual Close Only"),
        ],
        required=True,
        default="all",
        help="Limits closing execution without changing daily Hygiene execution.",
    )
    impact_policy = fields.Selection(
        CLOSING_CONTROL_IMPACT_POLICIES,
        required=True,
        default="evaluator",
        help=(
            "Dynamic preserves the installed evaluator's contextual result. "
            "The other choices consistently map every detected exception to "
            "information, a warning or a readiness blocker."
        ),
    )
    accountant_visible = fields.Boolean(
        string="Include in Accountant Summary",
        default=True,
        help=(
            "Include this control's summary in the accountant review text. "
            "The structured control and result remain inspectable."
        ),
    )
    expected_resolution = fields.Text(
        required=True,
        default=(
            "Open the current result, correct or document the underlying "
            "accounting condition, then refresh the control."
        ),
        help="Business-facing guidance shown to the person responsible for a failure.",
    )
    origin = fields.Selection(
        ACCOUNTING_DEFINITION_ORIGINS,
        required=True,
        default="usl",
        readonly=True,
    )
    source_module = fields.Char(required=True, default="rebuild_account_migration", readonly=True)
    evaluator_key = fields.Char(
        readonly=True,
        help="Stable key resolved through the installed evaluator registry.",
    )
    technical_model = fields.Char(readonly=True)
    technical_summary = fields.Text(
        readonly=True,
        help="Implementation boundary and important assumptions for technical review.",
    )
    closing_result_count = fields.Integer(compute="_compute_result_counts")
    hygiene_result_count = fields.Integer(compute="_compute_result_counts")

    @api.depends("company_id")
    def _compute_result_counts(self):
        ClosingResult = self.env["rebuild.account.closing.control"]
        HygieneResult = self.env["rebuild.account.hygiene.issue"]
        closing_groups = ClosingResult._read_group(
            [("definition_id", "in", self.ids)],
            ["definition_id"],
            ["__count"],
        ) if self.ids else []
        hygiene_groups = HygieneResult._read_group(
            [("definition_id", "in", self.ids)],
            ["definition_id"],
            ["__count"],
        ) if self.ids else []
        closing_counts = {definition.id: count for definition, count in closing_groups}
        hygiene_counts = {definition.id: count for definition, count in hygiene_groups}
        for definition in self:
            definition.closing_result_count = closing_counts.get(definition.id, 0)
            definition.hygiene_result_count = hygiene_counts.get(definition.id, 0)

    @api.constrains("applies_to_hygiene", "applies_to_closing")
    def _check_usage(self):
        for definition in self:
            if not definition.applies_to_hygiene and not definition.applies_to_closing:
                raise UserError(
                    "An Accounting Control must apply to Hygiene, Closing, or both.",
                )

    @api.model
    def _ensure_for_company(self, company):
        company.ensure_one()
        existing = {
            definition.code: definition
            for definition in self.with_context(active_test=False).search([
                ("company_id", "=", company.id),
            ])
        }
        for sequence, values in enumerate(CLOSING_CONTROL_DEFINITIONS, start=1):
            code, category, name, description, consequence, owner = values
            if code in existing:
                updates = {}
                if not existing[code].evaluator_key:
                    updates["evaluator_key"] = code
                if not existing[code].applies_to_closing:
                    updates["applies_to_closing"] = True
                if not existing[code].business_purpose:
                    updates["business_purpose"] = description
                if not existing[code].expected_outcome:
                    updates["expected_outcome"] = (
                        "The evaluator completes and any exception is resolved "
                        "or governed by the configured readiness policy."
                    )
                if updates:
                    existing[code].with_context(
                        accounting_control_seed=True,
                    ).write(updates)
                continue
            existing[code] = self.create({
                "company_id": company.id,
                "sequence": sequence * 10,
                "code": code,
                "evaluator_key": code,
                "category": category,
                "name": name,
                "description": description,
                "business_purpose": description,
                "accounting_consequence": consequence,
                "expected_outcome": (
                    "The evaluator completes and any exception is resolved or "
                    "governed according to the configured readiness policy."
                ),
                "owner": owner,
                "applies_to_closing": True,
                "expected_resolution": (
                    "Open the current closing result, resolve or document the "
                    "underlying condition, then refresh readiness."
                ),
                "technical_model": "rebuild.account.closing.period",
                "technical_summary": (
                    f"Python-backed closing evaluator registered as {code}. "
                    "It reads company-scoped accounting records for the "
                    "workspace dates and does not post or alter journal entries."
                ),
            })
        next_sequence = (len(CLOSING_CONTROL_DEFINITIONS) + 1) * 10
        for offset, values in enumerate(HYGIENE_CONTROL_DEFINITIONS):
            code, category, name, description, consequence, owner = values
            if code in existing:
                updates = {}
                if not existing[code].business_purpose:
                    updates["business_purpose"] = description
                if not existing[code].expected_outcome:
                    updates["expected_outcome"] = (
                        "The underlying accounting issue is corrected and the "
                        "next Hygiene refresh resolves the result naturally."
                    )
                if updates:
                    existing[code].with_context(
                        accounting_control_seed=True,
                    ).write(updates)
                continue
            existing[code] = self.create({
                "company_id": company.id,
                "sequence": next_sequence + offset * 10,
                "code": code,
                "evaluator_key": "builtin_hygiene",
                "category": category,
                "name": name,
                "description": description,
                "business_purpose": description,
                "accounting_consequence": consequence,
                "expected_outcome": (
                    "The underlying accounting issue is corrected and the "
                    "next Hygiene refresh resolves the result naturally."
                ),
                "owner": owner,
                "applies_to_hygiene": True,
                "expected_resolution": (
                    "Open the affected records, resolve or document the "
                    "underlying condition, then refresh Accounting Hygiene."
                ),
                "technical_model": "rebuild.account.hygiene.issue",
                "technical_summary": (
                    "Python-backed deterministic Hygiene evaluator. Results "
                    "retain first/last detection, resolution and source links."
                ),
            })
        return self.search([("company_id", "=", company.id)])

    def write(self, vals):
        vals = dict(vals)
        if "description" in vals and "business_purpose" not in vals:
            vals["business_purpose"] = vals["description"]
        if "expected_resolution" in vals and "expected_outcome" not in vals:
            vals["expected_outcome"] = vals["expected_resolution"]
        business_fields = {
            "enabled",
            "name",
            "category",
            "description",
            "accounting_consequence",
            "owner",
            "applies_to_hygiene",
            "applies_to_closing",
            "closing_period_scope",
            "impact_policy",
            "accountant_visible",
            "expected_resolution",
            "sequence",
            "definition_version",
            "lifecycle",
            "business_purpose",
            "expected_outcome",
            "effective_from",
            "effective_to",
        }
        if (
            business_fields & set(vals)
            and not self.env.context.get("accounting_control_seed")
        ):
            vals = {**vals, "origin": "company"}
        return super().write(vals)

    def _applies_to_period_type(self, period_type):
        self.ensure_one()
        return self.closing_period_scope in {"all", period_type}

    def _is_effective(self, on_date):
        self.ensure_one()
        on_date = fields.Date.to_date(on_date)
        return (
            self.lifecycle == "current"
            and (
                not self.effective_from
                or self.effective_from <= on_date
            )
            and (
                not self.effective_to
                or self.effective_to >= on_date
            )
        )

    def _apply_result_policy(self, values):
        self.ensure_one()
        status = values["status"]
        if (
            self.impact_policy != "evaluator"
            and status not in {"pass", "not_applicable", "technical_error"}
        ):
            status = {
                "informational": "info",
                "advisory": "warning",
                "blocking": "block",
            }[self.impact_policy]
        return {
            **values,
            "status": status,
            "next_action": values.get("next_action") or self.expected_resolution,
            "definition_version": self.definition_version,
            "definition_snapshot": self._definition_snapshot(),
        }

    def _apply_hygiene_policy(self, values):
        self.ensure_one()
        if self.impact_policy == "evaluator":
            severity = values["severity"]
        else:
            severity = {
                "informational": "4_information",
                "advisory": "2_warning",
                "blocking": "1_blocking",
            }[self.impact_policy]
        return {
            **values,
            "definition_id": self.id,
            "control_code": self.code,
            "severity": severity,
            "owner_role": self.owner,
            "definition_version": self.definition_version,
            "definition_snapshot": self._definition_snapshot(),
        }

    def action_refresh_open_workspaces(self):
        if not self.env.user.has_group("account.group_account_manager"):
            message = (
                "Only an Accounting Manager can refresh configured "
                "Accounting Controls."
            )
            raise AccessError(message)
        companies = self.company_id if self else self.env.companies
        closings = self.env["rebuild.account.closing.period"].search([
            ("company_id", "in", companies.ids),
            ("state", "not in", ["closed", "archived"]),
        ])
        closings.action_refresh_controls()
        for company in companies:
            self.env["rebuild.account.hygiene.issue"].sync_for_company(company)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Accounting controls refreshed",
                "message": (
                    f"Accounting Hygiene and {len(closings)} open closing "
                    "workspace(s) now use the current configuration."
                ),
                "type": "success",
                "sticky": False,
            },
        }

    def action_open_closing_results(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Closing Control Results",
            "res_model": "rebuild.account.closing.control",
            "view_mode": "list",
            "domain": [("definition_id", "in", self.ids)],
            "context": {"create": False, "delete": False},
        }

    def action_open_hygiene_results(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Accounting Hygiene Results",
            "res_model": "rebuild.account.hygiene.issue",
            "view_mode": "list,form",
            "domain": [("definition_id", "in", self.ids)],
            "context": {"create": False, "delete": False},
        }


class RebuildAccountClosingControl(models.Model):
    _name = "rebuild.account.closing.control"
    _description = "USL Accounting Closing Control"
    _order = "closing_period_id, sequence, code"

    _unique_closing_control = models.Constraint(
        "UNIQUE (closing_period_id, code)",
        "A closing control code must be unique within one workspace.",
    )

    closing_period_id = fields.Many2one("rebuild.account.closing.period", required=True, index=True, ondelete="cascade")
    definition_id = fields.Many2one(
        "rebuild.account.closing.control.definition",
        ondelete="restrict",
    )
    definition_version = fields.Char(readonly=True)
    definition_snapshot = fields.Json(readonly=True)
    company_id = fields.Many2one(related="closing_period_id.company_id", store=True, readonly=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)
    sequence = fields.Integer(default=10)
    code = fields.Char(required=True, index=True)
    category = fields.Selection(CLOSING_CONTROL_CATEGORIES, required=True, index=True)
    name = fields.Char(required=True)
    status = fields.Selection(
        [
            ("pass", "Passed"),
            ("info", "Information"),
            ("warning", "Warning"),
            ("block", "Blocking"),
            ("technical_error", "Technical Failure"),
            ("not_applicable", "Not Applicable"),
        ],
        required=True,
        index=True,
    )
    result_kind = fields.Selection(
        [
            ("accounting", "Accounting Result"),
            ("technical", "Technical Failure"),
        ],
        compute="_compute_result_kind",
        store=True,
        index=True,
    )
    record_count = fields.Integer()
    amount = fields.Monetary(currency_field="currency_id")
    summary = fields.Text(required=True)
    next_action = fields.Text(required=True)
    owner = fields.Selection(
        CLOSING_CONTROL_OWNERS,
        required=True,
        default="accounting_manager",
        string="Responsible Role",
    )
    accountant_visible = fields.Boolean(default=True)

    @api.depends("status")
    def _compute_result_kind(self):
        for control in self:
            control.result_kind = (
                "technical" if control.status == "technical_error" else "accounting"
            )

    @api.model
    def _upsert(self, closing, code, vals):
        control = self.search([("closing_period_id", "=", closing.id), ("code", "=", code)], limit=1)
        values = {"closing_period_id": closing.id, **vals}
        if control:
            control.write(values)
        else:
            control = self.create(values)
        return control

    def action_open_records(self):
        self.ensure_one()
        closing = self.closing_period_id
        if self.status == "technical_error" and self.definition_id:
            return {
                "type": "ir.actions.act_window",
                "name": "Accounting Control",
                "res_model": "rebuild.account.closing.control.definition",
                "res_id": self.definition_id.id,
                "view_mode": "form",
                "target": "current",
                "context": {"create": False, "delete": False},
            }
        if self.code == "accounting_completeness":
            return self._action("Draft Journal Entries", "account.move", [
                ("company_id", "=", self.company_id.id), ("date", ">=", closing.date_from),
                ("date", "<=", closing.date_to), ("state", "=", "draft"), ("move_type", "=", "entry"),
            ])
        if self.code == "document_completeness":
            return self._action("Closing Documents", "account.move", [
                ("company_id", "=", self.company_id.id), ("date", ">=", closing.date_from),
                ("date", "<=", closing.date_to),
                ("move_type", "in", ["out_invoice", "out_refund", "in_invoice", "in_refund"]),
            ])
        if self.code == "bank_reconciliation":
            return self._action("Bank Matching Items", "account.bank.statement.line", [
                ("company_id", "=", self.company_id.id), ("date", ">=", closing.date_from),
                ("date", "<=", closing.date_to), ("is_reconciled", "=", False),
            ], "kanban,list,form")
        if self.code == "unusual_balances":
            account_ids = [
                account.id
                for account, _balance, _line_count, _side
                in closing._unusual_balance_rows()
            ]
            action = self._action("Unusual Balance Journal Items", "account.move.line", [
                ("company_id", "=", self.company_id.id),
                ("move_id.state", "=", "posted"),
                ("date", "<=", closing.date_to),
                ("account_id", "in", account_ids),
                "|",
                (
                    "account_id.account_type",
                    "not in",
                    PROFIT_AND_LOSS_ACCOUNT_TYPES,
                ),
                ("date", ">=", closing.fiscalyear_start),
            ], "list,form,pivot")
            action["context"]["search_default_group_by_account"] = 1
            return action
        if self.code == "tax_declarations":
            return closing.action_open_declarations()
        if self.code == "issues":
            return self._action("Accounting Hygiene", "rebuild.account.hygiene.issue", [
                ("company_id", "=", self.company_id.id),
                ("status", "=", "open"),
                "|",
                ("issue_date", "=", False),
                ("issue_date", "<=", closing.date_to),
            ])
        if self.code == "reports":
            return self.env.ref(
                "rebuild_account_migration.action_rebuild_account_report_export_wizard",
            ).read()[0]
        return self._action("Closing Journal Items", "account.move.line", [
            ("company_id", "=", self.company_id.id), ("date", ">=", closing.date_from), ("date", "<=", closing.date_to),
        ], "list,form,pivot")

    @staticmethod
    def _action(name, model, domain, view_mode="list,form"):
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": model,
            "view_mode": view_mode,
            "domain": domain,
            "context": {"create": False, "delete": False},
        }
