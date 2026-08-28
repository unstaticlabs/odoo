import re

from odoo import api, fields, models
from odoo.exceptions import UserError

ACCOUNTING_REPORT_TYPES = [
    ("trial_balance", "Balance générale"),
    ("general_ledger", "Grand livre"),
    ("journal_report", "Journal comptable"),
    ("partner_ledger", "Grand livre auxiliaire"),
    ("customer_statement", "Relevé client"),
    ("open_items", "Écritures ouvertes"),
    ("aged_receivable", "Balance âgée clients"),
    ("aged_payable", "Balance âgée fournisseurs"),
    ("balance_sheet", "Bilan"),
    ("profit_loss", "Compte de résultat"),
    ("tax_report", "TVA et taxes"),
    ("tax_report_group_account_tax", "Taxes par compte puis taxe"),
    ("tax_report_group_tax_account", "Taxes par taxe puis compte"),
    ("ec_sales_list", "État récapitulatif TVA UE"),
    ("oss_sales", "Ventes OSS"),
    ("oss_imports", "Importations OSS"),
    ("bank_reconciliation", "État de rapprochement bancaire"),
    ("currency_report", "Gains, pertes et exposition de change"),
    ("cash_flow", "Tableau des flux de trésorerie"),
    ("executive_summary", "Synthèse de gestion"),
    ("analytic_report", "Compte de résultat analytique"),
    ("analytic_pivot", "Analyse analytique"),
    ("fixed_assets", "Registre des immobilisations"),
    ("fixed_asset_group_account", "Immobilisations par compte"),
    ("depreciation_schedule", "Plan d’amortissement"),
    ("deferred_schedule", "Charges et produits constatés d’avance"),
    ("french_annual", "États financiers français"),
    ("french_balance_sheet_2024", "Bilan détaillé"),
    (
        "french_profit_loss_2024",
        "Compte de résultat (alias historique)",
    ),
    ("sig_caf_2024", "SIG et CAF"),
    ("french_tax_package", "Liasse fiscale française"),
    ("closing_package", "Dossier de revue de clôture"),
    ("fec", "FEC"),
]

AMOUNT_ROUNDING_SELECTION = [
    ("whole", "Sans décimales"),
    ("cents", "Deux décimales"),
]

WHOLE_EURO_DEFAULT_REPORT_TYPES = {
    "balance_sheet",
    "profit_loss",
    "tax_report",
    "tax_report_group_account_tax",
    "tax_report_group_tax_account",
    "ec_sales_list",
    "oss_sales",
    "oss_imports",
    "cash_flow",
    "executive_summary",
    "analytic_report",
    "french_annual",
    "french_balance_sheet_2024",
    "french_profit_loss_2024",
    "sig_caf_2024",
    "french_tax_package",
    "closing_package",
}

LEGACY_STANDARD_REPORT_NAMES = {
    "trial_balance": "Trial Balance",
    "general_ledger": "General Ledger",
    "journal_report": "Journal Report",
    "partner_ledger": "Partner Ledger",
    "customer_statement": "Customer Statement",
    "open_items": "Open Items",
    "aged_receivable": "Aged Receivable",
    "aged_payable": "Aged Payable",
    "balance_sheet": "Balance Sheet",
    "profit_loss": "Profit and Loss",
    "tax_report": "VAT and Tax Report",
    "tax_report_group_account_tax": "Tax Report by Account then Tax",
    "tax_report_group_tax_account": "Tax Report by Tax then Account",
    "ec_sales_list": "EC Sales List",
    "oss_sales": "OSS Sales",
    "oss_imports": "OSS Imports",
    "bank_reconciliation": "Bank Reconciliation",
    "currency_report": "Currency Gain, Loss and Exposure",
    "cash_flow": "Cash Flow Statement",
    "executive_summary": "Executive Summary",
    "analytic_report": "Analytic Profit and Loss",
    "analytic_pivot": "Analytic Reporting",
    "fixed_assets": "Fixed Asset Register",
    "fixed_asset_group_account": "Fixed Asset Register by Account",
    "depreciation_schedule": "Depreciation Schedule",
    "deferred_schedule": "Deferred Expense and Revenue Schedule",
    "french_balance_sheet_2024": "Bilan détaillé (PCG 2024)",
    "french_profit_loss_2024": "Compte de résultat détaillé (PCG 2024)",
    "sig_caf_2024": "SIG et CAF (PCG 2024)",
    "french_tax_package": "French Tax Package Mapping",
    "closing_package": "Closing Review Package",
}


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
    "french_profit_loss_2024": "rebuild_account_migration.action_rebuild_interactive_profit_loss",
    "sig_caf_2024": "rebuild_account_migration.action_rebuild_interactive_sig_caf_2024",
    "french_tax_package": "rebuild_account_migration.action_rebuild_interactive_french_tax_package",
    "fec": "rebuild_account_migration.action_rebuild_account_report_export_fec",
}


