import calendar
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare

from .configurable_definition import ACCOUNTING_DEFINITION_ORIGINS

BENCHMARK_START = date(2024, 1, 10)
BENCHMARK_END = date(2025, 9, 30)
CURRENT_START = date(2025, 10, 1)
BENCHMARK_PERIOD_KEY = "USL benchmark 2024-01-10 to 2025-09-30"
CURRENT_PERIOD_KEY = "USL current from 2025-10-01"


def _month_end(value):
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


class ResCompany(models.Model):
    _inherit = "res.company"

    rebuild_declaration_profile_active = fields.Boolean(
        string="French Declaration Profile Active",
        help="Generate declaration obligations only after the company's legal and tax profile is confirmed.",
    )
    rebuild_legal_form = fields.Selection(
        [
            ("sasu", "SASU"),
            ("sas", "SAS"),
            ("other", "Other / Review Required"),
        ],
        string="Legal Form",
    )
    rebuild_corporate_tax_regime = fields.Selection(
        [
            ("is", "Corporate Income Tax (IS)"),
            ("ir", "Personal Income Tax (IR)"),
            ("unknown", "Review Required"),
        ],
        string="Corporate Tax Regime",
    )
    rebuild_corporate_tax_projection_profile = fields.Selection(
        [
            ("standard_25", "Standard 25% (Conservative)"),
            (
                "fr_sme_15_25",
                "French SME 15% / 25% (Annual Review)",
            ),
            ("disabled", "Do Not Estimate"),
        ],
        string="Cash Projection IS Profile",
        default="standard_25",
        required=True,
        help=(
            "Controls the management-only corporate-tax reserve shown on the "
            "Accounting Overview. Select the French SME profile when the "
            "available evidence supports the reduced rate, then reconfirm "
            "turnover, fully paid capital and ownership during each 2065 "
            "review. This does not create an accounting entry or a tax "
            "declaration."
        ),
    )
    rebuild_profit_tax_regime = fields.Selection(
        [
            ("bic_simplified", "BIC/IS Simplified (RSI)"),
            ("bic_normal", "BIC/IS Normal"),
            ("unknown", "Review Required"),
        ],
        string="Profit Tax Package",
    )
    rebuild_vat_regime = fields.Selection(
        [
            ("simplified", "VAT Simplified (RSI / CA12)"),
            ("normal", "VAT Normal (CA3)"),
            ("franchise", "VAT Exemption / Franchise"),
            ("unknown", "Review Required"),
        ],
        string="VAT Regime",
    )
    rebuild_first_fiscalyear_start = fields.Date(
        string="First Reconstructed Fiscal-Year Start",
        help="Optional first-year exception used before the company's recurring fiscal-year cadence.",
    )
    rebuild_first_fiscalyear_end = fields.Date(
        string="First Reconstructed Fiscal-Year End",
        help=(
            "Optional end of the exceptional first fiscal year. Reports, "
            "declarations and closing workspaces use this boundary before "
            "the company's recurring fiscal-year cadence."
        ),
    )
    rebuild_declaration_profile_evidence = fields.Text(
        string="Declaration Profile Evidence",
    )

    @api.constrains(
        "rebuild_first_fiscalyear_start",
        "rebuild_first_fiscalyear_end",
    )
    def _check_rebuild_first_fiscalyear_bounds(self):
        for company in self:
            first_start = company.rebuild_first_fiscalyear_start
            first_end = company.rebuild_first_fiscalyear_end
            if bool(first_start) != bool(first_end):
                raise ValidationError(
                    "Set both the start and end of the exceptional first "
                    "fiscal year."
                )
            if first_start and first_start > first_end:
                raise ValidationError(
                    "The exceptional first fiscal-year start must be before "
                    "or equal to its end."
                )

    def _rebuild_first_fiscalyear_dates(self):
        self.ensure_one()
        first_start = fields.Date.to_date(
            self.rebuild_first_fiscalyear_start,
        )
        first_end = fields.Date.to_date(
            self.rebuild_first_fiscalyear_end,
        )
        if first_start and not first_end and self.fiscalyear_lock_date:
            lock_date = fields.Date.to_date(self.fiscalyear_lock_date)
            lock_dates = super().compute_fiscalyear_dates(
                lock_date,
            )
            if lock_dates["date_to"] == lock_date:
                first_end = lock_date
        return first_start, first_end

    def compute_fiscalyear_dates(self, current_date):
        """Extend Odoo's fiscal-year API with the exceptional first year."""
        self.ensure_one()
        fiscal_dates = super().compute_fiscalyear_dates(current_date)
        anchor = fields.Date.to_date(current_date)
        first_start, first_end = self._rebuild_first_fiscalyear_dates()
        if (
            first_start
            and first_end
            and first_start <= anchor <= first_end
        ):
            return {
                "date_from": first_start,
                "date_to": first_end,
            }
        return fiscal_dates

    def rebuild_compute_fiscalyear_dates(self, anchor):
        """Return the governed fiscal year as a tuple for USL workflows."""
        fiscal_dates = self.compute_fiscalyear_dates(anchor)
        return fiscal_dates["date_from"], fiscal_dates["date_to"]

    def action_sync_accounting_obligations(self):
        declarations = self.env["rebuild.account.declaration"]
        closings = self.env["rebuild.account.closing.period"]
        for company in self:
            declarations |= declarations.sync_for_company(company)
            closings |= closings.sync_for_company(company)
        return {
            "type": "ir.actions.act_window",
            "name": "French Declaration Schedule",
            "res_model": "rebuild.account.declaration",
            "view_mode": "list,form,calendar",
            "domain": [("company_id", "in", self.ids)],
            "context": {"create": False, "delete": False},
        }


