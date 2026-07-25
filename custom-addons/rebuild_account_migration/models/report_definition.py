from odoo import api, fields, models
from odoo.exceptions import UserError

from .configurable_definition import ACCOUNTING_DEFINITION_ORIGINS


ACCOUNTING_REPORT_TYPES = [
    ("trial_balance", "Trial Balance"),
    ("general_ledger", "General Ledger"),
    ("journal_report", "Journal Report"),
    ("partner_ledger", "Partner Ledger"),
    ("customer_statement", "Customer Statement"),
    ("open_items", "Open Items"),
    ("aged_receivable", "Aged Receivable"),
    ("aged_payable", "Aged Payable"),
    ("balance_sheet", "Balance Sheet"),
    ("profit_loss", "Profit and Loss"),
    ("tax_report", "VAT and Tax Report"),
    ("tax_report_group_account_tax", "Tax Report by Account then Tax"),
    ("tax_report_group_tax_account", "Tax Report by Tax then Account"),
    ("ec_sales_list", "EC Sales List"),
    ("oss_sales", "OSS Sales"),
    ("oss_imports", "OSS Imports"),
    ("bank_reconciliation", "Bank Reconciliation"),
    ("currency_report", "Currency Gain, Loss and Exposure"),
    ("cash_flow", "Cash Flow Statement"),
    ("executive_summary", "Executive Summary"),
    ("analytic_report", "Analytic Distribution"),
    ("analytic_pivot", "Analytic Reporting"),
    ("fixed_assets", "Fixed Asset Register"),
    ("fixed_asset_group_account", "Fixed Asset Register by Account"),
    ("depreciation_schedule", "Depreciation Schedule"),
    ("deferred_schedule", "Deferred Expense and Revenue Schedule"),
    ("french_annual", "États financiers français"),
    ("french_balance_sheet_2024", "Bilan détaillé (PCG 2024)"),
    (
        "french_profit_loss_2024",
        "Compte de résultat détaillé (PCG 2024)",
    ),
    ("sig_caf_2024", "SIG et CAF (PCG 2024)"),
    ("french_tax_package", "French Tax Package Mapping"),
    ("closing_package", "Closing Review Package"),
    ("fec", "FEC"),
]


REPORT_ACTIONS = {
    "trial_balance": "rebuild_account_migration.action_rebuild_interactive_trial_balance",
    "general_ledger": "rebuild_account_migration.action_rebuild_interactive_general_ledger",
    "journal_report": "rebuild_account_migration.action_rebuild_interactive_journal_report",
    "partner_ledger": "rebuild_account_migration.action_rebuild_interactive_partner_ledger",
    "customer_statement": "rebuild_account_migration.action_rebuild_interactive_customer_statement",
    "open_items": "rebuild_account_migration.action_rebuild_interactive_open_items",
    "aged_receivable": "rebuild_account_migration.action_rebuild_interactive_aged_receivable",
    "aged_payable": "rebuild_account_migration.action_rebuild_interactive_aged_payable",
    "balance_sheet": "rebuild_account_migration.action_rebuild_interactive_balance_sheet",
    "profit_loss": "rebuild_account_migration.action_rebuild_interactive_profit_loss",
    "tax_report": "rebuild_account_migration.action_rebuild_interactive_tax_report",
    "tax_report_group_account_tax": "rebuild_account_migration.action_rebuild_account_report_export_tax_group_account_tax",
    "tax_report_group_tax_account": "rebuild_account_migration.action_rebuild_account_report_export_tax_group_tax_account",
    "ec_sales_list": "rebuild_account_migration.action_rebuild_account_report_export_ec_sales_list",
    "oss_sales": "rebuild_account_migration.action_rebuild_account_report_export_oss_sales",
    "oss_imports": "rebuild_account_migration.action_rebuild_account_report_export_oss_imports",
    "bank_reconciliation": "rebuild_account_migration.action_rebuild_interactive_bank_reconciliation",
    "currency_report": "rebuild_account_migration.action_rebuild_interactive_currency_report",
    "cash_flow": "rebuild_account_migration.action_rebuild_interactive_cash_flow",
    "executive_summary": "rebuild_account_migration.action_rebuild_interactive_executive_summary",
    "analytic_report": "rebuild_account_migration.action_rebuild_interactive_analytic_report",
    "analytic_pivot": "rebuild_account_migration.action_rebuild_analytic_reporting",
    "fixed_assets": "rebuild_account_migration.action_rebuild_interactive_fixed_assets",
    "fixed_asset_group_account": "rebuild_account_migration.action_rebuild_account_report_export_fixed_asset_group_account",
    "depreciation_schedule": "rebuild_account_migration.action_rebuild_interactive_depreciation_schedule",
    "deferred_schedule": "rebuild_account_migration.action_rebuild_interactive_deferred_schedule",
    "french_annual": "rebuild_account_migration.action_rebuild_interactive_french_annual",
    "french_balance_sheet_2024": "rebuild_account_migration.action_rebuild_interactive_french_balance_sheet_2024",
    "french_profit_loss_2024": "rebuild_account_migration.action_rebuild_interactive_french_profit_loss_2024",
    "sig_caf_2024": "rebuild_account_migration.action_rebuild_interactive_sig_caf_2024",
    "french_tax_package": "rebuild_account_migration.action_rebuild_interactive_french_tax_package",
    "fec": "rebuild_account_migration.action_rebuild_account_report_export_fec",
}