def _report_seed_values(report_type, name):
    french = report_type.startswith("french_") or report_type in {
        "profit_loss",
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
        "journal_report": "journal",
        "partner_ledger": "partner",
        "customer_statement": "partner",
        "open_items": "partner",
        "balance_sheet": "section",
        "profit_loss": "section",
        "executive_summary": "section",
        "french_annual": "section",
        "french_balance_sheet_2024": "section",
        "french_profit_loss_2024": "section",
        "sig_caf_2024": "section",
        "tax_report": "section",
        "tax_report_group_account_tax": "account",
        "tax_report_group_tax_account": "section",
        "ec_sales_list": "section",
        "oss_sales": "section",
        "oss_imports": "section",
        "bank_reconciliation": "journal",
        "currency_report": "section",
        "cash_flow": "section",
        "analytic_report": "analytic",
        "analytic_pivot": "analytic",
        "fixed_assets": "account",
        "fixed_asset_group_account": "account",
        "depreciation_schedule": "section",
        "deferred_schedule": "account",
        "french_tax_package": "section",
        "closing_package": "section",
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
        "definition_version": "saas~19.3.5",
        "active": report_type != "french_profit_loss_2024",
        "lifecycle": (
            "deprecated"
            if report_type == "french_profit_loss_2024"
            else "current"
        ),
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
        "default_amount_rounding": (
            "whole"
            if report_type in WHOLE_EURO_DEFAULT_REPORT_TYPES
            else "cents"
        ),
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
        "document_template": "usl_official",
        "document_primary_color": "#111111",
        "document_section_background_color": "#E9ECEF",
        "document_section_text_color": "#111111",
        "document_muted_color": "#666666",
        "document_footer_label": "Document comptable",
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
    default_amount_rounding = fields.Selection(
        AMOUNT_ROUNDING_SELECTION,
        required=True,
        default="cents",
        string="Arrondi par défaut",
        help=(
            "Précision proposée à l’ouverture du rapport. Elle affecte "
            "uniquement la présentation ; les calculs et les données d’audit "
            "conservent les montants comptables exacts. Avec une unité en "
            "euros, sans décimales signifie un arrondi à l’euro."
        ),
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
    document_template = fields.Selection(
        [
            ("usl_official", "USL official A4"),
            ("neutral_accounting", "Neutral accounting A4"),
        ],
        required=True,
        default="usl_official",
        help=(
            "Shared document presentation used by the interactive statement "
            "and its PDF export."
        ),
    )
    document_primary_color = fields.Char(
        required=True,
        default="#111111",
        help="Primary ink and rule color in hexadecimal notation.",
    )
    document_section_background_color = fields.Char(
        required=True,
        default="#E9ECEF",
        help="Background used for principal financial-statement sections.",
    )
    document_section_text_color = fields.Char(
        required=True,
        default="#111111",
        help="Text color used on principal financial-statement sections.",
    )
    document_muted_color = fields.Char(
        required=True,
        default="#666666",
        help="Secondary text color used for metadata and document context.",
    )
    document_footer_label = fields.Char(
        required=True,
        default="Document comptable",
    )
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
            definition = existing.get(report_type)
            if definition:
                seed_values = _report_seed_values(report_type, name)
                legacy_name = LEGACY_STANDARD_REPORT_NAMES.get(report_type)
                values = {}
                if legacy_name and definition.name == legacy_name:
                    values["name"] = name
                if legacy_name:
                    legacy_seed_values = _report_seed_values(
                        report_type,
                        legacy_name,
                    )
                    if (
                        definition.business_purpose
                        == legacy_seed_values["business_purpose"]
                    ):
                        values["business_purpose"] = seed_values[
                            "business_purpose"
                        ]
                if definition.origin != seed_values["origin"]:
                    values["origin"] = seed_values["origin"]
                if (
                    definition.definition_version
                    != seed_values["definition_version"]
                ):
                    values["definition_version"] = seed_values[
                        "definition_version"
                    ]
                    if (
                        definition.default_group_by == "none"
                        and seed_values["default_group_by"] != "none"
                    ):
                        values["default_group_by"] = seed_values[
                            "default_group_by"
                        ]
                if (
                    definition.default_amount_rounding
                    != seed_values["default_amount_rounding"]
                ):
                    values["default_amount_rounding"] = seed_values[
                        "default_amount_rounding"
                    ]
                if definition.active != seed_values["active"]:
                    values["active"] = seed_values["active"]
                if definition.lifecycle != seed_values["lifecycle"]:
                    values["lifecycle"] = seed_values["lifecycle"]
                if values:
                    definition.with_context(
                        accounting_definition_seed=True,
                    ).write(values)
                continue
            existing[report_type] = self.with_context(
                accounting_definition_seed=True,
            ).create(_report_seed_values(report_type, name))
        return self.with_context(active_test=False).search([
            ("company_id", "=", False),
        ])

    @staticmethod
    def _hex_rgb(value):
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value or ""):
            message = (
                "Document colors must use six-digit hexadecimal notation "
                "such as #111111."
            )
            raise UserError(message)
        return tuple(
            int(value[index:index + 2], 16) / 255
            for index in (1, 3, 5)
        )

    @staticmethod
    def _relative_luminance(rgb):
        def channel(value):
            return (
                value / 12.92
                if value <= 0.04045
                else ((value + 0.055) / 1.055) ** 2.4
            )

        red, green, blue = (channel(value) for value in rgb)
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    @api.constrains(
        "document_primary_color",
        "document_section_background_color",
        "document_section_text_color",
        "document_muted_color",
    )
    def _check_document_colors(self):
        for definition in self:
            primary = self._hex_rgb(definition.document_primary_color)
            background = self._hex_rgb(
                definition.document_section_background_color,
            )
            text = self._hex_rgb(definition.document_section_text_color)
            self._hex_rgb(definition.document_muted_color)
            lighter = max(
                self._relative_luminance(background),
                self._relative_luminance(text),
            )
            darker = min(
                self._relative_luminance(background),
                self._relative_luminance(text),
            )
            if (lighter + 0.05) / (darker + 0.05) < 4.5:
                message = (
                    "Section background and text colors must provide a "
                    "WCAG contrast ratio of at least 4.5:1."
                )
                raise UserError(message)
            # Evaluate the primary color as part of validation so malformed
            # values cannot reach the browser or PDF renderer.
            self._relative_luminance(primary)

    def _definition_snapshot(self):
        values = super()._definition_snapshot()
        values["document"] = {
            "template": self.document_template,
            "primary_color": self.document_primary_color,
            "section_background_color": (
                self.document_section_background_color
            ),
            "section_text_color": self.document_section_text_color,
            "muted_color": self.document_muted_color,
            "footer_label": self.document_footer_label,
        }
        values["default_amount_rounding"] = self.default_amount_rounding
        return values

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
            "default_amount_rounding",
            "hierarchy_guidance",
            "supports_comparison",
            "supports_journals",
            "supports_accounts",
            "supports_partners",
            "supports_analytics",
            "supports_pdf",
            "supports_xlsx",
            "document_template",
            "document_primary_color",
            "document_section_background_color",
            "document_section_text_color",
            "document_muted_color",
            "document_footer_label",
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
        if (
            protected_business_fields & set(vals)
            and not self.env.context.get("accounting_definition_seed")
        ):
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


class RebuildAccountReportAccountPresentation(models.Model):
    """Retain retired report-label records for upgrade compatibility."""

    _name = "rebuild.account.report.account.presentation"
    _description = "Legacy Accounting Report Account Presentation"
    _order = "account_code, report_type, company_id"

    active = fields.Boolean(default=False)
    company_id = fields.Many2one(
        "res.company",
        index=True,
        help=(
            "Leave empty for the shared presentation. A company-specific "
            "record takes precedence."
        ),
    )
    report_type = fields.Selection(
        ACCOUNTING_REPORT_TYPES,
        index=True,
        help=(
            "Leave empty to apply the label to every report containing this "
            "account."
        ),
    )
    account_code = fields.Char(required=True, index=True)
    display_label = fields.Char(required=True, translate=True)
    evidence_note = fields.Text(
        help=(
            "Documents why the report label differs from the configured "
            "account master-data label."
        ),
    )