class RebuildAccountDeclarationRule(models.Model):
    _name = "rebuild.account.declaration.rule"
    _description = "USL French Declaration Rule"
    _inherit = ["rebuild.account.configurable.definition.mixin"]
    _order = "sequence, code, effective_from desc"

    _unique_rule_version = models.Constraint(
        "UNIQUE (code, version)",
        "A declaration rule code and version must be unique.",
    )

    name = fields.Char(required=True)
    code = fields.Char(required=True, index=True)
    company_id = fields.Many2one(
        "res.company",
        index=True,
        help=(
            "Leave empty for a localization definition. A company definition "
            "overrides shared versions with the same code and effective dates."
        ),
    )
    origin = fields.Selection(
        ACCOUNTING_DEFINITION_ORIGINS,
        required=True,
        default="localization",
        readonly=True,
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    country_id = fields.Many2one("res.country", required=True, default=lambda self: self.env.ref("base.fr"))
    category = fields.Selection(
        [
            ("corporate_tax", "Corporate Income Tax"),
            ("profit_tax_package", "Profit Tax Package"),
            ("vat", "VAT"),
            ("tax_credit", "Tax Credits and Reductions"),
            ("dividend", "Dividends / RCM"),
        ],
        required=True,
        index=True,
    )
    cadence = fields.Selection(
        [
            ("annual", "Annual Fiscal Close"),
            ("is_instalments", "Four IS Instalments"),
            ("vat_instalments", "July and December VAT Instalments"),
            ("event", "Business Event"),
        ],
        required=True,
    )
    form_code = fields.Char(required=True)
    tax_form_codes = fields.Char(
        help="Comma-separated tax-package form codes whose ledger-derived fields feed this obligation.",
    )
    version = fields.Char(required=True)
    effective_from = fields.Date(required=True)
    effective_to = fields.Date()
    corporate_tax_required = fields.Boolean()
    profit_tax_regime = fields.Selection(
        [("any", "Any"), ("bic_simplified", "BIC/IS Simplified"), ("bic_normal", "BIC/IS Normal")],
        required=True,
        default="any",
    )
    vat_regime = fields.Selection(
        [("any", "Any"), ("simplified", "VAT Simplified"), ("normal", "VAT Normal")],
        required=True,
        default="any",
    )
    conditional = fields.Boolean()
    official_source_label = fields.Char(required=True)
    official_url = fields.Char(required=True)
    official_updated_on = fields.Date()
    portal_url = fields.Char(required=True, default="https://cfspro.impots.gouv.fr/mire/accueil.do")
    applicability_guidance = fields.Text(required=True)
    filing_guidance = fields.Text(required=True)
    deadline_guidance = fields.Text(required=True)
    customization_of_id = fields.Many2one(
        "rebuild.account.declaration.rule",
        readonly=True,
        ondelete="restrict",
    )
    declaration_count = fields.Integer(compute="_compute_declaration_count")

    @api.depends("code")
    def _compute_declaration_count(self):
        groups = self.env["rebuild.account.declaration"]._read_group(
            [("rule_id", "in", self.ids)],
            ["rule_id"],
            ["__count"],
        ) if self.ids else []
        counts = {rule.id: count for rule, count in groups}
        for rule in self:
            rule.declaration_count = counts.get(rule.id, 0)

    def _definition_snapshot(self):
        values = super()._definition_snapshot()
        values.update({
            "definition_version": self.version,
            "category": self.category,
            "cadence": self.cadence,
            "form_code": self.form_code,
            "tax_form_codes": self.tax_form_codes or "",
            "country_id": self.country_id.id,
            "country_code": self.country_id.code,
            "conditional": self.conditional,
            "official_source_label": self.official_source_label,
            "official_url": self.official_url,
            "portal_url": self.portal_url,
            "applicability_guidance": self.applicability_guidance,
            "filing_guidance": self.filing_guidance,
            "deadline_guidance": self.deadline_guidance,
        })
        return values

    def action_customize_for_company(self):
        self.ensure_one()
        if self.company_id:
            customized = self
        else:
            customized = self.with_context(active_test=False).search([
                ("customization_of_id", "=", self.id),
                ("company_id", "=", self.env.company.id),
            ], limit=1)
            if not customized:
                customized = self.copy({
                    "company_id": self.env.company.id,
                    "origin": "company",
                    "version": f"{self.version}-company-{self.env.company.id}",
                    "definition_version": (
                        f"{self.version}-company-{self.env.company.id}"
                    ),
                    "customization_of_id": self.id,
                })
        return {
            "type": "ir.actions.act_window",
            "name": "Company Declaration Definition",
            "res_model": self._name,
            "res_id": customized.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_declarations(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Declaration Instances",
            "res_model": "rebuild.account.declaration",
            "view_mode": "list,calendar,form",
            "domain": [("rule_id", "in", self.ids)],
            "context": {"create": False, "delete": False},
        }

    @api.model
    def _ensure_governance_metadata(self):
        rules = self.with_context(active_test=False).search([])
        for rule in rules:
            values = {}
            if not rule.company_id and rule.origin == "company":
                values["origin"] = "localization"
            if rule.definition_version != rule.version:
                values["definition_version"] = rule.version
            if not rule.business_purpose:
                values["business_purpose"] = (
                    rule.applicability_guidance
                    or f"Govern the {rule.form_code} declaration obligation."
                )
            if not rule.expected_outcome:
                values["expected_outcome"] = (
                    "The applicable company and fiscal period receive a "
                    "traceable declaration instance using this exact version."
                )
            if not rule.technical_model:
                values["technical_model"] = "rebuild.account.declaration"
            if not rule.technical_summary:
                values["technical_summary"] = (
                    "Versioned declaration scheduler with whitelisted "
                    "ledger-derived field resolvers and explicit external facts."
                )
            if values:
                rule.with_context(
                    accounting_definition_seed=True,
                ).write(values)
            self.env["rebuild.account.declaration"].search([
                ("rule_id", "=", rule.id),
                ("definition_snapshot", "=", False),
            ]).write({
                "definition_snapshot": rule._definition_snapshot(),
            })
        return True

    def action_open_official_source(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": self.official_url, "target": "new"}

    def write(self, vals):
        business_fields = {
            "active",
            "name",
            "sequence",
            "category",
            "cadence",
            "form_code",
            "tax_form_codes",
            "version",
            "lifecycle",
            "business_purpose",
            "expected_outcome",
            "effective_from",
            "effective_to",
            "corporate_tax_required",
            "profit_tax_regime",
            "vat_regime",
            "conditional",
            "official_source_label",
            "official_url",
            "official_updated_on",
            "portal_url",
            "applicability_guidance",
            "filing_guidance",
            "deadline_guidance",
        }
        if (
            business_fields & set(vals)
            and not self.env.context.get("accounting_definition_seed")
            and not self.env.context.get("install_mode")
            and self.filtered(lambda rule: not rule.company_id)
        ):
            raise UserError(
                "Localization Declaration definitions are upgrade-managed. "
                "Use Customize for Company and edit the company definition."
            )
        if "version" in vals:
            vals = {**vals, "definition_version": vals["version"]}
        if (
            business_fields & set(vals)
            and not self.env.context.get("accounting_definition_seed")
            and not self.env.context.get("install_mode")
        ):
            vals = {**vals, "origin": "company"}
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if values.get("version") and not values.get("definition_version"):
                values["definition_version"] = values["version"]
            if (
                values.get("company_id")
                and not self.env.context.get("accounting_definition_seed")
            ):
                values["origin"] = "company"
        return super().create(vals_list)


class RebuildAccountDeclaration(models.Model):
    _name = "rebuild.account.declaration"
    _description = "USL French Declaration Obligation"
    _order = "deadline_date, company_id, rule_id, instalment_number"

    _unique_declaration_instance = models.Constraint(
        "UNIQUE (company_id, rule_id, period_start, period_end, instalment_number)",
        "This declaration obligation already exists for the company and period.",
    )

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)
    rule_id = fields.Many2one("rebuild.account.declaration.rule", required=True, index=True)
    category = fields.Selection(related="rule_id.category", store=True, readonly=True)
    form_code = fields.Char(related="rule_id.form_code", store=True, readonly=True)
    rule_version = fields.Char(related="rule_id.version", store=True, readonly=True)
    definition_snapshot = fields.Json(readonly=True)
    official_source_label = fields.Char(related="rule_id.official_source_label", readonly=True)
    official_url = fields.Char(related="rule_id.official_url", readonly=True)
    portal_url = fields.Char(related="rule_id.portal_url", readonly=True)
    period_start = fields.Date(required=True, index=True)
    period_end = fields.Date(required=True, index=True)
    fiscalyear_start = fields.Date(required=True, index=True)
    fiscalyear_end = fields.Date(required=True, index=True)
    instalment_number = fields.Integer(default=0)
    deadline_window_start = fields.Date()
    deadline_date = fields.Date(required=True, index=True)
    deadline_basis = fields.Text(required=True)
    applicability = fields.Selection(
        [("applicable", "Applicable"), ("conditional", "Conditional Review"), ("not_applicable", "Not Applicable")],
        required=True,
        default="applicable",
        index=True,
    )
    applicability_reason = fields.Text(required=True)
    status = fields.Selection(
        [
            ("to_prepare", "To Prepare"),
            ("data_missing", "Data Missing"),
            ("internal_review", "Ready for Internal Review"),
            ("accountant_review", "Ready for Accountant Review"),
            ("accountant_reviewed", "Accountant Reviewed"),
            ("ready_to_file", "Ready to File Externally"),
            ("filed", "Filed Externally"),
            ("paid", "Paid / Refunded"),
            ("archived", "Archived"),
            ("blocked", "Blocked"),
            ("not_applicable", "Not Applicable"),
        ],
        required=True,
        default="to_prepare",
        index=True,
    )
    validation_status = fields.Selection(
        [("not_run", "Not Run"), ("ready", "Ready"), ("warning", "Warning"), ("blocked", "Blocked")],
        required=True,
        default="not_run",
        index=True,
    )
    review_status = fields.Selection(
        [
            ("not_started", "Not Started"),
            ("internal_ready", "Internal Review Ready"),
            ("accountant_requested", "Accountant Review Requested"),
            ("accepted", "Accepted by Reviewer"),
            ("accepted_with_difference", "Accepted with Difference"),
            ("rejected", "Rejected / Changes Required"),
        ],
        required=True,
        default="not_started",
    )
    filing_status = fields.Selection(
        [
            ("not_started", "Not Started"),
            ("portal_draft", "Draft in Portal"),
            ("submitted", "Submitted Externally"),
            ("accepted", "Accepted Externally"),
            ("rejected", "Rejected Externally"),
        ],
        required=True,
        default="not_started",
    )
    payment_status = fields.Selection(
        [
            ("not_assessed", "Not Assessed"),
            ("not_due", "No Payment Due"),
            ("due", "Payment Due"),
            ("partially_paid", "Partially Paid"),
            ("paid", "Paid"),
            ("credit", "Credit Carried Forward"),
            ("refund_requested", "Refund Requested"),
            ("refunded", "Refunded"),
        ],
        required=True,
        default="not_assessed",
    )
    acceptance_status = fields.Selection(
        [
            ("not_submitted", "Not Submitted"),
            ("pending", "Pending"),
            ("accepted", "Accepted"),
            ("rejected", "Rejected"),
        ],
        required=True,
        default="not_submitted",
    )
    amount_due = fields.Monetary(currency_field="currency_id")
    amount_paid = fields.Monetary(currency_field="currency_id")
    credit_amount = fields.Monetary(currency_field="currency_id")
    refund_amount = fields.Monetary(currency_field="currency_id")
    field_line_ids = fields.One2many("rebuild.account.declaration.field", "declaration_id", string="Prefilled Fields")
    prefilled_line_count = fields.Integer(compute="_compute_counts")
    unresolved_count = fields.Integer(compute="_compute_counts")
    validation_summary = fields.Text()
    unresolved_information = fields.Text()
    portal_entry_guidance = fields.Text()
    external_filing_reference = fields.Char()
    evidence_reference = fields.Char()
    evidence_attachment_ids = fields.Many2many(
        "ir.attachment",
        "rebuild_declaration_attachment_rel",
        "declaration_id",
        "attachment_id",
        string="Filing and Review Evidence",
    )
    last_refreshed_at = fields.Datetime(readonly=True)
    filed_at = fields.Datetime(readonly=True)
    filed_by_id = fields.Many2one("res.users", readonly=True)
    paid_at = fields.Datetime(readonly=True)
    paid_by_id = fields.Many2one("res.users", readonly=True)
    is_overdue = fields.Boolean(compute="_compute_is_overdue", search="_search_is_overdue")

    @api.depends("field_line_ids", "field_line_ids.is_unresolved")
    def _compute_counts(self):
        for declaration in self:
            declaration.prefilled_line_count = len(declaration.field_line_ids)
            declaration.unresolved_count = len(declaration.field_line_ids.filtered("is_unresolved"))

    @api.depends("deadline_date", "status")
    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        finished = {"filed", "paid", "archived", "not_applicable"}
        for declaration in self:
            declaration.is_overdue = bool(declaration.deadline_date and declaration.deadline_date < today and declaration.status not in finished)

    @api.model
    def _search_is_overdue(self, operator, value):
        positive = (operator in ("=", "==") and value) or (operator == "!=" and not value)
        domain = [
            ("deadline_date", "<", fields.Date.context_today(self)),
            ("status", "not in", ["filed", "paid", "archived", "not_applicable"]),
        ]
        return domain if positive else ["!", *domain]

    @api.model
    def sync_for_company(self, company):
        company.ensure_one()
        if not company.rebuild_declaration_profile_active:
            return self.browse()
        today = fields.Date.context_today(self)
        current_start, current_end = (
            company.rebuild_compute_fiscalyear_dates(today)
        )
        periods = [(current_start, current_end)]
        if company.fiscalyear_lock_date:
            locked_start, locked_end = (
                company.rebuild_compute_fiscalyear_dates(
                    company.fiscalyear_lock_date,
                )
            )
            if (locked_start, locked_end) not in periods:
                periods.insert(0, (locked_start, locked_end))
        declarations = self.browse()
        for fiscal_start, fiscal_end in periods:
            for rule in self._rules_for_period(company, fiscal_end):
                if not self._rule_applies_to_profile(rule, company, fiscal_end):
                    continue
                declarations |= self._sync_rule_instances(company, rule, fiscal_start, fiscal_end)
        declarations.action_refresh_preparation()
        return declarations

    @api.model
    def _rules_for_period(self, company, fiscal_end):
        country = (
            company.account_fiscal_country_id
            or company.country_id
        )
        candidates = self.env[
            "rebuild.account.declaration.rule"
        ].with_context(active_test=False).search([
            ("country_id", "=", country.id),
            ("company_id", "in", [False, company.id]),
            ("effective_from", "<=", fiscal_end),
            "|",
            ("effective_to", "=", False),
            ("effective_to", ">=", fiscal_end),
        ], order="sequence, code, company_id desc, effective_from desc")
        selected = self.env["rebuild.account.declaration.rule"]
        for code in candidates.mapped("code"):
            matching = candidates.filtered(lambda rule: rule.code == code)
            company_rule = matching.filtered(
                lambda rule: rule.company_id == company,
            )[:1]
            rule = company_rule or matching.filtered(
                lambda item: not item.company_id,
            )[:1]
            if rule and rule.active and rule.lifecycle == "current":
                selected |= rule
        return selected.sorted(lambda rule: (rule.sequence, rule.code))

    @api.model
    def _rule_applies_to_profile(self, rule, company, fiscal_end):
        if rule.effective_from > fiscal_end or (rule.effective_to and rule.effective_to < fiscal_end):
            return False
        if rule.corporate_tax_required and company.rebuild_corporate_tax_regime != "is":
            return False
        if rule.profit_tax_regime != "any" and rule.profit_tax_regime != company.rebuild_profit_tax_regime:
            return False
        if rule.vat_regime != "any" and rule.vat_regime != company.rebuild_vat_regime:
            return False
        if rule.code == "FR_2069_RCI" and not self._has_tax_credit_signal(company, fiscal_start=None, fiscal_end=fiscal_end):
            return False
        return not (
            rule.code == "FR_RCM_2777"
            and not self._has_dividend_signal(company, fiscal_start=None, fiscal_end=fiscal_end)
        )

    @api.model
    def _has_tax_credit_signal(self, company, fiscal_start, fiscal_end):
        return bool(self.env["rebuild.account.external.report.value"].search_count([
            ("company_id", "=", company.id),
            ("form_code", "ilike", "2069"),
            ("review_status", "!=", "superseded"),
        ]))

    @api.model
    def _has_dividend_signal(self, company, fiscal_start, fiscal_end):
        domain = [
            ("company_id", "=", company.id),
            ("move_id.state", "=", "posted"),
            ("account_id.code", "=like", "457%"),
            ("date", "<=", fiscal_end),
        ]
        if fiscal_start:
            domain.append(("date", ">=", fiscal_start))
        return bool(self.env["account.move.line"].with_company(company).search_count(domain))

    @api.model
    def _sync_rule_instances(self, company, rule, fiscal_start, fiscal_end):
        if rule.cadence == "is_instalments":
            deadlines = self._is_instalment_deadlines(fiscal_end)
            return self.browse().union(*[
                self._upsert_instance(company, rule, fiscal_start, fiscal_end, number, deadline, deadline)
                for number, deadline in enumerate(deadlines, start=1)
            ])
        if rule.cadence == "vat_instalments":
            windows = self._vat_instalment_windows(fiscal_start, fiscal_end)
            return self.browse().union(*[
                self._upsert_instance(company, rule, fiscal_start, fiscal_end, number, start, end)
                for number, (start, end) in enumerate(windows, start=1)
            ])
        deadline = self._annual_deadline(rule.code, fiscal_end)
        return self._upsert_instance(company, rule, fiscal_start, fiscal_end, 0, deadline, deadline)

    @api.model
    def _upsert_instance(self, company, rule, fiscal_start, fiscal_end, instalment_number, window_start, deadline):
        declaration = self.search([
            ("company_id", "=", company.id),
            ("rule_id", "=", rule.id),
            ("period_start", "=", fiscal_start),
            ("period_end", "=", fiscal_end),
            ("instalment_number", "=", instalment_number),
        ], limit=1)
        suffix = f" - instalment {instalment_number}" if instalment_number else ""
        vals = {
            "name": f"{rule.form_code}{suffix} - FY ending {fields.Date.to_string(fiscal_end)}",
            "company_id": company.id,
            "rule_id": rule.id,
            "period_start": fiscal_start,
            "period_end": fiscal_end,
            "fiscalyear_start": fiscal_start,
            "fiscalyear_end": fiscal_end,
            "instalment_number": instalment_number,
            "deadline_window_start": window_start,
            "deadline_date": deadline,
            "deadline_basis": self._deadline_basis(rule, fiscal_end, window_start, deadline),
            "applicability": "conditional" if rule.conditional else "applicable",
            "applicability_reason": self._applicability_reason(company, rule),
            "portal_entry_guidance": rule.filing_guidance,
        }
        if declaration:
            declaration.write(vals)
        else:
            declaration = self.create({
                **vals,
                "definition_snapshot": rule._definition_snapshot(),
            })
        return declaration

    @api.model
    def _applicability_reason(self, company, rule):
        profile = (
            f"{dict(company._fields['rebuild_legal_form'].selection).get(company.rebuild_legal_form, company.rebuild_legal_form)}, "
            f"{dict(company._fields['rebuild_corporate_tax_regime'].selection).get(company.rebuild_corporate_tax_regime, company.rebuild_corporate_tax_regime)}, "
            f"{dict(company._fields['rebuild_profit_tax_regime'].selection).get(company.rebuild_profit_tax_regime, company.rebuild_profit_tax_regime)}, "
            f"{dict(company._fields['rebuild_vat_regime'].selection).get(company.rebuild_vat_regime, company.rebuild_vat_regime)}"
        )
        return f"{rule.applicability_guidance}\nConfirmed company profile: {profile}."

    @api.model
    def _deadline_basis(self, rule, fiscal_end, window_start, deadline):
        if rule.cadence == "vat_instalments":
            return (
                f"{rule.deadline_guidance} The task uses the official 15-24 payment window; "
                f"the conservative displayed deadline is {fields.Date.to_string(deadline)}. "
                "Confirm the company's exact day in the professional tax portal."
            )
        return (
            f"{rule.deadline_guidance} Computed from the configured fiscal-year end "
            f"{fields.Date.to_string(fiscal_end)} under rule version {rule.version}."
        )

    @api.model
    def _annual_deadline(self, code, fiscal_end):
        if code in {"FR_2065", "FR_2033", "FR_2069_RCI"}:
            return _month_end(fiscal_end + relativedelta(months=3)) + relativedelta(days=15)
        if code == "FR_2572":
            return (fiscal_end + relativedelta(months=4)).replace(day=15)
        if code == "FR_3517_S":
            return _month_end(fiscal_end + relativedelta(months=3))
        return fiscal_end + relativedelta(months=3)

    @api.model
    def _is_instalment_deadlines(self, fiscal_end):
        marker = (fiscal_end.month, fiscal_end.day)
        year = fiscal_end.year
        if (2, 20) <= marker <= (5, 19):
            return [date(year - 1, 6, 15), date(year - 1, 9, 15), date(year - 1, 12, 15), date(year, 3, 15)]
        if (5, 20) <= marker <= (8, 19):
            return [date(year - 1, 9, 15), date(year - 1, 12, 15), date(year, 3, 15), date(year, 6, 15)]
        if (8, 20) <= marker <= (11, 19):
            return [date(year - 1, 12, 15), date(year, 3, 15), date(year, 6, 15), date(year, 9, 15)]
        return [date(year, 3, 15), date(year, 6, 15), date(year, 9, 15), date(year, 12, 15)]

    @api.model
    def _vat_instalment_windows(self, fiscal_start, fiscal_end):
        windows = []
        for year in range(fiscal_start.year, fiscal_end.year + 1):
            for month in (7, 12):
                start = date(year, month, 15)
                end = date(year, month, 24)
                if fiscal_start <= end <= fiscal_end:
                    windows.append((start, end))
        return windows

    def action_refresh_preparation(self):
        for declaration in self:
            declaration._sync_field_lines()
            unresolved = declaration.field_line_ids.filtered("is_unresolved")
            messages = []
            if unresolved:
                messages.append(f"{len(unresolved)} prefilled field(s) require external input or reviewer resolution.")
            mismatches = declaration.field_line_ids.filtered(lambda line: line.validation_status == "mismatch")
            if mismatches:
                messages.append(f"{len(mismatches)} field validation mismatch(es) remain.")
            if declaration.applicability == "not_applicable":
                validation_status = "ready"
                next_status = "not_applicable"
            elif unresolved or mismatches:
                validation_status = "blocked"
                next_status = "data_missing"
            else:
                validation_status = "ready"
                next_status = "internal_review"
                messages.append("Ledger-derived fields and confirmed facts currently pass automated checks.")
            vals = {
                "validation_status": validation_status,
                "validation_summary": "\n".join(messages),
                "unresolved_information": "\n".join(unresolved.mapped("unresolved_reason")),
                "last_refreshed_at": fields.Datetime.now(),
            }
            if declaration.status in {"to_prepare", "data_missing", "internal_review", "blocked"}:
                vals["status"] = next_status
            declaration.write(vals)
        return True

    def _sync_field_lines(self):
        self.ensure_one()
        FieldLine = self.env["rebuild.account.declaration.field"]
        seen_codes = set()
        tax_form_codes = [code.strip() for code in (self.rule_id.tax_form_codes or "").split(",") if code.strip()]
        if tax_form_codes:
            period_key = BENCHMARK_PERIOD_KEY if self.fiscalyear_end <= BENCHMARK_END else CURRENT_PERIOD_KEY
            tax_lines = self.env["rebuild.account.french.tax.package.line"].search([
                ("company_id", "=", self.company_id.id),
                ("period_key", "=", period_key),
                ("form_code", "in", tax_form_codes),
            ])
            for tax_line in tax_lines:
                code = tax_line.field_code
                seen_codes.add(code)
                unresolved = tax_line.review_status != "ledger_derived"
                FieldLine._upsert(self, code, {
                    "form_code": tax_line.form_code,
                    "field_label": tax_line.field_label,
                    "amount": tax_line.rounded_amount,
                    "value_text": tax_line.value_text,
                    "source_kind": tax_line.source_kind,
                    "source_formula": tax_line.source_formula,
                    "account_prefixes": tax_line.drilldown_account_prefixes,
                    "source_reference": f"Tax-package mapping {tax_line.period_key} / {tax_line.field_code}",
                    "tax_package_line_id": tax_line.id,
                    "is_unresolved": unresolved,
                    "unresolved_reason": (
                        f"{tax_line.field_label}: {dict(tax_line._fields['review_status'].selection).get(tax_line.review_status)}."
                        if unresolved else False
                    ),
                    "validation_status": "review" if unresolved else "matched",
                })
        self._sync_rule_specific_fields(seen_codes)
        stale = self.field_line_ids.filtered(lambda line: line.field_code not in seen_codes)
        stale.unlink()

    def _sync_rule_specific_fields(self, seen_codes):
        self.ensure_one()
        code = self.rule_id.code
        if code in {"FR_2065", "FR_2033"}:
            placeholders = []
            if code == "FR_2065":
                placeholders = [
                    ("2065_BIS_ADMIN_REVIEW", "2065-bis administrative and ownership information", "Company registry, ownership and tax-group facts must be confirmed in the official portal."),
                ]
            else:
                placeholders = [
                    ("2033_E_VALUE_ADDED_REVIEW", "2033-E value added and workforce", "Validate value added, workforce and CFE/CVAE information against payroll and tax evidence."),
                    ("2033_F_OWNERSHIP_REVIEW", "2033-F shareholding composition", "Confirm shareholder identity and ownership from the current corporate register."),
                    ("2033_G_SUBSIDIARIES_REVIEW", "2033-G subsidiaries and holdings", "Confirm whether any subsidiary or holding interest must be disclosed."),
                ]
            for field_code, label, reason in placeholders:
                seen_codes.add(field_code)
                self.env["rebuild.account.declaration.field"]._upsert(self, field_code, {
                    "form_code": self.form_code,
                    "field_label": label,
                    "source_kind": "external_confirmation",
                    "source_formula": "Not safely derivable from the general ledger alone.",
                    "source_reference": "Company registry / payroll / accountant evidence",
                    "is_unresolved": True,
                    "unresolved_reason": reason,
                    "validation_status": "review",
                })
        if code in {"FR_2571", "FR_2572"}:
            self._sync_corporate_tax_payment_fields(seen_codes)
        if code in {"FR_3517_S", "FR_3514"}:
            self._sync_vat_facts(seen_codes)

    def _sync_corporate_tax_payment_fields(self, seen_codes):
        prior_period = BENCHMARK_PERIOD_KEY if self.fiscalyear_start >= CURRENT_START else False
        charge = self.env["rebuild.account.french.tax.package.line"]
        if prior_period:
            charge = charge.search([
                ("company_id", "=", self.company_id.id),
                ("period_key", "=", prior_period),
                ("field_code", "=", "2065_CHARGE_IS_COMPTABILISEE"),
            ], limit=1)
        amount = charge.rounded_amount if charge else 0.0
        estimated = amount / 4 if self.rule_id.code == "FR_2571" else amount
        field_code = "2571_PROVISIONAL_INSTALMENT" if self.rule_id.code == "FR_2571" else "2572_LEDGER_IS_CHARGE_REVIEW"
        label = "Provisional IS instalment from prior accounting charge" if self.rule_id.code == "FR_2571" else "Accounting IS charge before tax-return adjustments"
        seen_codes.add(field_code)
        self.env["rebuild.account.declaration.field"]._upsert(self, field_code, {
            "form_code": self.form_code,
            "field_label": label,
            "amount": estimated,
            "source_kind": "ledger_review_anchor",
            "source_formula": "Prior fiscal-year account 695 charge divided by four" if self.rule_id.code == "FR_2571" else "Prior fiscal-year account 695 charge",
            "account_prefixes": "695",
            "source_reference": charge.field_code if charge else "No prior fiscal-year 2065 accounting-charge mapping exists",
            "is_unresolved": True,
            "unresolved_reason": "Confirm taxable profit, reduced-rate eligibility, prior instalments and the amount shown in the professional tax portal before payment.",
            "validation_status": "review",
        })

    def _sync_vat_facts(self, seen_codes):
        FieldLine = self.env["rebuild.account.declaration.field"]
        facts = [
            ("USL_CA12_OPENING_CREDIT", "Opening VAT credit", 0.0, "Confirmed USL filing fact", False, False),
            ("USL_CA12_INSTALMENTS_PAID", "VAT instalments paid", 0.0, "Confirmed USL filing fact", False, False),
        ]
        if (
            self.rule_id.code == "FR_3517_S"
            and (self.fiscalyear_end == BENCHMARK_END or self.fiscalyear_start >= CURRENT_START)
        ):
            facts.extend([
                ("USL_CA12_REFUND_ACCEPTED", "VAT refund requested, accepted and reimbursed", 2500.0, "Ledger accounts 445830/445670 and DGFiP bank settlement", False, False),
                ("USL_CA12_LATER_REFUND", "Later VAT credit reimbursed", 942.0, "DGFiP bank settlement and account 445670", False, False),
                ("USL_CA12_REMAINING_CREDIT", "Remaining VAT credit after both refunds", 0.0, "3442 credit less 2500 and 942 refunds", False, False),
            ])
        for field_code, label, amount, source, unresolved, reason in facts:
            seen_codes.add(field_code)
            FieldLine._upsert(self, field_code, {
                "form_code": self.form_code,
                "field_label": label,
                "amount": amount,
                "source_kind": "confirmed_fact",
                "source_formula": source,
                "account_prefixes": "445670,445830,512,471" if amount else "445670,445830",
                "source_reference": "Milestone 13 confirmed VAT facts and reconstructed ledger",
                "is_unresolved": unresolved,
                "unresolved_reason": reason,
                "validation_status": "matched",
            })
        if self.rule_id.code == "FR_3514":
            self.write({"amount_due": 0.0, "payment_status": "not_due"})
        if self.rule_id.code == "FR_3517_S":
            self._sync_vat_refund_control(seen_codes)

    def _sync_vat_refund_control(self, seen_codes):
        seen_codes.add("USL_CA12_942_LEDGER_CLASSIFICATION")
        vat_line = self.env["account.move.line"].with_company(self.company_id).search([
            ("company_id", "=", self.company_id.id),
            ("account_id.code", "=like", "445670%"),
            ("move_id.state", "=", "posted"),
        ])
        vat_residual = sum(vat_line.mapped("amount_residual"))
        suspense_refund = self.env["account.bank.statement.line"].search([
            ("company_id", "=", self.company_id.id),
            ("date", ">=", date(2026, 7, 1)),
            ("amount", ">", 941.99),
            ("amount", "<", 942.01),
            ("payment_ref", "ilike", "DGFiP"),
            ("is_reconciled", "=", False),
        ], limit=1)
        mismatch = bool(suspense_refund) or float_compare(vat_residual, 0.0, precision_rounding=self.currency_id.rounding) != 0
        self.env["rebuild.account.declaration.field"]._upsert(self, "USL_CA12_942_LEDGER_CLASSIFICATION", {
            "form_code": self.form_code,
            "field_label": "€942 refund ledger classification control",
            "amount": vat_residual,
            "source_kind": "ledger_control",
            "source_formula": "Residual on account 445670 after the €2,500 and €942 DGFiP refunds; expected zero.",
            "account_prefixes": "445670,471,512",
            "source_reference": suspense_refund.display_name if suspense_refund else "No open €942 DGFiP bank line",
            "is_unresolved": mismatch,
            "unresolved_reason": "The €942 DGFiP bank receipt remains on suspense and must be matched to account 445670." if mismatch else False,
            "validation_status": "mismatch" if mismatch else "matched",
        })

    def _trace_vat_refund_correction_children(self, correction, trace_values, trace_id):
        line_trace = {
            **trace_values,
            "rebuild_source_model": "account.move.line.usl_vat_refund_reclassification",
            "rebuild_import_note": (
                f"Source-preserving reclassification child of bank statement line {trace_id}: "
                "confirmed €942 DGFiP VAT refund."
            ),
        }
        for index, line in enumerate(correction.line_ids.sorted("id"), start=1):
            line.write({**line_trace, "rebuild_source_id": (trace_id * 10) + index})
        traced_partials = (
            correction.line_ids.mapped("matched_debit_ids")
            | correction.line_ids.mapped("matched_credit_ids")
        )
        partial_trace = {
            **trace_values,
            "rebuild_source_model": "account.partial.reconcile.usl_vat_refund_reclassification",
            "rebuild_import_note": (
                f"Native reconciliation child of bank statement line {trace_id}: "
                "confirmed €942 DGFiP VAT-refund reclassification."
            ),
        }
        for index, partial in enumerate(traced_partials.sorted("id"), start=1):
            partial.write({**partial_trace, "rebuild_source_id": (trace_id * 100) + index})

    def action_classify_confirmed_vat_refund(self):
        self.ensure_one()
        if not self.env.user.has_group("account.group_account_manager"):
            message = "Only an Accounting Manager can classify the confirmed VAT refund."
            raise AccessError(message)
        bank_line = self.env["account.bank.statement.line"].with_company(self.company_id).search([
            ("company_id", "=", self.company_id.id),
            ("date", ">=", date(2026, 7, 1)),
            ("amount", ">", 941.99),
            ("amount", "<", 942.01),
            ("payment_ref", "ilike", "DGFiP"),
        ], limit=1)
        if not bank_line:
            message = "The exact €942 DGFiP bank line was not found."
            raise UserError(message)
        target_line = self.env["account.move.line"].with_company(self.company_id).search([
            ("company_id", "=", self.company_id.id),
            ("account_id.code", "=like", "445670%"),
            ("move_id.state", "=", "posted"),
            ("amount_residual", ">", 941.99),
        ], order="date desc, id desc", limit=1)
        trace_id = bank_line.rebuild_source_id or bank_line.id
        correction = self.env["account.move"].search([
            ("company_id", "=", self.company_id.id),
            ("rebuild_source_model", "=", "account.move.usl_vat_refund_reclassification"),
            ("rebuild_source_id", "=", trace_id),
            ("state", "!=", "cancel"),
        ], limit=1)
        trace_values = {
            "rebuild_source_database": bank_line.rebuild_source_database,
            "rebuild_source_snapshot": bank_line.rebuild_source_snapshot,
            "rebuild_import_run_id": bank_line.rebuild_import_run_id.id,
            "rebuild_import_status": "transformed",
        }
        if not target_line and correction:
            target_line = correction.line_ids.filtered(
                lambda line: (line.account_id.with_company(self.company_id).code or "").startswith("445670"),
            )[:1]
        if not target_line:
            message = "The open account-445670 VAT-credit source line was not found."
            raise UserError(message)
        misclassified_line = bank_line.move_id.line_ids.filtered(
            lambda line: (line.account_id.with_company(self.company_id).code or "").startswith("471")
            and line.account_id != bank_line.journal_id.default_account_id,
        )
        if len(misclassified_line) != 1 or float_compare(
            misclassified_line.balance,
            -942.0,
            precision_rounding=self.currency_id.rounding,
        ) != 0:
            message = "The €942 DGFiP bank entry does not have the expected single account-471 counterpart."
            raise UserError(message)
        if not correction:
            journal = self.env["account.journal"].search([
                ("company_id", "=", self.company_id.id),
                ("type", "=", "general"),
            ], order="sequence, id", limit=1)
            if not journal:
                message = "A general journal is required for the VAT-refund reclassification entry."
                raise UserError(message)
            move_trace = {
                **trace_values,
                "rebuild_source_id": trace_id,
                "rebuild_source_model": "account.move.usl_vat_refund_reclassification",
                "rebuild_import_note": (
                    "Confirmed correction of the imported €942 DGFiP bank receipt: "
                    "clear bank suspense account 471 and classify the refund against VAT credit account 445670."
                ),
            }
            line_trace = {
                **trace_values,
                "rebuild_source_model": "account.move.line.usl_vat_refund_reclassification",
                "rebuild_import_note": (
                    "Source-preserving reclassification of the confirmed €942 DGFiP VAT refund."
                ),
            }
            correction = self.env["account.move"].with_company(self.company_id).create({
                "move_type": "entry",
                "company_id": self.company_id.id,
                "journal_id": journal.id,
                "date": bank_line.date,
                "ref": "USL €942 DGFiP VAT refund reclassification",
                **move_trace,
                "line_ids": [
                    (0, 0, {
                        "name": "Clear imported DGFiP refund suspense",
                        "account_id": misclassified_line.account_id.id,
                        "partner_id": bank_line.partner_id.id,
                        "debit": 942.0,
                        "credit": 0.0,
                        **line_trace,
                        "rebuild_source_id": (trace_id * 10) + 1,
                    }),
                    (0, 0, {
                        "name": "Classify confirmed DGFiP VAT refund",
                        "account_id": target_line.account_id.id,
                        "partner_id": bank_line.partner_id.id,
                        "debit": 0.0,
                        "credit": 942.0,
                        **line_trace,
                        "rebuild_source_id": (trace_id * 10) + 2,
                    }),
                ],
            })
        if correction.state == "draft":
            correction.action_post()
        correction_clearing = correction.line_ids.filtered(
            lambda line: line.account_id == misclassified_line.account_id,
        )
        if not misclassified_line.reconciled:
            (misclassified_line | correction_clearing).reconcile()
        vat_lines = self.env["account.move.line"].with_company(self.company_id).search([
            ("company_id", "=", self.company_id.id),
            ("account_id", "=", target_line.account_id.id),
            ("move_id.state", "=", "posted"),
            ("reconciled", "=", False),
        ])
        vat_residual = sum(vat_lines.mapped("amount_residual"))
        if float_compare(vat_residual, 0.0, precision_rounding=self.currency_id.rounding) != 0:
            message = "The open account-445670 VAT lines do not net to zero after the €942 reclassification."
            raise UserError(message)
        vat_lines.reconcile()
        self._trace_vat_refund_correction_children(correction, trace_values, trace_id)
        # The exact importer updates the inherited statement-line bridge with source values through SQL.
        # Recompute the stored native state after restoring the target reconciliation graph.
        bank_line._compute_is_reconciled()
        if not bank_line.is_reconciled:
            message = "The €942 DGFiP bank line remains unreconciled after clearing its account-471 counterpart."
            raise UserError(message)
        self.action_refresh_preparation()
        return True

    def action_mark_internal_ready(self):
        self.action_refresh_preparation()
        for declaration in self:
            if declaration.validation_status == "blocked":
                message = "Resolve the declaration's missing or mismatched fields before internal review."
                raise UserError(message)
            declaration.write({"status": "internal_review", "review_status": "internal_ready"})
        return True

    def action_request_accountant_review(self):
        self.action_mark_internal_ready()
        self.write({"status": "accountant_review", "review_status": "accountant_requested"})
        return True

    def action_mark_ready_to_file(self):
        if not self.env.user.has_group("account.group_account_manager"):
            raise AccessError(
                "Only an Accounting Manager can approve a declaration for filing.",
            )
        self.action_mark_internal_ready()
        self.write({"status": "ready_to_file"})
        return True

    def action_record_review_decision(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Declaration Review Decision",
            "res_model": "rebuild.account.assurance.decision",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_gate": "declaration_review",
                "default_conclusion": "pending",
                "default_required_authority": "accountant",
                "default_company_id": self.company_id.id,
                "default_period_key": f"{self.period_start}:{self.period_end}",
                "default_declaration_id": self.id,
                "default_evidence_key": f"declaration:{self.rule_id.code}:{self.period_end}:{self.instalment_number}",
                "default_source_value": self.validation_summary,
                "default_decision_summary": "Pending review.",
                "default_evidence_summary": self.evidence_reference,
                "default_remaining_risk": self.unresolved_information,
                "default_next_action": "Accept, accept with a documented difference, or require changes before external filing.",
            },
        }

    def action_mark_filed(self):
        if not self.env.user.has_group("account.group_account_manager"):
            message = "Only an Accounting Manager can record external filing."
            raise AccessError(message)
        for declaration in self:
            if declaration.status not in {"ready_to_file", "accountant_reviewed"}:
                message = (
                    "Approve the declaration for filing or complete the "
                    "optional accountant review before recording external filing."
                )
                raise UserError(message)
            if not declaration.external_filing_reference and not declaration.evidence_attachment_ids:
                message = "Attach filing evidence or record the external filing reference before marking a declaration filed."
                raise UserError(message)
            declaration.write({
                "status": "filed",
                "filing_status": "submitted",
                "acceptance_status": "pending",
                "filed_at": fields.Datetime.now(),
                "filed_by_id": self.env.user.id,
            })
        return True

    def action_mark_paid_or_refunded(self):
        if not self.env.user.has_group("account.group_account_manager"):
            message = "Only an Accounting Manager can record declaration payment or refund completion."
            raise AccessError(message)
        for declaration in self:
            if declaration.status != "filed":
                message = "Record external filing before marking payment or refund completion."
                raise UserError(message)
            if declaration.payment_status not in {"paid", "refunded", "not_due"}:
                message = "Set payment status to Paid, Refunded or No Payment Due before completing the obligation."
                raise UserError(message)
            declaration.write({"status": "paid", "paid_at": fields.Datetime.now(), "paid_by_id": self.env.user.id})
        return True

    def action_open_prefilled_fields(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"{self.form_code} Prefilled Fields",
            "res_model": "rebuild.account.declaration.field",
            "view_mode": "list,form",
            "domain": [("declaration_id", "=", self.id)],
            "context": {"create": False, "delete": False},
        }

    def action_open_official_source(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": self.official_url, "target": "new"}

    def action_open_portal(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": self.portal_url, "target": "new"}


class RebuildAccountDeclarationField(models.Model):
    _name = "rebuild.account.declaration.field"
    _description = "USL French Declaration Prefilled Field"
    _order = "declaration_id, form_code, field_code"

    _unique_declaration_field = models.Constraint(
        "UNIQUE (declaration_id, field_code)",
        "A declaration field code must be unique within one obligation.",
    )

    declaration_id = fields.Many2one("rebuild.account.declaration", required=True, index=True, ondelete="cascade")
    company_id = fields.Many2one(related="declaration_id.company_id", store=True, readonly=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)
    form_code = fields.Char(required=True, index=True)
    field_code = fields.Char(required=True, index=True)
    field_label = fields.Char(required=True)
    amount = fields.Monetary(currency_field="currency_id")
    value_text = fields.Char()
    source_kind = fields.Selection(
        [
            ("annual_statement", "Annual Statement Mapping"),
            ("confirmed_fact", "Confirmed Company Fact"),
            ("depreciation_schedule", "Depreciation Schedule"),
            ("external_confirmation", "External Confirmation Needed"),
            ("fixed_asset_register", "Fixed Asset Register"),
            ("ledger_control", "Ledger Control"),
            ("ledger_review_anchor", "Ledger Review Starting Point"),
            ("manual_required", "External Value Needed"),
            ("vat_accounts", "VAT Ledger Accounts"),
        ],
        required=True,
        string="Value Source",
    )
    source_formula = fields.Text(required=True)
    account_prefixes = fields.Char()
    source_reference = fields.Char()
    tax_package_line_id = fields.Many2one("rebuild.account.french.tax.package.line", ondelete="set null")
    is_unresolved = fields.Boolean(string="Needs Follow-up", index=True)
    unresolved_reason = fields.Text()
    validation_status = fields.Selection(
        [("matched", "Matched"), ("review", "Review Required"), ("mismatch", "Mismatch")],
        required=True,
        default="review",
        index=True,
    )

    @api.model
    def _upsert(self, declaration, field_code, vals):
        line = self.search([("declaration_id", "=", declaration.id), ("field_code", "=", field_code)], limit=1)
        values = {"declaration_id": declaration.id, "field_code": field_code, **vals}
        if line:
            line.write(values)
        else:
            line = self.create(values)
        return line

    def action_open_source_items(self):
        self.ensure_one()
        if self.tax_package_line_id:
            return self.tax_package_line_id.action_open_journal_items()
        prefixes = [prefix.strip() for prefix in (self.account_prefixes or "").split(",") if prefix.strip()]
        if not prefixes:
            message = "This field has no ledger-account drill-down; use its explicit source reference."
            raise UserError(message)
        accounts = self.env["account.account"].with_company(self.company_id).search([
            ("company_ids", "in", self.company_id.id),
        ]).filtered(lambda account: any((account.code or "").startswith(prefix) for prefix in prefixes))
        return {
            "type": "ir.actions.act_window",
            "name": f"{self.form_code} - {self.field_label}",
            "res_model": "account.move.line",
            "view_mode": "list,form,pivot",
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("move_id.state", "=", "posted"),
                ("date", ">=", self.declaration_id.period_start),
                ("date", "<=", max(self.declaration_id.period_end, fields.Date.context_today(self))),
                ("account_id", "in", accounts.ids),
            ],
            "context": {"create": False, "delete": False},
        }