def _report_seed_values(report_type, name):
    french = report_type.startswith("french_") or report_type in {
        "sig_caf_2024",
        "fec",
    }
    partner = report_type in {
        "partner_ledger",
        "customer_statement",
        "open_items",
        "aged_receivable",
        "aged_payable",
    }
    tax = report_type in {
        "tax_report",
        "tax_report_group_account_tax",
        "tax_report_group_tax_account",
        "ec_sales_list",
        "oss_sales",
        "oss_imports",
        "french_tax_package",
        "fec",
    }
    management = report_type in {
        "cash_flow",
        "executive_summary",
        "analytic_report",
        "analytic_pivot",
    }
    schedule = report_type in {
        "fixed_assets",
        "fixed_asset_group_account",
        "depreciation_schedule",
        "deferred_schedule",
    }
    family = (
        "partner" if partner
        else "tax" if tax
        else "management" if management
        else "statement"
    )
    presentation = (
        "schedule" if schedule
        else "ledger" if report_type in {
            "general_ledger",
            "journal_report",
            "partner_ledger",
            "customer_statement",
            "open_items",
        }
        else "analysis" if management
        else "statement"
    )
    no_comparison = {
        "aged_receivable",
        "aged_payable",
        "fixed_assets",
        "fixed_asset_group_account",
        "depreciation_schedule",
        "deferred_schedule",
        "closing_package",
        "fec",
    }
    no_analytics = {
        "bank_reconciliation",
        "fixed_assets",
        "fixed_asset_group_account",
        "depreciation_schedule",
        "deferred_schedule",
        "closing_package",
        "fec",
    }
    default_groups = {
        "trial_balance": "section",
        "general_ledger": "account",
        "partner_ledger": "partner",
        "customer_statement": "partner",
        "open_items": "partner",
        "balance_sheet": "section",
        "profit_loss": "section",
        "french_annual": "section",
        "french_balance_sheet_2024": "section",
        "french_profit_loss_2024": "section",
        "sig_caf_2024": "section",
        "tax_report": "section",
        "analytic_report": "analytic",
        "analytic_pivot": "analytic",
    }
    return {
        "name": name,
        "code": report_type,
        "report_type": report_type,
        "sequence": 10,
        "origin": "localization" if french else (
            "oca" if schedule else "usl"
        ),
        "source_module": (
            "l10n_fr_account" if report_type == "fec"
            else "account_asset_management" if schedule
            else "rebuild_account_migration"
        ),
        "definition_version": "saas~19.2.1",
        "lifecycle": "current",
        "business_purpose": (
            f"Provide the governed {name} used for accounting review, "
            "investigation, and evidence."
        ),
        "expected_outcome": (
            "The screen and exports show the same company, period, filters, "
            "hierarchy, calculations, and totals."
        ),
        "family": family,
        "presentation_style": presentation,
        "navigation_group": family,
        "default_group_by": default_groups.get(report_type, "none"),
        "target_action_xmlid": REPORT_ACTIONS.get(report_type),
        "supports_comparison": report_type not in no_comparison,
        "supports_journals": not schedule,
        "supports_accounts": report_type not in {
            "bank_reconciliation",
            "closing_package",
        },
        "supports_partners": report_type not in no_analytics,
        "supports_analytics": report_type not in no_analytics,
        "supports_pdf": report_type != "fec",
        "supports_xlsx": report_type != "fec",
        "technical_key": report_type,
        "technical_model": "rebuild.account.report.export.wizard",
        "technical_summary": (
            "Whitelisted report engine key resolved by the canonical USL "
            "report client. Configuration never executes arbitrary code."
        ),
    }


