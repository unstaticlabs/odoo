"""Company declaration profile and configurable declaration rules."""

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from .configurable_definition import ACCOUNTING_DEFINITION_ORIGINS


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
    rebuild_vat_transition_date = fields.Date(
        string="VAT Periodicity Transition Date",
        help=(
            "First transaction date governed by the next VAT periodicity. "
            "For a French simplified-regime company with a non-calendar "
            "financial year, this preserves the statutory 2027 transitional "
            "rule instead of switching blindly on 1 January."
        ),
    )
    rebuild_oss_registered = fields.Boolean(
        string="Registered for the EU OSS Scheme",
        help=(
            "Generate OSS filing periods only after registration has been "
            "confirmed. Foreign or platform revenue alone never enables OSS."
        ),
    )
    rebuild_oss_registration_evidence = fields.Text(
        string="OSS Registration Evidence",
    )
    rebuild_cfe_monthly_payment = fields.Boolean(
        string="CFE Paid Monthly",
        help=(
            "Suppress the June CFE advance when the company uses the monthly "
            "payment scheme. The annual CFE balance notice remains scheduled."
        ),
    )
    rebuild_first_fiscalyear_start = fields.Date(
        string="Exceptional First Fiscal-Year Start",
        help="Optional first-year exception used before the company's recurring fiscal-year cadence.",
    )
    rebuild_first_fiscalyear_end = fields.Date(
        string="Exceptional First Fiscal-Year End",
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
                    "fiscal year.",
                )
            if first_start and first_start > first_end:
                raise ValidationError(
                    "The exceptional first fiscal-year start must be before "
                    "or equal to its end.",
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
    _inherit = [
        "rebuild.account.configurable.definition.mixin",
        "mail.thread",
    ]
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
            ("eu_services", "EU Services"),
            ("local_tax", "French Local Business Taxes"),
            ("third_party_reporting", "Third-party Reporting"),
            ("legacy", "Legacy / Retired Workflow"),
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
    period_basis = fields.Selection(
        [
            ("fiscal_year", "Fiscal Year"),
            ("calendar_year", "Calendar Year"),
            ("calendar_quarter", "Calendar Quarter"),
            ("calendar_month", "Calendar Month"),
            ("event", "Dated Event"),
        ],
        required=True,
        default="fiscal_year",
        help="Legal period used to create filing instances; it is independent from the accounting fiscal year.",
    )
    trigger_kind = fields.Selection(
        [
            ("always", "Always for Matching Profile"),
            ("not_first_fiscal_year", "After First Fiscal Year"),
            ("dividend_transactions", "RCM Transactions"),
            ("eu_b2b_services", "Qualifying EU B2B Services"),
            ("oss_registration", "Confirmed OSS Registration"),
            ("das2_threshold", "DAS2 Review Threshold"),
            ("cvae_turnover_threshold", "CVAE Turnover Threshold"),
            ("cvae_prior_liability", "Prior CVAE Liability"),
            ("company_creation", "Company Creation"),
            ("cfe_annual", "Annual CFE Notice"),
            ("cfe_prior_liability", "Prior CFE Liability"),
            ("vat_transition", "VAT Regime Transition"),
        ],
        required=True,
        default="always",
    )
    deadline_rule = fields.Selection(
        [
            ("fiscal_default", "Fiscal-period Default"),
            ("is_instalments", "IS Instalment Band"),
            ("is_balance", "IS Balance"),
            ("result_return", "Result Return with Teleprocedure Extension"),
            ("vat_instalment", "VAT Instalment Portal Window"),
            ("ca12_calendar", "Calendar CA12"),
            ("ca12_fiscal", "Fiscal-year CA12-E"),
            ("next_month_15", "15th of Following Month"),
            ("next_year_feb_15", "15 February Following Year"),
            ("des_tenth_workday", "10th Working Day of Following Month"),
            ("quarter_next_month_end", "End of Month Following Quarter"),
            ("das2_campaign", "DAS2 Annual Campaign"),
            ("cfe_creation", "CFE Initial Declaration"),
            ("cfe_balance", "CFE Annual Balance"),
        ],
        required=True,
        default="fiscal_default",
    )
    filing_channel = fields.Selection(
        [("edi", "EDI"), ("efi", "EFI"), ("customs", "French Customs"), ("portal", "Specialized Portal")],
        default="edi",
        required=True,
    )
    payment_required = fields.Selection(
        [("no", "No"), ("conditional", "Conditional"), ("yes", "Yes")],
        default="conditional",
        required=True,
    )
    supporting_form_codes = fields.Char(
        help="Forms and annexes filed as components of this obligation rather than as duplicate declarations.",
    )
    threshold_amount = fields.Float(
        string="Primary Threshold",
        help=(
            "Statutory amount interpreted by the selected trigger, such as "
            "beneficiary payments, turnover or prior assessed tax."
        ),
    )
    secondary_threshold_amount = fields.Float(
        string="Secondary Threshold",
        help="Additional statutory amount that must also be satisfied by the trigger.",
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
            "period_basis": self.period_basis,
            "trigger_kind": self.trigger_kind,
            "deadline_rule": self.deadline_rule,
            "filing_channel": self.filing_channel,
            "payment_required": self.payment_required,
            "threshold_amount": self.threshold_amount,
            "secondary_threshold_amount": self.secondary_threshold_amount,
            "supporting_form_codes": self.supporting_form_codes or "",
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
            "period_basis",
            "trigger_kind",
            "deadline_rule",
            "filing_channel",
            "payment_required",
            "threshold_amount",
            "secondary_threshold_amount",
            "supporting_form_codes",
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
                "Use Customize for Company and edit the company definition.",
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