class RebuildAccountReportDefinition(models.Model):
    _name = "rebuild.account.report.definition"
    _description = "Accounting Report Definition"
    _inherit = ["rebuild.account.configurable.definition.mixin"]
    _order = "family, sequence, name, company_id"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True, readonly=True)
    report_type = fields.Selection(
        ACCOUNTING_REPORT_TYPES,
        required=True,
        index=True,
        readonly=True,
    )
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        "res.company",
        index=True,
        help=(
            "Leave empty for a shared definition. A company definition "
            "overrides the shared definition with the same code."
        ),
    )
    family = fields.Selection(
        [
            ("statement", "Statement Reports"),
            ("partner", "Partner Reports"),
            ("tax", "Taxes and Fiscal"),
            ("management", "Management"),
        ],
        required=True,
        default="statement",
        index=True,
    )
    presentation_style = fields.Selection(
        [
            ("statement", "Financial Statement"),
            ("ledger", "Ledger"),
            ("schedule", "Schedule / Register"),
            ("analysis", "Management Analysis"),
        ],
        required=True,
        default="statement",
    )
    navigation_group = fields.Selection(
        [
            ("statement", "Reporting / Statement Reports"),
            ("partner", "Reporting / Partner Reports"),
            ("tax", "Reporting / Taxes and Fiscal"),
            ("management", "Reporting / Management"),
        ],
        required=True,
        default="statement",
    )
    default_group_by = fields.Selection(
        [
            ("none", "No Grouping"),
            ("section", "Section"),
            ("account", "Account"),
            ("partner", "Partner"),
            ("journal", "Journal"),
            ("month", "Month"),
            ("analytic", "Analytic Account"),
        ],
        default="none",
    )
    hierarchy_guidance = fields.Text(
        default=(
            "Use explicit sections, groups, details, subtotals, totals, and "
            "controls according to the report's professional convention."
        ),
    )
    supports_comparison = fields.Boolean(default=True)
    supports_journals = fields.Boolean(default=True)
    supports_accounts = fields.Boolean(default=True)
    supports_partners = fields.Boolean(default=True)
    supports_analytics = fields.Boolean(default=True)
    supports_pdf = fields.Boolean(default=True)
    supports_xlsx = fields.Boolean(default=True)
    target_action_xmlid = fields.Char(readonly=True)
    technical_key = fields.Char(readonly=True)
    customization_of_id = fields.Many2one(
        "rebuild.account.report.definition",
        readonly=True,
        ondelete="restrict",
    )
    generated_session_count = fields.Integer(
        compute="_compute_generated_session_count",
    )

    @api.depends("report_type", "company_id")
    def _compute_generated_session_count(self):
        Wizard = self.env["rebuild.account.report.export.wizard"]
        for definition in self:
            definition.generated_session_count = Wizard.search_count([
                ("report_definition_id", "=", definition.id),
            ])

    @api.constrains("company_id", "code")
    def _check_unique_scope(self):
        for definition in self:
            domain = [
                ("id", "!=", definition.id),
                ("code", "=", definition.code),
            ]
            domain.append(
                ("company_id", "=", definition.company_id.id)
                if definition.company_id
                else ("company_id", "=", False)
            )
            if self.with_context(active_test=False).search_count(domain):
                raise UserError(
                    "Only one Accounting Report definition is allowed for "
                    "the same code and company scope."
                )

    @api.model
    def _ensure_standard_definitions(self):
        existing = {
            definition.code: definition
            for definition in self.with_context(active_test=False).search([
                ("company_id", "=", False),
            ])
        }
        for report_type, name in ACCOUNTING_REPORT_TYPES:
            if report_type in existing:
                continue
            existing[report_type] = self.with_context(
                accounting_definition_seed=True,
            ).create(_report_seed_values(report_type, name))
        return self.search([("company_id", "=", False)])

    @api.model
    def _resolve(self, report_type, company, on_date=None):
        company.ensure_one()
        candidates = self.with_context(active_test=False).search([
            ("report_type", "=", report_type),
            ("company_id", "in", [False, company.id]),
        ])
        on_date = fields.Date.to_date(on_date or fields.Date.context_today(self))
        candidates = candidates.filtered(
            lambda definition: (
                not definition.effective_from
                or definition.effective_from <= on_date
            ) and (
                not definition.effective_to
                or definition.effective_to >= on_date
            )
        )
        company_definition = candidates.filtered(
            lambda definition: definition.company_id == company,
        )
        definition = (
            company_definition.sorted(
                lambda item: (item.effective_from or fields.Date.from_string("1900-01-01"), item.id),
                reverse=True,
            )[:1]
            or candidates.filtered(
                lambda item: not item.company_id,
            ).sorted(
                lambda item: (item.effective_from or fields.Date.from_string("1900-01-01"), item.id),
                reverse=True,
            )[:1]
        )
        if not definition:
            raise UserError(
                f"No Accounting Report definition is installed for {report_type}."
            )
        if not definition.active or definition.lifecycle != "current":
            raise UserError(
                f"{definition.name} is not active for {company.display_name}."
            )
        return definition

    def action_customize_for_company(self):
        self.ensure_one()
        if self.company_id:
            return {
                "type": "ir.actions.act_window",
                "res_model": self._name,
                "res_id": self.id,
                "view_mode": "form",
                "target": "current",
            }
        existing = self.with_context(active_test=False).search([
            ("code", "=", self.code),
            ("company_id", "=", self.env.company.id),
        ], limit=1)
        if not existing:
            existing = self.copy({
                "company_id": self.env.company.id,
                "origin": "company",
                "definition_version": (
                    f"{self.definition_version}-{self.env.company.id}"
                ),
                "customization_of_id": self.id,
            })
        return {
            "type": "ir.actions.act_window",
            "name": "Company Report Definition",
            "res_model": self._name,
            "res_id": existing.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_report(self):
        self.ensure_one()
        definition = self._resolve(
            self.report_type,
            self.company_id or self.env.company,
        )
        if definition.target_action_xmlid:
            return self.env["ir.actions.actions"]._for_xml_id(
                definition.target_action_xmlid,
            )
        return {
            "type": "ir.actions.client",
            "name": definition.name,
            "tag": "rebuild_accounting_report",
            "context": {"report_type": definition.report_type},
        }

    def write(self, vals):
        protected_business_fields = {
            "active",
            "name",
            "sequence",
            "definition_version",
            "lifecycle",
            "business_purpose",
            "expected_outcome",
            "effective_from",
            "effective_to",
            "family",
            "presentation_style",
            "navigation_group",
            "default_group_by",
            "hierarchy_guidance",
            "supports_comparison",
            "supports_journals",
            "supports_accounts",
            "supports_partners",
            "supports_analytics",
            "supports_pdf",
            "supports_xlsx",
        }
        if (
            protected_business_fields & set(vals)
            and not self.env.context.get("accounting_definition_seed")
            and self.filtered(lambda definition: not definition.company_id)
        ):
            raise UserError(
                "Shared Accounting Report definitions are upgrade-managed. "
                "Use Customize for Company and edit the company definition."
            )
        if protected_business_fields & set(vals):
            vals = {**vals, "origin": "company"}
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if (
                values.get("company_id")
                and not self.env.context.get("accounting_definition_seed")
            ):
                values["origin"] = "company"
        return super().create(vals_list)
