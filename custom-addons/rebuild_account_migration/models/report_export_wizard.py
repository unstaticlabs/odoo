import calendar
import csv
import hashlib
import io
import json
import math
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import BinaryBytes

from .report_definition import (
    ACCOUNTING_REPORT_TYPES,
    AMOUNT_ROUNDING_SELECTION,
)

ACCOUNT_CODE_SQL = (
    "COALESCE("
    "account.code_store->>company.id::text, "
    "account.code_store->>'1', "
    "account.code_store::text"
    ")"
)
ACCOUNT_NAME_SQL = "COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text)"

MONETARY_REPORT_FIELDS = {
    "opening_balance",
    "debit",
    "credit",
    "balance",
    "closing_balance",
    "running_balance",
    "movement",
    "amount",
    "gross_amount",
    "depreciation_amount",
    "net_amount",
    "residual",
    "presented_residual",
    "amount_residual",
    "imported_period_net_value",
    "original_value",
    "accumulated_depreciation",
    "accumulated_depreciation_amount",
    "net_book_value_after_line",
    "deferred_account_balance",
    "counterpart_balance",
    "rounded_amount",
    "statement_balance",
    "tax_base_amount",
    "presented_tax_base",
    "presented_tax_amount",
    "period_value",
    "comparison_value",
    "difference",
    "not_due",
    "bucket_1_30",
    "bucket_31_60",
    "bucket_61_90",
    "bucket_over_90",
    "total",
    "allocated_debit",
    "allocated_credit",
    "allocated_balance",
    "amount_currency",
    "amount_residual_currency",
    "taxable_amount",
    "tax_amount",
    "expense_amount",
    "accumulated_depreciation",
    "accumulated_depreciation_amount",
    "net_book_value_after_line",
    "source_book_value",
    "benchmark_amount",
    "ledger_amount",
    "difference_amount",
}

DATE_REPORT_FIELDS = {
    "date",
    "due_date",
    "acquisition_date",
    "depreciation_date",
    "deferred_date",
}

DISPLAY_UNIT_VALUES = {
    "units": {
        "factor": 1,
        "label": "Unités",
        "short_label": "€",
    },
    "thousands": {
        "factor": 1_000,
        "label": "Milliers",
        "short_label": "k€",
    },
    "millions": {
        "factor": 1_000_000,
        "label": "Millions",
        "short_label": "M€",
    },
}

AMOUNT_ROUNDING_VALUES = {
    "whole": {
        "decimal_places": 0,
    },
    "cents": {
        "decimal_places": 2,
    },
}

CANONICAL_REPORT_TYPES = {
    "french_profit_loss_2024": "profit_loss",
}

ZERO_ACCOUNT_FILTER_REPORT_TYPES = {
    "trial_balance",
    "balance_sheet",
    "profit_loss",
    "tax_report",
    "fixed_asset_group_account",
    "french_annual",
    "french_balance_sheet_2024",
    "french_profit_loss_2024",
    "sig_caf_2024",
}

FRENCH_PROFIT_LOSS_SECTIONS = {
    "CR_VENTES_PRODUITS": "Produits d’exploitation",
    "CR_SERVICES": "Produits d’exploitation",
    "CR_CHIFFRE_AFFAIRES": "Produits d’exploitation",
    "CR_AUTRES_PRODUITS_EXPLOITATION": "Produits d’exploitation",
    "CR_TOTAL_PRODUITS_EXPLOITATION": "Produits d’exploitation",
    "CR_ACHATS_MARCHANDISES": "Charges d’exploitation",
    "CR_CHARGES_EXTERNES": "Charges d’exploitation",
    "CR_IMPOTS_TAXES": "Charges d’exploitation",
    "CR_SALAIRES": "Charges d’exploitation",
    "CR_CHARGES_SOCIALES": "Charges d’exploitation",
    "CR_DOTATIONS_AMORTISSEMENTS": "Charges d’exploitation",
    "CR_AUTRES_CHARGES_EXPLOITATION": "Charges d’exploitation",
    "CR_TOTAL_CHARGES_EXPLOITATION": "Charges d’exploitation",
    "CR_RESULTAT_EXPLOITATION": "Résultat d’exploitation",
    "CR_PRODUITS_FINANCIERS": "Résultat financier",
    "CR_CHARGES_FINANCIERES": "Résultat financier",
    "CR_RESULTAT_FINANCIER": "Résultat financier",
    "CR_RESULTAT_COURANT_AVANT_IMPOT": "Résultat courant et exceptionnel",
    "CR_RESULTAT_EXCEPTIONNEL": "Résultat courant et exceptionnel",
    "CR_IMPOTS_BENEFICES": "Résultat de l’exercice",
    "CR_TOTAL_PRODUITS": "Résultat de l’exercice",
    "CR_TOTAL_CHARGES": "Résultat de l’exercice",
    "CR_RESULTAT_NET": "Résultat de l’exercice",
}

FRENCH_PROFIT_LOSS_SECTION_TOTALS = {
    "Produits d’exploitation": "CR_TOTAL_PRODUITS_EXPLOITATION",
    "Charges d’exploitation": "CR_TOTAL_CHARGES_EXPLOITATION",
    "Résultat financier": "CR_RESULTAT_FINANCIER",
    "Résultat d’exploitation": "CR_RESULTAT_EXPLOITATION",
    "Résultat courant et exceptionnel": "CR_RESULTAT_EXCEPTIONNEL",
    "Résultat de l’exercice": "CR_RESULTAT_NET",
}

FRENCH_PROFIT_LOSS_SUBTOTALS = {
    *FRENCH_PROFIT_LOSS_SECTION_TOTALS.values(),
    "CR_RESULTAT_EXPLOITATION",
    "CR_RESULTAT_COURANT_AVANT_IMPOT",
    "CR_RESULTAT_EXCEPTIONNEL",
    "CR_TOTAL_PRODUITS",
    "CR_TOTAL_CHARGES",
}

HIERARCHY_STATE_SENTINEL = "__pcg_hierarchy_initialized__"

# These reports contain comparable summary rows which can be added across
# companies sharing the same company currency. Detail-ledger reports keep one
# row per company so their source identity and running balances stay exact.
MULTI_COMPANY_AGGREGATE_KEYS = {
    "trial_balance": ("account_code", "account_type"),
    "journal_report": ("journal_code", "journal_type"),
    "balance_sheet": ("section", "account_code", "account_type"),
    "profit_loss": ("statement_key", "line_code", "section"),
    "cash_flow": ("line_code", "section", "label"),
    "executive_summary": ("line_code", "section", "label"),
    "fixed_asset_group_account": ("account_code",),
    "french_annual": ("statement_key", "line_code", "section"),
    "french_balance_sheet_2024": (
        "statement_key",
        "line_code",
        "section",
    ),
    "sig_caf_2024": ("statement_key", "line_code", "section"),
}


def _amount(value):
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


def _amount_text(value):
    return f"{_amount(value):.2f}"


def _fec_amount(value):
    return Decimal((value or "0").strip().replace(" ", "").replace(",", ".") or "0").quantize(Decimal("0.01"))


def _matches(row, prefixes):
    return any((row.get("account_code") or "").startswith(prefix) for prefix in prefixes)


class RebuildAccountReportExportWizard(models.TransientModel):
    _name = "rebuild.account.report.export.wizard"
    _description = "USL Dynamic Accounting Report Workbench"

    report_type = fields.Selection(
        ACCOUNTING_REPORT_TYPES,
        required=True,
        default="trial_balance",
    )
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    closing_period_id = fields.Many2one(
        "rebuild.account.closing.period",
        string="Closing Workspace",
        readonly=True,
    )
    company_ids = fields.Many2many(
        "res.company",
        string="Companies",
        default=lambda self: self.env.company,
    )
    period_preset = fields.Selection(
        [
            ("custom", "Custom Dates"),
            ("month", "Month"),
            ("quarter", "Quarter"),
            ("fiscal_year", "Fiscal Year"),
            ("year_to_date", "Fiscal Year to Date"),
        ],
        required=True,
        default="custom",
    )
    period_anchor_date = fields.Date(
        string="Period Containing",
        default=fields.Date.context_today,
    )
    date_from = fields.Date(
        required=True,
        default=lambda self: date(fields.Date.context_today(self).year, 1, 1),
    )
    date_to = fields.Date(required=True, default=fields.Date.context_today)
    comparison_mode = fields.Selection(
        [
            ("none", "No Comparison"),
            ("previous_period", "Previous Period"),
            ("previous_year", "Same Period Last Year"),
            ("custom", "Custom Comparison"),
        ],
        required=True,
        default="none",
    )
    comparison_date_from = fields.Date()
    comparison_date_to = fields.Date()
    target_move = fields.Selection(
        [
            ("posted", "Posted Entries Only"),
            ("all", "Posted and Draft Entries"),
        ],
        required=True,
        default="posted",
    )
    display_unit = fields.Selection(
        [
            ("units", "Unités"),
            ("thousands", "Milliers"),
            ("millions", "Millions"),
        ],
        required=True,
        default="units",
        string="Unité d’affichage",
    )
    amount_rounding = fields.Selection(
        AMOUNT_ROUNDING_SELECTION,
        required=True,
        default="cents",
        string="Arrondi",
        help=(
            "Arrondit uniquement les montants présentés à l’écran et dans "
            "les exports. Les calculs et la feuille Audit Data du XLSX "
            "conservent les montants comptables exacts."
        ),
    )
    export_format = fields.Selection(
        [
            ("csv", "CSV"),
            ("xlsx", "XLSX"),
            ("pdf", "PDF"),
            ("txt", "FEC TXT"),
        ],
        required=True,
        default="csv",
    )
    fec_test_mode = fields.Boolean(string="FEC Test Mode", default=True)
    can_generate_official_fec = fields.Boolean(
        compute="_compute_can_generate_official_fec",
    )
    journal_ids = fields.Many2many("account.journal", string="Journals")
    account_ids = fields.Many2many("account.account", string="Accounts")
    partner_ids = fields.Many2many("res.partner", string="Partners")
    analytic_plan_ids = fields.Many2many(
        "account.analytic.plan",
        "rebuild_report_analytic_plan_rel",
        "wizard_id",
        "plan_id",
        string="Analytic Plans",
    )
    analytic_account_ids = fields.Many2many(
        "account.analytic.account",
        "rebuild_report_analytic_account_rel",
        "wizard_id",
        "analytic_account_id",
        string="Analytic Accounts",
    )
    group_by = fields.Selection(
        [
            ("none", "No Grouping"),
            ("section", "Section"),
            ("account", "Account"),
            ("partner", "Partner"),
            ("journal", "Journal"),
            ("month", "Month"),
            ("analytic", "Analytic Account"),
        ],
        required=True,
        default="none",
    )
    search_text = fields.Char(string="Search Report")
    hide_zero_accounts = fields.Boolean(
        string="Masquer les lignes à zéro",
        help=(
            "Masque les lignes de détail et les comptes dont toutes les "
            "valeurs monétaires affichées sont nulles. Les comptes ayant une "
            "activité débit ou crédit restent visibles même si leur solde "
            "est nul."
        ),
    )
    show_details = fields.Boolean(default=True)
    collapsed_group_keys = fields.Text(default="[]")
    export_file = fields.Binary(readonly=True, attachment=True)
    export_filename = fields.Char(readonly=True)
    export_metadata = fields.Text(readonly=True)
    preview_limit = fields.Integer(default=500, required=True)
    preview_line_ids = fields.One2many(
        "rebuild.account.report.preview.line",
        "wizard_id",
        readonly=True,
    )
    preview_row_count = fields.Integer(readonly=True)
    preview_truncated = fields.Boolean(readonly=True)
    preview_generated_at = fields.Datetime(readonly=True)
    preview_metadata = fields.Text(readonly=True)
    draft_entry_count = fields.Integer(readonly=True)
    preview_warning = fields.Text(readonly=True)
    report_definition_id = fields.Many2one(
        "rebuild.account.report.definition",
        readonly=True,
        ondelete="restrict",
    )
    report_definition_version = fields.Char(readonly=True)
    report_definition_snapshot = fields.Json(readonly=True)

    def _can_generate_official_fec(self):
        return self.env.user.has_group("account.group_account_manager")

    @api.depends_context("uid")
    def _compute_can_generate_official_fec(self):
        allowed = self._can_generate_official_fec()
        for wizard in self:
            wizard.can_generate_official_fec = allowed

    @api.model
    def report_client_load(
        self,
        report_type,
        filters=None,
        wizard_id=None,
    ):
        """Return a user-facing interactive report payload.

        The transient model remains the calculation/export engine. This API
        keeps its implementation details out of the normal Accounting UI.
        """
        filters = filters or {}
        report_type = CANONICAL_REPORT_TYPES.get(
            report_type,
            report_type,
        )
        wizard = self.browse(wizard_id).exists() if wizard_id else self.browse()
        if wizard and wizard.report_type in CANONICAL_REPORT_TYPES:
            wizard.report_type = CANONICAL_REPORT_TYPES[
                wizard.report_type
            ]
        allowed_company_ids = set(self.env.companies.ids)
        requested_company_id = int(filters.get("company_id") or 0)
        if (
            requested_company_id
            and requested_company_id not in allowed_company_ids
        ):
            message = (
                "You cannot report on a company outside your allowed "
                "companies."
            )
            raise AccessError(message)
        requested_scope_ids = [
            int(company_id)
            for company_id in (filters.get("company_ids") or [])
        ]
        if requested_scope_ids:
            if not set(requested_scope_ids) <= allowed_company_ids:
                message = (
                    "You cannot report on a company outside your allowed "
                    "companies."
                )
                raise AccessError(message)
            if requested_company_id not in requested_scope_ids:
                filters = {
                    **filters,
                    "company_id": requested_scope_ids[0],
                }
                requested_company_id = requested_scope_ids[0]
        requested_company = (
            self.env["res.company"].browse(requested_company_id).exists()
            or wizard.company_id
            or self.env.company
        )
        definition = self.env[
            "rebuild.account.report.definition"
        ]._resolve(
            report_type,
            requested_company,
            filters.get("date_to") or wizard.date_to,
        )
        if not wizard:
            today = fields.Date.context_today(self)
            fiscal_from, fiscal_to = (
                requested_company.rebuild_compute_fiscalyear_dates(today)
            )
            default_group = definition.default_group_by
            initial_company_ids = requested_scope_ids or self.env.companies.ids
            wizard = self.create({
                "report_type": report_type,
                "company_id": requested_company.id,
                "company_ids": [Command.set(initial_company_ids)],
                "period_preset": "fiscal_year",
                "period_anchor_date": today,
                "date_from": fiscal_from,
                "date_to": fiscal_to,
                "target_move": "posted",
                "comparison_mode": "none",
                "group_by": default_group,
                "display_unit": "units",
                "amount_rounding": definition.default_amount_rounding,
                "preview_limit": 1000,
                "export_format": "xlsx",
                "report_definition_id": definition.id,
                "report_definition_version": definition.definition_version,
                "report_definition_snapshot": definition._definition_snapshot(),
            })
        elif wizard.report_type != report_type:
            raise UserError("This report session belongs to another report.")
        elif wizard.report_definition_id != definition:
            wizard.write({
                "report_definition_id": definition.id,
                "report_definition_version": definition.definition_version,
                "report_definition_snapshot": definition._definition_snapshot(),
            })

        allowed_filter_fields = {
            "company_id",
            "company_ids",
            "date_from",
            "date_to",
            "period_preset",
            "period_anchor_date",
            "target_move",
            "comparison_mode",
            "comparison_date_from",
            "comparison_date_to",
            "group_by",
            "display_unit",
            "amount_rounding",
            "hide_zero_accounts",
            "search_text",
            "journal_ids",
            "account_ids",
            "partner_ids",
            "analytic_plan_ids",
            "analytic_account_ids",
        }
        values = {
            key: value
            for key, value in filters.items()
            if key in allowed_filter_fields
        }
        if not definition.supports_comparison:
            values.update({
                "comparison_mode": "none",
                "comparison_date_from": False,
                "comparison_date_to": False,
            })
        for supported, field_name in (
            (definition.supports_journals, "journal_ids"),
            (definition.supports_accounts, "account_ids"),
            (definition.supports_partners, "partner_ids"),
        ):
            if not supported:
                values[field_name] = []
        if not definition.supports_analytics:
            values.update({
                "analytic_plan_ids": [],
                "analytic_account_ids": [],
            })
        if "company_ids" in values:
            requested_company_ids = [
                int(company_id)
                for company_id in (values.pop("company_ids") or [])
            ]
            allowed_company_ids = set(self.env.companies.ids)
            if not requested_company_ids:
                message = "Select at least one company."
                raise UserError(message)
            if not set(requested_company_ids) <= allowed_company_ids:
                message = (
                    "You cannot report on a company outside your allowed "
                    "companies."
                )
                raise AccessError(message)
            values["company_ids"] = [Command.set(requested_company_ids)]
            if int(values.get("company_id") or 0) not in requested_company_ids:
                values["company_id"] = requested_company_ids[0]
        elif values.get("company_id"):
            requested_company_id = int(values["company_id"])
            if requested_company_id not in self.env.companies.ids:
                message = (
                    "You cannot report on a company outside your allowed "
                    "companies."
                )
                raise AccessError(message)
            values["company_ids"] = [Command.set([requested_company_id])]
        for field_name in {
            "journal_ids",
            "account_ids",
            "partner_ids",
            "analytic_plan_ids",
            "analytic_account_ids",
        } & values.keys():
            values[field_name] = [
                Command.set([int(record_id) for record_id in values[field_name]]),
            ]
        if values.get("comparison_mode") == "custom":
            current_from = fields.Date.to_date(
                values.get("date_from") or wizard.date_from,
            )
            current_to = fields.Date.to_date(
                values.get("date_to") or wizard.date_to,
            )
            values["comparison_date_from"] = (
                values.get("comparison_date_from")
                or wizard._previous_year_date(current_from)
            )
            values["comparison_date_to"] = (
                values.get("comparison_date_to")
                or wizard._previous_year_date(current_to)
            )
        if values:
            wizard.write(values)
            if {
                "period_preset",
                "period_anchor_date",
            } & values.keys():
                wizard._apply_period_values()
        wizard.action_preview_report()
        return wizard._report_client_payload()

    def _report_client_payload(self):
        self.ensure_one()

        def selection_options(field_name):
            field = self._fields[field_name]
            selection = (
                field.selection(self)
                if callable(field.selection)
                else field.selection
            )
            return [
                {"value": value, "label": label}
                for value, label in selection
            ]

        columns = self._report_client_columns()
        lines = []
        collapsed_groups = self._collapsed_group_key_set()
        for line in self.preview_line_ids.sorted("sequence"):
            row = line._row_payload()
            display_label = line.label or ""
            if self.report_type in {"aged_receivable", "aged_payable"}:
                display_label = row.get("partner_name") or "No partner"
            if (
                self.report_type in {
                    "general_ledger",
                    "partner_ledger",
                    "customer_statement",
                }
                and not line.is_group
                and display_label.strip() in {"", "/"}
            ):
                display_label = (
                    row.get("move_ref")
                    or row.get("partner_name")
                    or "Journal item"
                )
            row_currency = row.get("currency") or ""
            if row_currency == self.company_id.currency_id.name:
                row_currency = ""
            presentation_role = self._report_presentation_role(
                row,
                is_group=line.is_group,
                level=line.level,
            )
            lines.append({
                "id": line.id,
                "date": fields.Date.to_string(line.date) if line.date else "",
                "section": line.section or "",
                "label": display_label,
                "account_code": line.account_code or "",
                "account_name": line.account_name or "",
                "partner_name": line.partner_name or "",
                "move_name": line.move_name or "",
                "opening_balance": line.opening_balance,
                "debit": line.debit,
                "credit": line.credit,
                "movement": line.movement,
                "closing_balance": line.closing_balance,
                "balance": line.balance,
                "residual": line.residual,
                "comparison_value": line.comparison_value,
                "difference": line.difference,
                "record_count": line.record_count,
                "is_group": line.is_group,
                "level": line.level,
                "presentation_role": presentation_role,
                "group_key": line.group_key or "",
                "collapsed": bool(
                    line.is_group
                    and line.group_key in collapsed_groups
                ),
                "journal_code": row.get("journal_code") or "",
                "move_ref": row.get("move_ref") or "",
                "currency": row_currency,
                "amount_currency": _amount(row.get("amount_currency")),
                "running_balance": _amount(
                    row.get("running_balance")
                    or row.get("closing_balance"),
                ),
                "matching_number": row.get("matching_number") or "",
                "payment_status": row.get("payment_status") or "",
                "due_date": row.get("due_date") or "",
                "can_drilldown": row.get("empty_report") != "true",
                "company_contributions": row.get("company_contributions") or [],
                "company_name": row.get("report_company_name") or "",
                "values": {
                    column["key"]: row.get(column["key"])
                    for column in columns
                },
            })
        return {
            "wizard_id": self.id,
            "title": self._report_type_label(),
            "company_id": self.company_id.id,
            "company_name": ", ".join(
                self._selected_companies().mapped("display_name"),
            ),
            "company_ids": self._selected_companies().ids,
            "multi_company": len(self._selected_companies()) > 1,
            "aggregation_mode": (
                "aggregate"
                if len(self._selected_companies()) > 1
                and self.report_type in MULTI_COMPANY_AGGREGATE_KEYS
                else "company_rows"
            ),
            # Accounting statements follow the French presentation contract
            # independently from the user's general Odoo interface language.
            "locale": "fr-FR",
            "definition": {
                "id": self.report_definition_id.id,
                "code": self.report_definition_id.code,
                "version": self.report_definition_version,
                "origin": self.report_definition_id.origin,
                "company_id": self.report_definition_id.company_id.id,
                "business_purpose": (
                    self.report_definition_id.business_purpose or ""
                ),
            },
            "variant": {
                "key": self._report_variant_key() or "standard",
                "label": self._report_variant_label(),
                "basis": self._report_variant_basis(),
            },
            "document": self._document_theme(),
            "label_column": self._report_client_label_column(),
            "report_type": self.report_type,
            "currency": {
                "id": self.company_id.currency_id.id,
                "name": self.company_id.currency_id.name,
                "symbol": self.company_id.currency_id.symbol,
                "position": self.company_id.currency_id.position,
            },
            "display_unit": self._display_unit_metadata(),
            "amount_rounding": self._amount_rounding_metadata(),
            "filters": {
                "company_id": self.company_id.id,
                "company_ids": self._selected_companies().ids,
                "date_from": fields.Date.to_string(self.date_from),
                "date_to": fields.Date.to_string(self.date_to),
                "period_preset": self.period_preset,
                "period_anchor_date": fields.Date.to_string(
                    self.period_anchor_date,
                ),
                "target_move": self.target_move,
                "comparison_mode": self.comparison_mode,
                "comparison_date_from": (
                    fields.Date.to_string(self.comparison_date_from)
                    if self.comparison_date_from else ""
                ),
                "comparison_date_to": (
                    fields.Date.to_string(self.comparison_date_to)
                    if self.comparison_date_to else ""
                ),
                "group_by": self.group_by,
                "display_unit": self.display_unit,
                "amount_rounding": self.amount_rounding,
                "hide_zero_accounts": self.hide_zero_accounts,
                "search_text": self.search_text or "",
                "journal_ids": self.journal_ids.ids,
                "account_ids": self.account_ids.ids,
                "partner_ids": self.partner_ids.ids,
                "analytic_plan_ids": self.analytic_plan_ids.ids,
                "analytic_account_ids": self.analytic_account_ids.ids,
            },
            "options": {
                "companies": [
                    {"value": company.id, "label": company.display_name}
                    for company in self.env.companies
                ],
                "period_preset": selection_options("period_preset"),
                "target_move": selection_options("target_move"),
                "comparison_mode": selection_options("comparison_mode"),
                "group_by": selection_options("group_by"),
                "display_unit": selection_options("display_unit"),
                "amount_rounding": selection_options("amount_rounding"),
                "journals": [
                    {
                        "value": journal.id,
                        "label": f"{journal.code} — {journal.name}",
                    }
                    for journal in self.env["account.journal"].search([
                        ("company_id", "=", self.company_id.id),
                    ], order="code, name")
                ],
                "accounts": [
                    {
                        "value": account.id,
                        "label": (
                            f"{account.code} — {account.name}"
                            if account.code else account.name
                        ),
                    }
                    for account in self.env["account.account"].search([
                        ("company_ids", "in", self.company_id.id),
                    ], order="code")
                ],
                "partners": [
                    {"value": partner.id, "label": partner.display_name}
                    for partner in self.env["res.partner"].search([
                        "|",
                        ("company_id", "=", False),
                        ("company_id", "=", self.company_id.id),
                        "|",
                        ("customer_rank", ">", 0),
                        ("supplier_rank", ">", 0),
                    ], order="name")
                ],
                "analytic_plans": [
                    {"value": plan.id, "label": plan.display_name}
                    for plan in self.env["account.analytic.plan"].search(
                        [],
                        order="name",
                    )
                ],
                "analytic_accounts": [
                    {
                        "value": account.id,
                        "label": (
                            f"{account.plan_id.name} — {account.display_name}"
                            if account.plan_id else account.display_name
                        ),
                    }
                    for account in self.env["account.analytic.account"].search([
                        "|",
                        ("company_id", "=", False),
                        ("company_id", "=", self.company_id.id),
                    ], order="plan_id, name")
                ],
            },
            "lines": lines,
            "columns": columns,
            "row_count": self.preview_row_count,
            "truncated": self.preview_truncated,
            "warning": self.preview_warning or "",
            "draft_entry_count": self.draft_entry_count,
            "capabilities": self._report_client_capabilities(),
            "summary": self._report_client_summary(),
            "generated_at": fields.Datetime.to_string(
                self.preview_generated_at,
            ),
        }

    def _report_client_label_column(self):
        self.ensure_one()
        return {
            "trial_balance": "Compte",
            "general_ledger": "Libellé",
            "journal_report": "Journal",
            "partner_ledger": "Partenaire / écriture",
            "customer_statement": "Client / écriture",
            "open_items": "Pièce ouverte",
            "aged_receivable": "Client",
            "aged_payable": "Fournisseur",
            "balance_sheet": "Poste du bilan",
            "profit_loss": "Poste du compte de résultat",
            "tax_report": "Rubrique fiscale",
            "analytic_report": "Dimension analytique",
            "fixed_assets": "Immobilisation",
            "depreciation_schedule": "Échéance d'amortissement",
            "french_annual": "Rubrique",
            "french_balance_sheet_2024": "Rubrique du bilan",
            "french_profit_loss_2024": "Rubrique du compte de résultat",
            "sig_caf_2024": "Solde intermédiaire",
        }.get(self.report_type, "Rubrique")

    def _report_presentation_role(self, row, *, is_group=None, level=None):
        """Return one stable hierarchy role shared by screen and exports."""
        self.ensure_one()
        explicit = row.get("presentation_role")
        if explicit in {
            "section",
            "group",
            "detail",
            "subtotal",
            "total",
            "control",
            "empty",
        }:
            return explicit
        if row.get("empty_report") == "true":
            return "empty"
        grouped = (
            row.get("is_group") in (True, "true")
            if is_group is None
            else is_group
        )
        row_level = (
            int(row.get("row_level") or 0)
            if level is None
            else level
        )
        if grouped:
            return (
                "section"
                if self.group_by == "section" and row_level == 0
                else "group"
            )
        code = str(
            row.get("line_code")
            or row.get("field_code")
            or row.get("account_code")
            or "",
        ).upper()
        label = str(
            row.get("label")
            or row.get("line_name")
            or row.get("field_label")
            or row.get("account_name")
            or "",
        ).strip().upper()
        final_codes = {
            "RESULT",
            "ACTIF_TOTAL",
            "PASSIF_TOTAL",
            "CR_RESULTAT_NET",
            "SIG_RESULTAT_NET",
            "SIG_CAPACITE_AUTOFINANCEMENT",
            "CLOSE_STATUS",
        }
        if code in final_codes:
            return "total"
        if (
            "CONTROL" in code
            or "CONTROLE" in code
            or "ÉCART" in label
            or "ECART" in label
        ):
            return "control"
        if (
            code.endswith("_TOTAL")
            or code.startswith("TOTAL_")
            or label.startswith(("TOTAL ", "SOUS-TOTAL "))
        ):
            return "subtotal"
        return "detail"

    def _report_client_capabilities(self):
        """Describe only controls that make sense for this report."""
        self.ensure_one()
        asset_reports = {
            "fixed_assets",
            "fixed_asset_group_account",
            "depreciation_schedule",
        }
        analytic_reports = {
            "trial_balance",
            "general_ledger",
            "journal_report",
            "balance_sheet",
            "profit_loss",
            "tax_report",
            "analytic_report",
        }
        comparison_reports = {
            "trial_balance",
            "balance_sheet",
            "profit_loss",
            "french_annual",
            "french_balance_sheet_2024",
            "french_profit_loss_2024",
            "sig_caf_2024",
            "analytic_report",
            "cash_flow",
            "executive_summary",
        }
        capabilities = {
            "period_presets": True,
            "display_unit": True,
            "amount_rounding": True,
            "comparison": self.report_type in comparison_reports,
            "group_by": self.report_type not in {
                "aged_receivable",
                "aged_payable",
                "french_tax_package",
            },
            "journals": self.report_type not in asset_reports | {
                "french_tax_package",
            },
            "accounts": self.report_type != "bank_reconciliation",
            "partners": self.report_type not in asset_reports | {
                "french_tax_package",
            },
            "analytics": self.report_type in analytic_reports,
            "hide_zero_accounts": (
                self.report_type in ZERO_ACCOUNT_FILTER_REPORT_TYPES
                or self.group_by == "account"
            ),
        }
        definition = self.report_definition_id
        if definition:
            capabilities.update({
                "comparison": (
                    capabilities["comparison"]
                    and definition.supports_comparison
                ),
                "journals": (
                    capabilities["journals"]
                    and definition.supports_journals
                ),
                "accounts": (
                    capabilities["accounts"]
                    and definition.supports_accounts
                ),
                "partners": (
                    capabilities["partners"]
                    and definition.supports_partners
                ),
                "analytics": (
                    capabilities["analytics"]
                    and definition.supports_analytics
                ),
                "pdf": definition.supports_pdf,
                "xlsx": definition.supports_xlsx,
            })
        return capabilities

    def _report_client_summary(self):
        """Return compact statement controls, not technical calculation logs."""
        self.ensure_one()
        collapsed_groups = self._collapsed_group_key_set()
        rows = [
            line._row_payload()
            for line in self.preview_line_ids
            if (
                not line.is_group
                or line.group_key in collapsed_groups
            )
        ]

        def amount_for(code, key="amount"):
            row = next(
                (
                    candidate
                    for candidate in rows
                    if candidate.get("line_code") == code
                ),
                {},
            )
            return float(_amount(row.get(key)))

        def total_for(key):
            return float(sum(_amount(row.get(key)) for row in rows))

        if self.report_type == "trial_balance":
            debit = total_for("debit")
            credit = total_for("credit")
            difference = round(debit - credit, 2)
            return {
                "cards": [
                    {"label": "Total débit", "value": debit, "type": "currency"},
                    {"label": "Total crédit", "value": credit, "type": "currency"},
                    {
                        "label": "Contrôle d'équilibre",
                        "value": difference,
                        "type": "currency",
                        "status": (
                            "success"
                            if abs(difference) < 0.01
                            else "danger"
                        ),
                    },
                ],
            }
        if self.report_type in {"general_ledger", "journal_report"}:
            debit = total_for("debit")
            credit = total_for("credit")
            return {
                "cards": [
                    {"label": "Mouvements débit", "value": debit, "type": "currency"},
                    {"label": "Mouvements crédit", "value": credit, "type": "currency"},
                    {
                        "label": "Variation nette",
                        "value": debit - credit,
                        "type": "currency",
                    },
                ],
            }
        if self.report_type in {"aged_receivable", "aged_payable"}:
            return {
                "cards": [{
                    "label": (
                        "Créances ouvertes"
                        if self.report_type == "aged_receivable"
                        else "Dettes ouvertes"
                    ),
                    "value": total_for("total"),
                    "type": "currency",
                }],
            }
        if self.report_type == "balance_sheet":
            asset_sections = {
                "Immobilisations",
                "Actif circulant",
                # Preserve old report sessions created before the French
                # presentation labels were introduced.
                "Fixed assets",
                "Current assets",
            }

            def is_asset(row):
                account_type = str(row.get("account_type") or "")
                return (
                    account_type.startswith("asset")
                    or str(row.get("section") or "") in asset_sections
                )

            assets = float(sum(
                _amount(row.get("amount"))
                for row in rows
                if is_asset(row)
            ))
            liabilities = float(sum(
                _amount(row.get("amount"))
                for row in rows
                if not is_asset(row)
            ))
            difference = round(assets - liabilities, 2)
            return {
                "cards": [
                    {"label": "Total actif", "value": assets, "type": "currency"},
                    {
                        "label": "Capitaux propres et passif",
                        "value": liabilities,
                        "type": "currency",
                    },
                    {
                        "label": "Contrôle d'équilibre",
                        "value": difference,
                        "type": "currency",
                        "status": (
                            "success"
                            if abs(difference) < 0.01
                            else "danger"
                        ),
                    },
                ],
            }

        if self.report_type in {
            "french_annual",
            "french_balance_sheet_2024",
        }:
            assets = amount_for("ACTIF_TOTAL", "net_amount")
            liabilities = amount_for("PASSIF_TOTAL")
            difference = round(assets - liabilities, 2)
            return {
                "cards": [
                    {"label": "Total actif", "value": assets, "type": "currency"},
                    {
                        "label": "Total capitaux propres et passif",
                        "value": liabilities,
                        "type": "currency",
                    },
                    {
                        "label": "Contrôle d'équilibre",
                        "value": difference,
                        "type": "currency",
                        "status": "success" if abs(difference) < 0.01 else "danger",
                    },
                ],
            }
        if self.report_type in {
            "profit_loss",
            "french_profit_loss_2024",
            "sig_caf_2024",
        }:
            result_code = (
                "SIG_RESULTAT_NET"
                if self.report_type == "sig_caf_2024"
                else "CR_RESULTAT_NET"
            )
            result = amount_for(result_code)
            return {
                "cards": [{
                    "label": "Résultat net de l’exercice",
                    "value": result,
                    "type": "currency",
                    "status": "success" if result >= 0 else "warning",
                }],
            }
        return {"cards": []}

    def _report_client_columns(self):
        self.ensure_one()
        currency = "currency"
        number = "number"
        date = "date"
        text = "text"
        column_map = {
            "journal_report": [
                ("move_count", "Écritures", number),
                ("move_line_count", "Lignes comptables", number),
                ("debit", "Débit", currency),
                ("credit", "Crédit", currency),
                ("balance", "Solde", currency),
            ],
            "open_items": [
                ("date", "Date", date),
                ("due_date", "Échéance", date),
                ("move_name", "Écriture", text),
                ("presented_residual", "Résiduel", currency),
                ("matching_number", "Lettrage", text),
            ],
            "aged_receivable": [
                ("not_due", "Non échu", currency),
                ("bucket_1_30", "1–30 jours", currency),
                ("bucket_31_60", "31–60 jours", currency),
                ("bucket_61_90", "61–90 jours", currency),
                ("bucket_over_90", "Plus de 90 jours", currency),
                ("total", "Total", currency),
            ],
            "aged_payable": [
                ("not_due", "Non échu", currency),
                ("bucket_1_30", "1–30 jours", currency),
                ("bucket_31_60", "31–60 jours", currency),
                ("bucket_61_90", "61–90 jours", currency),
                ("bucket_over_90", "Plus de 90 jours", currency),
                ("total", "Total", currency),
            ],
            "balance_sheet": [("amount", "Solde", currency)],
            "profit_loss": [("amount", "Montant", currency)],
            "tax_report": [
                ("tax_name", "Ligne de taxe", text),
                ("presented_tax_base", "Base taxable", currency),
                ("presented_tax_amount", "Montant de taxe", currency),
                ("balance", "Solde comptable", currency),
            ],
            "bank_reconciliation": [
                ("date", "Date", date),
                ("journal_code", "Journal", text),
                ("amount", "Montant", currency),
                ("amount_residual", "Résiduel", currency),
                ("reconciliation_status", "Statut", text),
            ],
            "currency_report": [
                ("currency", "Devise", text),
                ("amount_currency", "Montant d’origine", number),
                ("balance", "Contre-valeur société", currency),
                ("amount_residual", "Résiduel", currency),
            ],
            "cash_flow": [
                ("amount", "Montant", currency),
                ("statement_balance", "Solde de trésorerie", currency),
            ],
            "executive_summary": [
                ("metric_value", "Valeur", number),
                ("unit", "Unité", text),
                ("details", "Définition", text),
            ],
            "analytic_report": [
                ("allocated_debit", "Produits", currency),
                ("allocated_credit", "Charges", currency),
                ("allocated_balance", "Contribution nette", currency),
                ("move_line_count", "Écritures", number),
            ],
            "fixed_assets": [
                ("acquisition_date", "Acquisition", date),
                ("original_value", "Valeur d’origine", currency),
                (
                    "accumulated_depreciation",
                    "Amortissements cumulés",
                    currency,
                ),
                (
                    "imported_period_net_value",
                    "Valeur nette comptable",
                    currency,
                ),
                ("state", "Statut", text),
            ],
            "depreciation_schedule": [
                ("depreciation_date", "Date", date),
                ("depreciation_amount", "Dotation", currency),
                (
                    "accumulated_depreciation_amount",
                    "Amortissements cumulés",
                    currency,
                ),
                ("net_book_value_after_line", "Valeur nette comptable", currency),
                ("representation_status", "Statut", text),
            ],
            "deferred_schedule": [
                ("deferred_date", "Date", date),
                ("deferred_account_code", "Compte de régularisation", text),
                ("amount", "Montant", currency),
                ("review_status", "Statut", text),
            ],
            "french_annual": [
                ("gross_amount", "Brut", currency),
                ("depreciation_amount", "Amortissements / provisions", currency),
                ("net_amount", "Net / montant", currency),
            ],
            "french_balance_sheet_2024": [
                ("gross_amount", "Brut", currency),
                ("depreciation_amount", "Amortissements / provisions", currency),
                ("net_amount", "Net", currency),
            ],
            "french_profit_loss_2024": [
                ("amount", "Montant", currency),
            ],
            "sig_caf_2024": [("amount", "Montant", currency)],
            "french_tax_package": [
                ("quantity", "Quantité", number),
                ("amount", "Montant", currency),
                ("rounded_amount", "Montant arrondi", currency),
                ("value_text", "Valeur / note", text),
                ("review_status", "Statut de revue", text),
            ],
        }
        columns = column_map.get(
            self.report_type,
            [("balance", "Balance", currency)],
        )
        if self.comparison_mode != "none":
            columns = [
                *columns,
                ("comparison_value", "Comparaison", currency),
                ("difference", "Écart", currency),
            ]
        unit_label = self._display_unit_metadata()["short_label"]
        return [
            {
                "key": key,
                "label": (
                    f"{label} ({unit_label})"
                    if value_type == currency
                    else label
                ),
                "type": value_type,
            }
            for key, label, value_type in columns
        ]

    @api.model
    def report_client_export(
        self,
        wizard_id,
        export_format,
        filters=None,
    ):
        wizard = self.browse(wizard_id).exists()
        if not wizard:
            raise UserError("The report session expired. Reopen the report.")
        if export_format not in {"pdf", "xlsx"}:
            raise UserError("Choose PDF or XLSX.")
        if filters is not None:
            self.report_client_load(
                wizard.report_type,
                filters,
                wizard.id,
            )
            wizard = self.browse(wizard.id).exists()
        definition = wizard.report_definition_id
        if (
            definition
            and (
                export_format == "pdf" and not definition.supports_pdf
                or export_format == "xlsx" and not definition.supports_xlsx
            )
        ):
            raise UserError(
                f"{export_format.upper()} is disabled by the active "
                f"{definition.name} definition."
            )
        wizard.export_format = export_format
        wizard.action_generate_export()
        return {
            "model": wizard._name,
            "id": wizard.id,
            "field": "export_file",
            "filename_field": "export_filename",
            "filename": wizard.export_filename,
            "download": True,
            # Applying the live filters may recreate transient preview lines.
            # Return their current identifiers so fold and drill-down actions
            # remain valid immediately after a download.
            "report_payload": wizard._report_client_payload(),
        }

    @api.model
    def report_client_open_sources(self, wizard_id, line_id):
        wizard = self.browse(wizard_id).exists()
        line = self.env["rebuild.account.report.preview.line"].browse(
            line_id,
        ).exists()
        if not wizard or not line or line.wizard_id != wizard:
            raise UserError("The selected report line is no longer available.")
        return wizard._preview_source_action(line)

    @api.model
    def report_client_toggle_group(self, wizard_id, line_id):
        wizard = self.browse(wizard_id).exists()
        line = self.env["rebuild.account.report.preview.line"].browse(
            line_id,
        ).exists()
        if not wizard or not line or line.wizard_id != wizard:
            raise UserError("The selected report group is no longer available.")
        line.action_toggle_group()
        return wizard._report_client_payload()

    @api.model_create_multi
    def create(self, vals_list):
        can_generate_official_fec = self._can_generate_official_fec()
        for values in vals_list:
            report_type = (
                values.get("report_type")
                or self.env.context.get("default_report_type")
                or "trial_balance"
            )
            if not can_generate_official_fec and report_type == "fec":
                values["fec_test_mode"] = True
            definition = self.env[
                "rebuild.account.report.definition"
            ].browse(values.get("report_definition_id")).exists()
            if not definition:
                company = self.env["res.company"].browse(
                    values.get("company_id"),
                ).exists() or self.env.company
                definition = self.env[
                    "rebuild.account.report.definition"
                ]._resolve(
                    report_type,
                    company,
                    values.get("date_to"),
                )
                values.update({
                    "report_definition_id": definition.id,
                    "report_definition_version": (
                        definition.definition_version
                    ),
                    "report_definition_snapshot": (
                        definition._definition_snapshot()
                    ),
                })
            values.setdefault(
                "amount_rounding",
                definition.default_amount_rounding,
            )
        return super().create(vals_list)

    def write(self, values):
        if (
            not self._can_generate_official_fec()
            and values.get("fec_test_mode") is False
            and any(
                values.get("report_type", wizard.report_type) == "fec"
                for wizard in self
            )
        ):
            raise UserError(
                self.env._(
                    "Only an Accounting Manager can generate an official "
                    "non-test FEC because it may update lock dates.",
                ),
            )
        return super().write(values)

    def action_apply_period(self):
        self.ensure_one()
        self._apply_period_values()
        return self.action_preview_report()

    def action_expand_all(self):
        self.ensure_one()
        self.write({
            "show_details": True,
            "collapsed_group_keys": json.dumps([
                HIERARCHY_STATE_SENTINEL,
            ]),
        })
        return self.action_preview_report()

    def action_collapse_all(self):
        self.ensure_one()
        self.write({"show_details": False})
        return self.action_preview_report()

    def action_preview_report(self):
        self.ensure_one()
        self._prepare_dynamic_filters()
        self._validate_filter_scope()
        if self.report_type == "fec":
            message = (
                "Use Generate Export to create and download the FEC file. "
                "FEC preview is limited to generated export metadata."
            )
            raise UserError(message)
        rows = self._report_rows()
        collapsed_groups = self._collapsed_group_key_set()
        if HIERARCHY_STATE_SENTINEL not in collapsed_groups:
            default_collapsed = sorted({
                str(row.get("group_key"))
                for row in rows
                if row.get("hierarchy_kind") == "statement"
                and row.get("group_key")
            })
            if default_collapsed:
                self.write({
                    "collapsed_group_keys": json.dumps([
                        HIERARCHY_STATE_SENTINEL,
                        *default_collapsed,
                    ]),
                })
        limit = max(1, min(self.preview_limit or 500, 5000))
        visible_rows = self._visible_preview_rows(rows)
        preview_rows = (
            visible_rows[:limit]
            if visible_rows
            else [{"empty_report": "true"}]
        )
        draft_count, warning = self._draft_entry_warning()
        metadata = self._export_metadata(len(rows))
        metadata.update({
            "preview_limit": limit,
            "previewed_row_count": len(preview_rows) if visible_rows else 0,
            "preview_visible_row_count": len(visible_rows),
            "preview_truncated": len(visible_rows) > limit,
            "draft_entry_count": draft_count,
            "warning": warning,
        })
        self.preview_line_ids.sudo().unlink()
        self.write({
            "preview_limit": limit,
            "preview_line_ids": [
                *[
                    Command.create(self._preview_line_values(sequence, row))
                    for sequence, row in enumerate(preview_rows, start=1)
                ],
            ],
            "preview_row_count": len(rows),
            "preview_truncated": len(visible_rows) > limit,
            "preview_generated_at": fields.Datetime.now(),
            "preview_metadata": json.dumps(metadata, indent=2, sort_keys=True),
            "draft_entry_count": draft_count,
            "preview_warning": warning,
        })
        return {
            "type": "ir.actions.act_window",
            "name": self._report_type_label(),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_generate_export(self):
        self.ensure_one()
        self._prepare_dynamic_filters()
        self._validate_filter_scope()
        render_result = None
        if self.report_type == "fec":
            payload, filename, metadata = self._fec_export_payload()
        else:
            rows = self._visible_preview_rows(self._report_rows())
            render_result = (
                self._pdf_payload(rows, return_result=True)
                if self.export_format == "pdf"
                else None
            )
            payload = (
                render_result["pdf"]
                if render_result
                else self._export_payload(rows)
            )
            filename = self._export_filename()
            metadata = self._export_metadata(len(rows))
            if render_result:
                metadata["document_render"] = {
                    "template_key": "accounting_statement.v2",
                    "template_revision": render_result["template_revision"],
                    "payload_sha256": render_result["payload_sha256"],
                    "renderer_version": render_result["renderer_version"],
                    "rendered_company_id": self.company_id.id,
                    "rendered_at": fields.Datetime.to_string(
                        fields.Datetime.now(),
                    ),
                }
        self.write({
            "export_file": BinaryBytes(payload),
            "export_filename": filename,
            "export_metadata": json.dumps(metadata, indent=2, sort_keys=True),
        })
        export_attachment = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", self._name),
            ("res_id", "=", self.id),
            ("res_field", "=", "export_file"),
        ], limit=1, order="id desc")
        closing_attachment = self._attach_generated_closing_package(
            payload,
            filename,
        )
        if render_result:
            provenance = self._usl_accounting_attachment_provenance(
                render_result,
            )
            (export_attachment | closing_attachment).write(provenance)
        return {
            "type": "ir.actions.act_window",
            "name": f"Export — {self._report_type_label()}",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def _usl_accounting_attachment_provenance(self, render_result):
        self.ensure_one()
        template = self.env.ref(
            "usl_document_templates.template_accounting_statement_v2",
        )
        return {
            "usl_document_template_id": template.id,
            "usl_document_template_revision": (
                render_result["template_revision"]
            ),
            "usl_document_payload_sha256": (
                render_result["payload_sha256"]
            ),
            "usl_document_renderer_version": (
                render_result["renderer_version"]
            ),
            "usl_document_company_id": self.company_id.id,
            "usl_document_rendered_at": fields.Datetime.now(),
        }

    def _attach_generated_closing_package(self, payload, filename):
        self.ensure_one()
        closing = self.closing_period_id
        if (
            self.report_type != "closing_package"
            or not closing
            or not self.env.user.has_group("account.group_account_manager")
        ):
            return self.env["ir.attachment"]
        if (
            closing.company_id != self.company_id
            or closing.date_from != self.date_from
            or closing.date_to != self.date_to
        ):
            raise UserError(
                "The closing package company and dates must match the "
                "linked closing workspace."
            )
        checksum = hashlib.sha1(payload).hexdigest()
        attachment = closing.package_attachment_ids.filtered(
            lambda item: (
                item.checksum == checksum
                and item.name == filename
            ),
        )[:1]
        if attachment:
            return attachment
        mimetype = {
            "csv": "text/csv",
            "xlsx": (
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            "pdf": "application/pdf",
            "txt": "text/plain",
        }.get(self.export_format, "application/octet-stream")
        attachment = self.env["ir.attachment"].create({
            "name": filename,
            "type": "binary",
            "raw": payload,
            "mimetype": mimetype,
            "res_model": closing._name,
            "res_id": closing.id,
        })
        closing.write({
            "package_attachment_ids": [Command.link(attachment.id)],
        })
        return attachment

    def _prepare_dynamic_filters(self):
        self.ensure_one()
        if not self.date_from or not self.date_to:
            message = "Select a report start and end date."
            raise UserError(message)
        if self.date_from > self.date_to:
            message = "The start date must be before or equal to the end date."
            raise UserError(message)
        if self.comparison_mode != "none":
            self._apply_comparison_values()
            if (
                not self.comparison_date_from
                or not self.comparison_date_to
            ):
                message = "Select comparison start and end dates."
                raise UserError(message)
            if self.comparison_date_from > self.comparison_date_to:
                message = (
                    "The comparison start date must be before or equal "
                    "to the comparison end date."
                )
                raise UserError(message)

    def _apply_period_values(self):
        self.ensure_one()
        if self.period_preset == "custom":
            self._apply_comparison_values()
            return
        anchor = fields.Date.to_date(
            self.period_anchor_date
            or fields.Date.context_today(self),
        )
        if self.period_preset == "month":
            date_from = anchor.replace(day=1)
            date_to = anchor.replace(
                day=calendar.monthrange(anchor.year, anchor.month)[1],
            )
        elif self.period_preset == "quarter":
            first_month = ((anchor.month - 1) // 3) * 3 + 1
            date_from = anchor.replace(month=first_month, day=1)
            last_month = first_month + 2
            date_to = anchor.replace(
                month=last_month,
                day=calendar.monthrange(anchor.year, last_month)[1],
            )
        else:
            fiscal_from, fiscal_to = self._fiscal_year_dates(anchor)
            date_from = fiscal_from
            date_to = (
                anchor
                if self.period_preset == "year_to_date"
                else fiscal_to
            )
        self.write({
            "date_from": date_from,
            "date_to": date_to,
        })
        self._apply_comparison_values()

    def _fiscal_year_dates(self, anchor):
        self.ensure_one()
        return self.company_id.rebuild_compute_fiscalyear_dates(
            anchor,
        )

    def _apply_comparison_values(self):
        self.ensure_one()
        if self.comparison_mode == "none":
            self.write({
                "comparison_date_from": False,
                "comparison_date_to": False,
            })
            return
        if self.comparison_mode == "custom":
            return
        if not self.date_from or not self.date_to:
            return
        date_from = fields.Date.to_date(self.date_from)
        date_to = fields.Date.to_date(self.date_to)
        if self.comparison_mode == "previous_period":
            period_days = (date_to - date_from).days + 1
            comparison_to = date_from - timedelta(days=1)
            comparison_from = comparison_to - timedelta(
                days=period_days - 1,
            )
        else:
            comparison_from = self._previous_year_date(date_from)
            comparison_to = self._previous_year_date(date_to)
        self.write({
            "comparison_date_from": comparison_from,
            "comparison_date_to": comparison_to,
        })

    @staticmethod
    def _previous_year_date(value):
        try:
            return value.replace(year=value.year - 1)
        except ValueError:
            return value.replace(year=value.year - 1, day=28)

    def _selected_companies(self):
        self.ensure_one()
        companies = self.company_ids | self.company_id
        unauthorized = companies - self.env.companies
        if unauthorized:
            message = (
                "You cannot report on a company outside your allowed "
                "companies."
            )
            raise AccessError(message)
        return companies.sorted(lambda company: (company.name, company.id))

    def _draft_entry_warning(self):
        self.ensure_one()
        if self.report_type in {
            "fixed_assets",
            "fixed_asset_group_account",
            "depreciation_schedule",
        }:
            return 0, ""
        domain = [
            ("company_id", "in", self._selected_companies().ids),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
            ("state", "=", "draft"),
            ("line_ids", "!=", False),
        ]
        count = self.env["account.move"].search_count(domain)
        if not count:
            return 0, ""
        if self.target_move == "all":
            treatment = "est incluse" if count == 1 else "sont incluses"
        else:
            treatment = (
                "est exclue car seules les écritures comptabilisées "
                "sont sélectionnées"
                if count == 1
                else "sont exclues car seules les écritures comptabilisées "
                "sont sélectionnées"
            )
        entry_label = "écriture comptable brouillon" if count == 1 else (
            "écritures comptables brouillon"
        )
        return count, f"{count} {entry_label} {treatment}."

    def action_open_journal_items(self):
        self.ensure_one()
        self._validate_filter_scope(for_drilldown=True)
        if self.report_type == "analytic_report":
            return {
                "type": "ir.actions.act_window",
                "name": "Analytic Lines",
                "res_model": "account.analytic.line",
                "view_mode": "list,form,pivot",
                "views": [
                    (False, "list"),
                    (False, "form"),
                    (False, "pivot"),
                ],
                "domain": self._analytic_line_domain(),
                "context": {"create": False, "delete": False},
            }
        return {
            "type": "ir.actions.act_window",
            "name": "Journal Items",
            "res_model": "account.move.line",
            "view_mode": "list,form,pivot",
            "views": [
                (False, "list"),
                (False, "form"),
                (False, "pivot"),
            ],
            "domain": self._journal_item_domain(),
            "context": {"create": False, "delete": False},
        }

    def _export_filename(self):
        company_key = (
            "multi-company"
            if len(self._selected_companies()) > 1
            else str(
                self.company_id.id
                or self.company_id.id,
            )
        )
        return "%s-%s-%s-%s.%s" % (
            self.report_type.replace("_", "-"),
            company_key,
            fields.Date.to_string(self.date_from),
            fields.Date.to_string(self.date_to),
            self.export_format,
        )

    def _report_type_label(self):
        if self.report_definition_id:
            return self.report_definition_id.name
        return dict(self._fields["report_type"].selection).get(
            self.report_type,
            self.report_type,
        )

    def _report_variant_key(self):
        if self.report_type in {
            "profit_loss",
            "french_annual",
            "french_balance_sheet_2024",
            "french_profit_loss_2024",
            "sig_caf_2024",
        }:
            return "pcg_fr"
        return ""

    def _report_variant_label(self):
        if self._report_variant_key():
            return "Présentation française (PCG)"
        return "Présentation standard"

    def _report_variant_basis(self):
        if self._report_variant_key():
            return (
                "Présentation française résolue pour la société et la "
                "période sélectionnées par la définition de rapport active."
            )
        return ""

    def _document_theme(self):
        self.ensure_one()
        definition = self.report_definition_id
        primary_color = (
            definition.document_primary_color
            if definition
            else "#714B67"
        )
        if (
            definition
            and not definition.company_id
            and primary_color.upper() == "#111111"
        ):
            primary_color = "#714B67"
        return {
            "template": (
                definition.document_template
                if definition
                else "usl_official"
            ),
            "primary_color": primary_color,
            "section_background_color": (
                definition.document_section_background_color
                if definition
                else "#E9ECEF"
            ),
            "section_text_color": (
                definition.document_section_text_color
                if definition
                else "#111111"
            ),
            "muted_color": (
                definition.document_muted_color
                if definition
                else "#666666"
            ),
            "footer_label": (
                definition.document_footer_label
                if definition
                else "Document comptable"
            ),
        }

    def _display_unit_metadata(self):
        self.ensure_one()
        metadata = DISPLAY_UNIT_VALUES.get(
            self.display_unit,
            DISPLAY_UNIT_VALUES["units"],
        )
        currency_symbol = self.company_id.currency_id.symbol or (
            self.company_id.currency_id.name
        )
        short_label = {
            "units": currency_symbol,
            "thousands": f"k{currency_symbol}",
            "millions": f"M{currency_symbol}",
        }.get(self.display_unit, currency_symbol)
        return {
            **metadata,
            "key": self.display_unit,
            "short_label": short_label,
        }

    def _amount_rounding_metadata(self):
        self.ensure_one()
        metadata = AMOUNT_ROUNDING_VALUES.get(
            self.amount_rounding,
            AMOUNT_ROUNDING_VALUES["cents"],
        )
        labels = {
            "whole": {
                "units": "À l’euro",
                "thousands": "Au millier d’euros",
                "millions": "Au million d’euros",
            },
            "cents": {
                "units": "Au centime",
                "thousands": "Deux décimales en k€",
                "millions": "Deux décimales en M€",
            },
        }
        return {
            **metadata,
            "key": self.amount_rounding,
            "label": labels.get(
                self.amount_rounding,
                labels["cents"],
            ).get(
                self.display_unit,
                "Deux décimales",
            ),
        }

    def _preview_line_values(self, sequence, row):
        if row.get("empty_report") == "true":
            label = "Aucune ligne pour les filtres sélectionnés"
        else:
            label = (
                row.get("label")
                or row.get("line_name")
                or row.get("field_label")
                or row.get("asset_name")
                or row.get("name")
                or row.get("payment_ref")
                or row.get("source_original_name")
                or row.get("tax_tag_name")
                or row.get("account_name")
                or row.get("partner_name")
                or row.get("move_name")
                or row.get("journal_name")
                or row.get("report_section")
                or row.get("details")
                or self._report_type_label()
            )
        return {
            "sequence": sequence,
            "company_id": (
                row.get("report_company_id")
                or self.company_id.id
            ),
            "date": row.get("date") or row.get("due_date") or row.get("deferred_date"),
            "section": row.get("section") or row.get("statement_name") or row.get("statement_key") or row.get("report_section") or row.get("form_code") or row.get("journal_code"),
            "line_code": row.get("line_code") or row.get("field_code") or row.get("account_code") or row.get("journal_code"),
            "label": label,
            "account_code": row.get("account_code"),
            "account_name": row.get("account_name"),
            "partner_name": row.get("partner_name"),
            "move_name": row.get("move_name"),
            "opening_balance": _amount(row.get("opening_balance")),
            "debit": _amount(row.get("debit")),
            "credit": _amount(row.get("credit")),
            "movement": _amount(
                row.get("movement")
                or row.get("balance"),
            ),
            "closing_balance": _amount(row.get("closing_balance")),
            "balance": _amount(
                row.get("period_value")
                or row.get("balance")
                or row.get("amount")
                or row.get("net_amount")
                or row.get("statement_balance"),
            ),
            "residual": _amount(row.get("presented_residual") or row.get("residual") or row.get("amount_residual") or row.get("imported_period_net_value")),
            "comparison_value": _amount(row.get("comparison_value")),
            "difference": _amount(row.get("difference")),
            "record_count": int(row.get("record_count") or 0),
            "is_group": row.get("is_group") in (True, "true"),
            "level": int(row.get("row_level") or 0),
            "group_key": row.get("group_key"),
            "parent_group_key": row.get("parent_group_key"),
            "currency_id": (
                row.get("report_currency_id")
                or self.company_id.currency_id.id
            ),
            "row_json": json.dumps(row, indent=2, sort_keys=True, default=str),
        }

    def _visible_preview_rows(self, rows):
        self.ensure_one()
        if self.group_by == "none":
            return rows
        collapsed = self._collapsed_group_key_set()
        hidden_groups = set()
        visible = []
        for row in rows:
            parent_key = str(row.get("parent_group_key") or "")
            hidden_by_parent = (
                parent_key in collapsed
                or parent_key in hidden_groups
            )
            if hidden_by_parent:
                if row.get("is_group") in (True, "true"):
                    hidden_groups.add(str(row.get("group_key") or ""))
                continue
            if row.get("is_group") in (True, "true"):
                visible.append(row)
                if (
                    not self.show_details
                    or str(row.get("group_key") or "") in collapsed
                ):
                    hidden_groups.add(str(row.get("group_key") or ""))
                continue
            if not self.show_details:
                continue
            visible.append(row)
        return visible

    def _collapsed_group_key_set(self):
        self.ensure_one()
        try:
            values = json.loads(self.collapsed_group_keys or "[]")
        except json.JSONDecodeError:
            return set()
        return {
            str(value)
            for value in values
            if value not in (None, "")
        }

    def _toggle_preview_group(self, group_key):
        self.ensure_one()
        if not group_key:
            return self.action_preview_report()
        collapsed = self._collapsed_group_key_set()
        if group_key in collapsed:
            collapsed.remove(group_key)
        else:
            collapsed.add(group_key)
        self.write({
            "show_details": True,
            "collapsed_group_keys": json.dumps(sorted(collapsed)),
        })
        return self.action_preview_report()

    def _journal_item_domain(self, company_ids=None):
        companies = (
            self.env["res.company"].browse(company_ids)
            if company_ids
            else self._selected_companies()
        )
        domain = [
            ("company_id", "in", companies.ids),
            ("move_id.date", "<=", self.date_to),
        ]
        if self.report_type != "trial_balance":
            domain.append(("move_id.date", ">=", self.date_from))
        if self.target_move == "posted":
            domain.append(("move_id.state", "=", "posted"))
        if self.journal_ids:
            domain.append(("journal_id", "in", self.journal_ids.ids))
        if self.account_ids:
            domain.append(("account_id", "in", self.account_ids.ids))
        if self.partner_ids:
            domain.append(("partner_id", "in", self.partner_ids.ids))
        if self.report_type == "partner_ledger":
            domain.append(("partner_id", "!=", False))
        elif self.report_type == "customer_statement":
            domain.extend([
                ("partner_id", "!=", False),
                ("account_id.account_type", "=", "asset_receivable"),
            ])
            if not self.partner_ids:
                domain.append(("partner_id.customer_rank", ">", 0))
        elif self.report_type in ("open_items", "aged_receivable", "aged_payable"):
            domain.extend([
                ("account_id.account_type", "in", ["asset_receivable", "liability_payable"]),
                "|",
                ("reconciled", "=", False),
                ("amount_residual", "!=", 0),
            ])
            if self.report_type == "aged_receivable":
                domain.append(("account_id.account_type", "=", "asset_receivable"))
            elif self.report_type == "aged_payable":
                domain.append(("account_id.account_type", "=", "liability_payable"))
        elif self.report_type == "bank_reconciliation":
            domain.append(("move_id.statement_line_id", "!=", False))
        elif self.report_type == "currency_report":
            domain.extend([
                ("currency_id", "!=", False),
                ("currency_id", "!=", self.company_id.currency_id.id),
            ])
        elif self.report_type == "deferred_schedule":
            schedules = self.env["rebuild.account.deferral.line"].search([
                ("company_id", "=", self.company_id.id),
                ("date", ">=", self.date_from),
                ("date", "<=", self.date_to),
            ])
            move_ids = (schedules.move_id | schedules.deferral_id.original_move_id).ids
            domain.append(("move_id", "in", move_ids or [0]))
        return domain

    def _analytic_line_domain(self, company_ids=None):
        companies = (
            self.env["res.company"].browse(company_ids)
            if company_ids
            else self._selected_companies()
        )
        domain = [
            ("company_id", "in", companies.ids),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
        ]
        if self.journal_ids:
            domain.append(("move_line_id.journal_id", "in", self.journal_ids.ids))
        if self.account_ids:
            domain.append(("general_account_id", "in", self.account_ids.ids))
        if self.partner_ids:
            domain.append(("partner_id", "in", self.partner_ids.ids))
        if self.analytic_plan_ids:
            domain.append(
                ("account_id.plan_id", "in", self.analytic_plan_ids.ids),
            )
        if self.analytic_account_ids:
            domain.append(
                ("account_id", "in", self.analytic_account_ids.ids),
            )
        if self.target_move == "posted":
            domain.extend([
                "|",
                ("move_line_id", "=", False),
                ("move_line_id.move_id.state", "=", "posted"),
            ])
        return domain

    def _preview_source_action(self, preview_line):
        self.ensure_one()
        self._validate_filter_scope(for_drilldown=True)
        row = preview_line._row_payload()
        if self.report_type in {
            "fixed_assets",
            "fixed_asset_group_account",
            "depreciation_schedule",
        }:
            source_move_id = self._row_int(row, "source_move_id")
            if (
                self.report_type == "depreciation_schedule"
                and source_move_id
            ):
                move = self.env["account.move"].search([
                    ("id", "=", source_move_id),
                ], limit=1)
                if move:
                    return {
                        "type": "ir.actions.act_window",
                        "name": move.display_name,
                        "res_model": "account.move",
                        "res_id": move.id,
                        "view_mode": "form",
                        "views": [(False, "form")],
                    }
            source_asset_id = self._row_int(row, "source_asset_id")
            asset = self.env["account.asset"].search([
                ("id", "=", source_asset_id),
            ], limit=1)
            if asset:
                return {
                    "type": "ir.actions.act_window",
                    "name": asset.display_name,
                    "res_model": "account.asset",
                    "res_id": asset.id,
                    "view_mode": "form",
                    "views": [(False, "form")],
                }
        if self.report_type == "analytic_report":
            domain = self._preview_analytic_line_domain(row)
            return {
                "type": "ir.actions.act_window",
                "name": self._preview_source_action_name(preview_line, "Analytic Sources"),
                "res_model": "account.analytic.line",
                "view_mode": "list,form,pivot",
                "views": [
                    (False, "list"),
                    (False, "form"),
                    (False, "pivot"),
                ],
                "domain": domain,
                "context": {"create": False, "delete": False},
            }
        domain = self._preview_journal_item_domain(row)
        return {
            "type": "ir.actions.act_window",
            "name": self._preview_source_action_name(preview_line, "Journal Item Sources"),
            "res_model": "account.move.line",
            "view_mode": "list,form,pivot",
            "views": [
                (False, "list"),
                (False, "form"),
                (False, "pivot"),
            ],
            "domain": domain,
            "context": {"create": False, "delete": False},
        }

    def _preview_source_action_name(self, preview_line, fallback):
        label = preview_line.label or preview_line.line_code or self._report_type_label()
        return f"{fallback} - {label}"[:120]

    def _preview_journal_item_domain(self, row):
        row_company_id = self._row_int(row, "report_company_id")
        row_company_ids = self._row_int_list(row, "report_company_ids")
        domain = list(
            self._journal_item_domain(
                company_ids=(
                    row_company_ids
                    or ([row_company_id] if row_company_id else None)
                ),
            ),
        )
        refinements = []

        source_line_id = self._row_int(row, "source_line_id")
        if source_line_id:
            domain.append(("id", "=", source_line_id))
            refinements.append("source_line_id")

        source_move_ids = self._row_int_values(
            row,
            "source_move_id",
            "imported_source_move_id",
            "source_original_move_id",
            "source_deferred_move_id",
        )
        if source_move_ids:
            domain.append(("move_id", "in", source_move_ids))
            refinements.append("source_move_id")

        source_statement_line_id = self._row_int(row, "source_statement_line_id")
        if source_statement_line_id:
            domain.append(("move_id.statement_line_id", "=", source_statement_line_id))
            refinements.append("source_statement_line_id")

        source_partner_id = self._row_int(row, "source_partner_id")
        if source_partner_id:
            domain.append(("partner_id", "=", source_partner_id))
            refinements.append("source_partner_id")
        elif row.get("partner_name"):
            domain.append(("partner_id.name", "=", row["partner_name"]))
            refinements.append("partner_name")

        source_account_ids = self._row_int_values(row, "source_account_id")
        accounts = self._preview_accounts(row, source_account_ids=source_account_ids)
        if accounts:
            domain.append(("account_id", "in", accounts.ids))
            refinements.append("account")
        elif self._row_has_account_ref(row):
            domain.append(("account_id", "in", [0]))
            refinements.append("missing_account")

        source_tax_tag_id = self._row_int(row, "source_tax_tag_id")
        if source_tax_tag_id:
            tax_tag = self.env["account.account.tag"].search([
                ("id", "=", source_tax_tag_id),
            ], limit=1)
            domain.append(("tax_tag_ids", "in", tax_tag.ids or [0]))
            refinements.append("tax_tag")

        journal_code = row.get("journal_code")
        if journal_code:
            journal = self.env["account.journal"].search([
                ("company_id", "in", [False, self.company_id.id]),
                ("code", "=", journal_code),
            ], limit=1)
            domain.append(("journal_id", "=", journal.id or 0))
            refinements.append("journal_code")

        account_type = row.get("account_type")
        if account_type and not accounts:
            domain.append(("account_id.account_type", "=", account_type))
            refinements.append("account_type")

        report_month = row.get("report_month")
        if report_month and len(str(report_month)) == 7:
            year, month = map(int, str(report_month).split("-"))
            month_from = date(year, month, 1)
            month_to = date(
                year,
                month,
                calendar.monthrange(year, month)[1],
            )
            domain.extend([
                ("move_id.date", ">=", month_from),
                ("move_id.date", "<=", month_to),
            ])
            refinements.append("report_month")

        if not refinements:
            return domain
        return domain

    def _preview_analytic_line_domain(self, row):
        row_company_id = self._row_int(row, "report_company_id")
        row_company_ids = self._row_int_list(row, "report_company_ids")
        domain = list(
            self._analytic_line_domain(
                company_ids=(
                    row_company_ids
                    or ([row_company_id] if row_company_id else None)
                ),
            ),
        )
        analytic_key = self._row_int(row, "analytic_key")
        if analytic_key:
            domain.append(("account_id", "=", analytic_key))
        elif row.get("analytic_name"):
            domain.append(
                ("account_id.name", "=", row["analytic_name"]),
            )
        source_partner_id = self._row_int(row, "source_partner_id")
        if source_partner_id:
            domain.append(("partner_id", "=", source_partner_id))
        source_account_ids = self._row_int_values(row, "source_account_id")
        accounts = self._preview_accounts(row, source_account_ids=source_account_ids)
        if accounts:
            domain.append(("general_account_id", "in", accounts.ids))
        elif self._row_has_account_ref(row):
            domain.append(("general_account_id", "in", [0]))
        return domain

    def _preview_accounts(self, row, source_account_ids=None):
        Account = self.env["account.account"]
        accounts = Account.browse()
        company_ids = (
            self._row_int_list(row, "report_company_ids")
            or [self._row_int(row, "report_company_id") or self.company_id.id]
        )
        if source_account_ids:
            accounts |= Account.search([
                ("company_ids", "in", company_ids),
                ("id", "in", source_account_ids),
            ])
        exact_codes = {
            code
            for code in self._row_account_codes(row)
            if code and any(character.isdigit() for character in code)
        }
        prefixes = [
            prefix.strip()
            for prefix in (row.get("drilldown_account_prefixes") or "").split(",")
            if prefix.strip()
        ]
        if exact_codes or prefixes:
            for company in self.env["res.company"].browse(company_ids):
                for account in Account.with_company(company).search([
                    ("company_ids", "in", company.id),
                ]):
                    code = self._account_code_for_company(account, company=company)
                    if code in exact_codes or any(
                        code.startswith(prefix) for prefix in prefixes
                    ):
                        accounts |= account
        return accounts

    def _row_has_account_ref(self, row):
        return bool(self._row_account_codes(row) or row.get("drilldown_account_prefixes") or row.get("source_account_id"))

    @staticmethod
    def _row_account_codes(row):
        codes = []
        codes.extend(
            code.strip()
            for code in str(
                row.get("drilldown_account_codes") or "",
            ).split(",")
            if code.strip()
        )
        for key in (
            "account_code",
            "asset_account",
            "depreciation_account",
            "depreciation_expense_account",
            "deferred_account_code",
        ):
            value = row.get(key)
            if value:
                codes.append(str(value).strip())
        return [code for code in codes if code]

    def _account_code_for_company(self, account, company=None):
        code_store = account.code_store
        if isinstance(code_store, dict):
            source_company_id = str((company or self.company_id).id or "")
            return (
                code_store.get(source_company_id)
                or code_store.get("1")
                or next(iter(code_store.values()), "")
                or ""
            )
        return getattr(account, "code", False) or str(code_store or "")

    @staticmethod
    def _row_int(row, key):
        value = row.get(key)
        if value in (None, "", False):
            return False
        try:
            return int(value)
        except (TypeError, ValueError):
            return False

    def _row_int_values(self, row, *keys):
        values = []
        for key in keys:
            value = self._row_int(row, key)
            if value and value not in values:
                values.append(value)
        return values

    @staticmethod
    def _row_int_list(row, key):
        value = row.get(key) or []
        if not isinstance(value, (list, tuple, set)):
            return []
        result = []
        for item in value:
            try:
                item = int(item)
            except (TypeError, ValueError):
                continue
            if item and item not in result:
                result.append(item)
        return result

    def _export_metadata(self, row_count=None):
        partner = self.company_id.partner_id
        companies = self._selected_companies()
        return {
            "report_type": self.report_type,
            "report_name": self._report_type_label(),
            "company": ", ".join(companies.mapped("display_name")),
            "companies": [
                {
                    "id": company.id,
                    "name": company.display_name,
                    "currency": company.currency_id.name,
                }
                for company in companies
            ],
            "legal_name": self.company_id.name,
            "company_registry": self.company_id.company_registry or "",
            "vat_number": self.company_id.vat or "",
            "address": ", ".join(filter(None, [
                partner.street,
                partner.street2,
                " ".join(filter(None, [partner.zip, partner.city])),
                partner.country_id.name,
            ])),
            "source_company_id": self.company_id.id,
            "date_from": fields.Date.to_string(self.date_from),
            "date_to": fields.Date.to_string(self.date_to),
            "currency": self.company_id.currency_id.name,
            "display_unit": self.display_unit,
            "amount_rounding": self.amount_rounding,
            "hide_zero_accounts": self.hide_zero_accounts,
            "display_unit_label": self._display_unit_metadata()["label"],
            "display_unit_short_label": (
                self._display_unit_metadata()["short_label"]
            ),
            "display_unit_factor": self._display_unit_metadata()["factor"],
            "amount_rounding_label": (
                self._amount_rounding_metadata()["label"]
            ),
            "amount_decimal_places": (
                self._amount_rounding_metadata()["decimal_places"]
            ),
            "generated_at": fields.Datetime.to_string(fields.Datetime.now()),
            "target_move": self.target_move,
            "period_preset": self.period_preset,
            "comparison_mode": self.comparison_mode,
            "comparison_date_from": (
                fields.Date.to_string(self.comparison_date_from)
                if self.comparison_date_from
                else None
            ),
            "comparison_date_to": (
                fields.Date.to_string(self.comparison_date_to)
                if self.comparison_date_to
                else None
            ),
            "group_by": self.group_by,
            "show_details": self.show_details,
            "collapsed_group_keys": sorted(
                self._collapsed_group_key_set()
                - {HIERARCHY_STATE_SENTINEL},
            ),
            "search_text": self.search_text or "",
            "row_count": row_count,
            "format": self.export_format,
            "report_variant": self._report_variant_key(),
            "report_variant_basis": self._report_variant_basis(),
            "document": self._document_theme(),
            "report_definition": self.report_definition_snapshot or {},
            "report_definition_version": self.report_definition_version or "",
            "fec_test_mode": self.fec_test_mode if self.report_type == "fec" else None,
            "journal_filter": [
                {
                    "id": journal.id,
                    "source_id": journal.id,
                    "code": journal.code,
                    "name": journal.display_name,
                }
                for journal in self.journal_ids.sorted("code")
            ],
            "account_filter": [
                {
                    "id": account.id,
                    "source_id": account.id,
                    "name": account.display_name,
                }
                for account in self.account_ids.sorted("display_name")
            ],
            "partner_filter": [
                {
                    "id": partner.id,
                    "source_id": partner.id,
                    "name": partner.display_name,
                }
                for partner in self.partner_ids.sorted("display_name")
            ],
            "analytic_plan_filter": [
                {
                    "id": plan.id,
                    "name": plan.display_name,
                }
                for plan in self.analytic_plan_ids.sorted("display_name")
            ],
            "analytic_account_filter": [
                {
                    "id": account.id,
                    "name": account.display_name,
                }
                for account in self.analytic_account_ids.sorted(
                    "display_name",
                )
            ],
        }

    @api.onchange("report_type")
    def _onchange_report_type(self):
        if self.report_type == "fec":
            self.export_format = "txt"
            self.target_move = "posted"
            self.fec_test_mode = True
        elif self.export_format == "txt":
            self.export_format = "csv"

    def _export_payload(self, rows):
        if self.export_format == "csv":
            return self._csv_payload(rows)
        if self.export_format == "xlsx":
            return self._xlsx_payload(rows)
        if self.export_format == "pdf":
            return self._pdf_payload(rows)
        message = "Unsupported export format."
        raise UserError(message)

    def _csv_payload(self, rows):
        output = io.StringIO()
        metadata = self._export_metadata(len(rows))
        fieldnames = sorted({key for row in rows for key in row}) or ["empty_report"]
        writer = csv.DictWriter(output, fieldnames=["metadata", *fieldnames], extrasaction="ignore")
        writer.writeheader()
        metadata_text = json.dumps(metadata, sort_keys=True)
        if rows:
            for row in rows:
                writer.writerow({"metadata": metadata_text, **row})
        else:
            writer.writerow({"metadata": metadata_text, "empty_report": "true"})
        return output.getvalue().encode("utf-8")

    def _report_export_columns(self, rows):
        available = {key for row in rows for key in row}
        labels = {
            "report_company_name": "Société",
            "section": "Section",
            "statement_name": "État",
            "report_section": "Section",
            "form_code": "Formulaire",
            "line_code": "Code",
            "field_code": "Champ",
            "date": "Date",
            "due_date": "Échéance",
            "journal_code": "Journal",
            "journal_name": "Libellé du journal",
            "move_name": "Écriture",
            "piece_reference": "Référence de pièce",
            "account_code": "Compte",
            "account_name": "Libellé du compte",
            "partner_name": "Partenaire",
            "label": "Libellé",
            "line_name": "Libellé",
            "field_label": "Libellé du champ",
            "asset_name": "Immobilisation",
            "opening_balance": "Ouverture",
            "debit": "Débit",
            "credit": "Crédit",
            "balance": "Solde",
            "closing_balance": "Clôture",
            "running_balance": "Solde progressif",
            "amount": "Montant",
            "gross_amount": "Brut",
            "depreciation_amount": "Amortissements / provisions",
            "net_amount": "Net",
            "tax_base_amount": "Base taxable",
            "taxable_amount": "Base taxable",
            "tax_amount": "Taxe",
            "residual": "Résiduel",
            "presented_residual": "Résiduel",
            "amount_residual": "Résiduel",
            "amount_residual_currency": "Résiduel en devise",
            "amount_currency": "Montant en devise",
            "imported_period_net_value": "Valeur nette comptable",
            "currency": "Devise",
            "status": "Statut",
            "validation": "Validation",
            "review_status": "Statut de revue",
            "record_count": "Nombre",
            "quantity": "Quantité",
            "value_text": "Valeur / note",
            "period_value": "Période sélectionnée",
            "comparison_value": "Période comparée",
            "difference": "Écart",
            "details": "Explication",
            "next_action": "Action suivante",
            "evidence": "Justificatif",
            "source_reference": "Référence source",
            "metric_value": "Valeur",
            "unit": "Unité",
            "vat_number": "N° TVA",
            "country_code": "Pays",
            "tax_name": "Taxe",
            "tax_treatment": "Traitement fiscal",
            "period_key": "Période déclarative",
            "representation_status": "Comptabilisation",
            "accumulated_depreciation": "Amortissements cumulés",
            "net_book_value_after_line": "Valeur nette après échéance",
            "not_due": "Non échu",
            "bucket_1_30": "1–30 jours",
            "bucket_31_60": "31–60 jours",
            "bucket_61_90": "61–90 jours",
            "bucket_over_90": "> 90 jours",
            "total": "Total dû",
            "source_original_name": "Pièce d’origine",
        }
        if self.report_type == "balance_sheet":
            labels["amount"] = "Solde"
        preferred = {
            "trial_balance": ["account_code", "account_name", "opening_balance", "debit", "credit", "closing_balance"],
            "general_ledger": ["date", "journal_code", "move_name", "account_code", "account_name", "partner_name", "debit", "credit", "balance"],
            "journal_report": ["journal_code", "journal_name", "debit", "credit", "balance"],
            "partner_ledger": ["partner_name", "date", "account_code", "move_name", "debit", "credit", "running_balance"],
            "customer_statement": ["date", "due_date", "move_name", "partner_name", "debit", "credit", "residual", "running_balance"],
            "open_items": ["date", "due_date", "move_name", "account_code", "partner_name", "presented_residual"],
            "aged_receivable": ["partner_name", "not_due", "bucket_1_30", "bucket_31_60", "bucket_61_90", "bucket_over_90", "total"],
            "aged_payable": ["partner_name", "not_due", "bucket_1_30", "bucket_31_60", "bucket_61_90", "bucket_over_90", "total"],
            "balance_sheet": ["section", "line_code", "label", "amount"],
            "profit_loss": ["section", "line_code", "label", "amount"],
            "cash_flow": ["section", "line_code", "label", "amount", "statement_balance"],
            "executive_summary": [
                "section",
                "line_code",
                "label",
                "metric_value",
                "unit",
                "details",
            ],
            "tax_report": [
                "account_code",
                "account_name",
                "tax_name",
                "presented_tax_base",
                "presented_tax_amount",
                "balance",
            ],
            "tax_report_group_account_tax": ["account_code", "account_name", "tax_name", "debit", "credit", "balance"],
            "tax_report_group_tax_account": ["tax_name", "account_code", "account_name", "debit", "credit", "balance"],
            "ec_sales_list": ["period_key", "country_code", "partner_name", "vat_number", "taxable_amount", "tax_amount", "review_status"],
            "oss_sales": ["period_key", "country_code", "partner_name", "tax_name", "taxable_amount", "tax_amount", "review_status"],
            "oss_imports": ["period_key", "country_code", "partner_name", "tax_name", "taxable_amount", "tax_amount", "review_status"],
            "bank_reconciliation": ["date", "journal_code", "payment_ref", "partner_name", "amount", "residual", "status"],
            "currency_report": ["currency", "account_code", "partner_name", "amount_currency", "balance", "amount_residual_currency", "amount_residual"],
            "analytic_report": ["date", "analytic_plan_name", "analytic_account_name", "account_code", "partner_name", "debit", "credit", "balance"],
            "fixed_assets": ["asset_name", "account_code", "acquisition_date", "original_value", "depreciation_amount", "imported_period_net_value", "state"],
            "fixed_asset_group_account": ["account_code", "account_name", "original_value", "depreciation_amount", "imported_period_net_value"],
            "depreciation_schedule": ["asset_name", "depreciation_date", "depreciation_amount", "accumulated_depreciation", "imported_period_net_value", "status"],
            "deferred_schedule": ["deferred_date", "deferred_account_code", "source_original_name", "amount", "deferred_account_balance", "review_status"],
            "french_annual": [
                "statement_name",
                "line_code",
                "label",
                "gross_amount",
                "depreciation_amount",
                "net_amount",
            ],
            "french_balance_sheet_2024": ["statement_name", "line_code", "label", "gross_amount", "depreciation_amount", "net_amount"],
            "french_profit_loss_2024": ["statement_name", "line_code", "label", "amount"],
            "sig_caf_2024": ["statement_name", "line_code", "label", "amount"],
            "french_tax_package": [
                "form_code",
                "field_code",
                "field_label",
                "quantity",
                "amount",
                "rounded_amount",
                "value_text",
                "review_status",
                "source_reference",
            ],
            "closing_package": ["section", "line_code", "label", "status", "validation", "record_count", "amount", "details", "next_action", "evidence"],
        }.get(self.report_type, [])
        if self.report_type in {
            "french_annual",
            "french_balance_sheet_2024",
            "french_profit_loss_2024",
            "sig_caf_2024",
        }:
            labels.update({
                "report_company_name": "Société",
                "label": "Libellé",
                "gross_amount": "Brut",
                "depreciation_amount": "Amortissements / provisions",
                "net_amount": (
                    "Net / montant"
                    if self.report_type == "french_annual"
                    else "Net"
                ),
                "amount": "Montant",
            })
        if self.group_by != "none":
            structural_fields = {
                "statement_name",
                "section",
                "account_name",
                "line_name",
                "line_code",
            }
            chosen = [
                key
                for key in (
                    *(
                        ("report_company_name",)
                        if len(self._selected_companies()) > 1
                        else ()
                    ),
                    "label",
                    *(
                        fieldname
                        for fieldname in preferred
                        if fieldname not in structural_fields
                        and fieldname != "label"
                    ),
                    "comparison_value",
                    "difference",
                )
                if key in available
            ]
        else:
            chosen = [key for key in preferred if key in available]
            if len(self._selected_companies()) > 1:
                chosen.insert(0, "report_company_name")
            if self.comparison_mode != "none":
                chosen.extend([
                    key
                    for key in (
                        "period_value",
                        "comparison_value",
                        "difference",
                    )
                    if key in available and key not in chosen
                ])
        if not chosen:
            excluded = {
                key for key in available
                if key.endswith("_id") or key.startswith("source_") or key in {"row_json"}
            }
            chosen = sorted(available - excluded)[:9]
        if not chosen:
            chosen = ["label"]
        unit_label = self._display_unit_metadata()["short_label"]
        return [
            (
                key,
                (
                    f"{labels.get(key, key.replace('_', ' ').title())} "
                    f"({unit_label})"
                    if key in MONETARY_REPORT_FIELDS
                    else labels.get(key, key.replace("_", " ").title())
                ),
            )
            for key in chosen
        ]

    @staticmethod
    def _report_export_row_value(row, fieldname):
        value = row.get(fieldname)
        if fieldname in DATE_REPORT_FIELDS and value:
            return fields.Date.to_date(str(value)[:10]).strftime(
                "%d/%m/%Y",
            )
        if fieldname != "label":
            return value
        label = value or (
            row.get("line_name")
            or row.get("field_label")
            or row.get("asset_name")
            or row.get("tax_tag_name")
            or row.get("account_name")
            or row.get("partner_name")
            or row.get("move_name")
            or row.get("report_section")
            or ""
        )
        account_code = str(row.get("account_code") or "").strip()
        if (
            account_code
            and row.get("hierarchy_kind") in {"pcg_group", "account"}
            and not str(label).startswith(f"{account_code} ")
        ):
            label = f"{account_code} {label}"
        if (
            row.get("is_group") not in (True, "true")
            and row.get("hierarchy_kind") not in {"account"}
        ):
            label = f"  {label}"
        return label

    @staticmethod
    def _xlsx_write_value(
        worksheet,
        row,
        column,
        value,
        formats,
        fieldname,
        presentation_role="detail",
        monetary_scale_factor=1,
        monetary_decimal_places=None,
    ):
        numeric_fields = {
            "opening_balance", "debit", "credit", "balance", "closing_balance", "movement",
            "amount", "gross_amount", "depreciation_amount", "net_amount", "residual",
            "presented_residual", "amount_residual", "imported_period_net_value", "original_value",
            "amount_currency", "rounded_amount", "statement_balance", "record_count",
            "quantity", "tax_base_amount", "presented_tax_base",
            "presented_tax_amount", "period_value",
            "comparison_value", "difference",
            "metric_value",
        }
        label_fields = {
            "label",
            "line_name",
            "field_label",
            "account_name",
            "partner_name",
        }
        body_format = formats.get(
            (
                f"{presentation_role}_label"
                if fieldname in label_fields
                else f"{presentation_role}_body"
            ),
            formats["body"],
        )
        number_format = formats.get(
            f"{presentation_role}_number",
            formats["number"],
        )
        if value in (None, "", False):
            worksheet.write_blank(row, column, None, body_format)
            return
        if fieldname in numeric_fields:
            try:
                numeric_value = float(value)
                if fieldname in MONETARY_REPORT_FIELDS:
                    numeric_value /= monetary_scale_factor or 1
                    if monetary_decimal_places is not None:
                        quantum = Decimal(1).scaleb(
                            -monetary_decimal_places,
                        )
                        numeric_value = float(
                            Decimal(str(numeric_value)).quantize(
                                quantum,
                                rounding=ROUND_HALF_UP,
                            ),
                        )
                worksheet.write_number(
                    row,
                    column,
                    numeric_value,
                    number_format,
                )
                return
            except (TypeError, ValueError):
                pass
        worksheet.write(row, column, str(value), body_format)

    def _xlsx_payload(self, rows):
        try:
            import xlsxwriter  # noqa: PLC0415
        except ImportError as exc:
            message = "XLSX export requires the xlsxwriter Python package in the Odoo runtime."
            raise UserError(message) from exc

        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {
            "in_memory": True,
            "strings_to_formulas": False,
            "strings_to_urls": False,
        })
        workbook.set_properties({
            "title": self._report_type_label(),
            "subject": (
                f"{self.company_id.display_name} - "
                f"{self.date_from.strftime('%d/%m/%Y')} au "
                f"{self.date_to.strftime('%d/%m/%Y')}"
            ),
            "company": self.company_id.display_name,
            "comments": "Generated from Odoo Community by the USL accounting report exporter.",
        })
        metadata = self._export_metadata(len(rows))
        decimal_places = metadata["amount_decimal_places"]
        decimal_suffix = ".00" if decimal_places else ""
        number_pattern = f"#,##0{decimal_suffix}"
        document_theme = metadata["document"]
        primary_color = document_theme["primary_color"]
        section_background_color = (
            document_theme["section_background_color"]
        )
        section_text_color = document_theme["section_text_color"]
        muted_color = document_theme["muted_color"]
        formats = {
            "title": workbook.add_format({
                "bold": True,
                "font_size": 18,
                "font_color": primary_color,
            }),
            "subtitle": workbook.add_format({
                "font_size": 10,
                "font_color": muted_color,
                "text_wrap": True,
                "valign": "vcenter",
            }),
            "header": workbook.add_format({
                "bold": True,
                "font_color": section_text_color,
                "bg_color": section_background_color,
                "border": 1,
                "border_color": primary_color,
                "text_wrap": True,
                "valign": "vcenter",
            }),
            "metadata_key": workbook.add_format({"bold": True, "bg_color": "#E8EDF2", "border": 1}),
            "metadata_value": workbook.add_format({
                "border": 1,
                "text_wrap": True,
                "valign": "top",
                "align": "left",
                "num_format": "@",
            }),
            "body": workbook.add_format({"border": 1, "border_color": "#D5DBE1", "valign": "top", "text_wrap": True}),
            "number": workbook.add_format({
                "border": 1,
                "border_color": "#D5DBE1",
                "num_format": (
                    f"{number_pattern};[Red]-{number_pattern};-"
                ),
                "align": "right", "valign": "top",
            }),
        }
        role_styles = {
            "section": {
                "bold": True,
                "font_color": section_text_color,
                "bg_color": section_background_color,
                "top": 1,
                "bottom": 1,
                "border_color": primary_color,
            },
            "group": {
                "bold": True,
                "font_color": primary_color,
                "bg_color": "#F1F1F1",
                "top": 1,
                "bottom": 1,
                "border_color": "#B8C7D3",
            },
            "subtotal": {
                "bold": True,
                "font_color": primary_color,
                "top": 1,
                "bottom": 1,
                "border_color": "#8395A4",
            },
            "total": {
                "bold": True,
                "font_color": primary_color,
                "top": 1,
                "bottom": 6,
                "border_color": primary_color,
            },
            "control": {
                "bold": True,
                "font_color": primary_color,
                "bg_color": "#F7F7F7",
                "top": 1,
                "bottom": 1,
                "border_color": "#6E93AA",
            },
            "detail": {
                "font_color": "#243B53",
                "bottom": 1,
                "border_color": "#E3E8EC",
            },
            "empty": {
                "italic": True,
                "font_color": "#6B7C8C",
                "bottom": 1,
                "border_color": "#E3E8EC",
            },
        }
        for role, style in role_styles.items():
            base_style = {
                **style,
                "valign": "vcenter",
            }
            formats[f"{role}_body"] = workbook.add_format(base_style)
            formats[f"{role}_label"] = workbook.add_format({
                **base_style,
                "indent": 1 if role == "detail" else 0,
                "text_wrap": True,
            })
            formats[f"{role}_number"] = workbook.add_format({
                **base_style,
                "align": "right",
                "num_format": (
                    f"{number_pattern};-{number_pattern};-"
                    if role == "section"
                    else (
                        f"{number_pattern};[Red]-{number_pattern};-"
                    )
                ),
            })
        metadata_sheet = workbook.add_worksheet("Metadata")
        metadata_sheet.hide_gridlines(2)
        metadata_sheet.write(0, 0, self._report_type_label(), formats["title"])
        metadata_sheet.merge_range(0, 0, 0, 1, self._report_type_label(), formats["title"])
        for row_idx, (key, value) in enumerate(metadata.items(), start=2):
            metadata_sheet.write(row_idx, 0, key.replace("_", " ").title(), formats["metadata_key"])
            display_value = self._export_metadata_display_value(
                key,
                value,
            )
            metadata_sheet.write(row_idx, 1, "" if display_value is None else str(display_value), formats["metadata_value"])
        metadata_sheet.set_column(0, 0, 28)
        metadata_sheet.set_column(1, 1, 88)
        metadata_sheet.set_landscape()
        metadata_sheet.fit_to_pages(1, 0)

        report_sheet = workbook.add_worksheet("Report")
        report_sheet.hide_gridlines(2)
        columns = self._report_export_columns(rows)
        last_column = max(0, len(columns) - 1)
        zero_accounts_label = (
            " | Lignes à zéro masquées"
            if metadata["hide_zero_accounts"]
            else ""
        )
        date_from_display = self._display_export_date(
            metadata["date_from"],
        )
        date_to_display = self._display_export_date(
            metadata["date_to"],
        )
        subtitle = (
            f"{metadata['company']} | {date_from_display} au "
            f"{date_to_display} | {metadata['currency']} | "
            f"{metadata['display_unit_label']} "
            f"({metadata['display_unit_short_label']}) | "
            f"{metadata['amount_rounding_label']}"
            f"{zero_accounts_label}"
        )
        if last_column:
            report_sheet.merge_range(0, 0, 0, last_column, self._report_type_label(), formats["title"])
            report_sheet.merge_range(1, 0, 1, last_column, subtitle, formats["subtitle"])
        else:
            report_sheet.write(0, 0, self._report_type_label(), formats["title"])
            report_sheet.write(1, 0, subtitle, formats["subtitle"])
        report_sheet.set_row(0, 26)
        report_sheet.set_row(1, 30)
        header_row = 3
        for column_idx, (fieldname, label) in enumerate(columns):
            report_sheet.write(header_row, column_idx, label, formats["header"])
            width = 15
            if fieldname in {
                "label",
                "line_name",
                "field_label",
                "account_name",
                "partner_name",
            }:
                width = 58
            elif fieldname in {
                "details",
                "evidence",
                "next_action",
                "source_reference",
            }:
                width = 42
            report_sheet.set_column(column_idx, column_idx, width)
        data_rows = rows or [{
            "label": "Aucune ligne pour les filtres sélectionnés",
        }]
        for row_idx, row in enumerate(data_rows, start=header_row + 1):
            presentation_role = self._report_presentation_role(row)
            for column_idx, (fieldname, _label) in enumerate(columns):
                self._xlsx_write_value(
                    report_sheet,
                    row_idx,
                    column_idx,
                    self._report_export_row_value(row, fieldname),
                    formats,
                    fieldname,
                    presentation_role,
                    metadata["display_unit_factor"],
                    metadata["amount_decimal_places"],
                )
        report_sheet.freeze_panes(header_row + 1, 0)
        report_sheet.autofilter(header_row, 0, header_row + len(data_rows), last_column)
        report_sheet.set_landscape()
        report_sheet.fit_to_pages(1, 0)
        report_sheet.repeat_rows(header_row, header_row)
        report_sheet.set_header(
            f"&L{self.company_id.display_name}"
            f"&C{self._report_type_label()}"
            f"&RArrêté au {date_to_display}",
        )
        report_sheet.set_footer(
            "&LExport comptable Odoo Community"
            "&CPage &P sur &N"
            f"&RGénéré le "
            f"{self._display_export_datetime(metadata['generated_at'])}"
        )

        raw_sheet = workbook.add_worksheet("Audit Data")
        raw_sheet.hide_gridlines(2)
        fieldnames = sorted({key for row in rows for key in row}) or ["empty_report"]
        for column_idx, fieldname in enumerate(fieldnames):
            raw_sheet.write(0, column_idx, fieldname, formats["header"])
            width = max(12, min(42, len(fieldname) + 3))
            if fieldname in {"details", "evidence", "label", "next_action", "source_reference"}:
                width = 38
            raw_sheet.set_column(column_idx, column_idx, width)
        for row_idx, row in enumerate(rows or [{"empty_report": "true"}], start=1):
            for column_idx, fieldname in enumerate(fieldnames):
                self._xlsx_write_value(
                    raw_sheet,
                    row_idx,
                    column_idx,
                    row.get(fieldname),
                    formats,
                    fieldname,
                    "raw",
                )
        raw_sheet.freeze_panes(1, 0)
        raw_sheet.autofilter(0, 0, max(1, len(rows)), max(0, len(fieldnames) - 1))

        workbook.close()
        return output.getvalue()

    def _export_metadata_display_value(self, key, value):
        if value in (None, False, ""):
            return value
        if key in {
            "date_from",
            "date_to",
            "period_anchor_date",
            "comparison_date_from",
            "comparison_date_to",
        }:
            return self._display_export_date(value)
        if key == "generated_at":
            return self._display_export_datetime(value)
        if isinstance(value, (list, dict)):
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
            )
        return value

    @staticmethod
    def _display_export_date(value):
        return fields.Date.to_date(str(value)[:10]).strftime("%d/%m/%Y")

    def _display_export_datetime(self, value):
        generated_at = fields.Datetime.to_datetime(value)
        localized = fields.Datetime.context_timestamp(
            self,
            generated_at,
        )
        return localized.strftime("%d/%m/%Y %H:%M")

    def _legacy_reportlab_pdf_payload(self, rows):
        try:
            from reportlab.lib import colors  # noqa: PLC0415
            from reportlab.lib.enums import (  # noqa: PLC0415
                TA_CENTER,
                TA_LEFT,
                TA_RIGHT,
            )
            from reportlab.lib.pagesizes import A4, landscape  # noqa: PLC0415
            from reportlab.lib.styles import (  # noqa: PLC0415
                ParagraphStyle,
                getSampleStyleSheet,
            )
            from reportlab.lib.units import mm  # noqa: PLC0415
            from reportlab.pdfbase import pdfmetrics  # noqa: PLC0415
            from reportlab.pdfbase.ttfonts import TTFError, TTFont  # noqa: PLC0415
            from reportlab.platypus import (  # noqa: I001, PLC0415
                BaseDocTemplate,
                Frame,
                PageBreak,
                PageTemplate,
                Paragraph,
                Spacer,
                Table,
                TableStyle,
            )
        except ImportError as exc:
            message = "PDF export requires the reportlab Python package in the Odoo runtime."
            raise UserError(message) from exc

        font_name = "Helvetica"
        bold_font_name = "Helvetica-Bold"
        try:
            pdfmetrics.registerFont(TTFont("DejaVuSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
            font_name = "DejaVuSans"
            bold_font_name = "DejaVuSans-Bold"
        except (OSError, TTFError):
            font_name = "Helvetica"
            bold_font_name = "Helvetica-Bold"

        output = io.BytesIO()
        metadata = self._export_metadata(len(rows))
        document_theme = metadata["document"]
        primary_color = colors.HexColor(
            document_theme["primary_color"],
        )
        section_background_color = colors.HexColor(
            document_theme["section_background_color"],
        )
        section_text_color = colors.HexColor(
            document_theme["section_text_color"],
        )
        muted_color = colors.HexColor(
            document_theme["muted_color"],
        )
        light_rule_color = colors.HexColor("#C9CDD1")
        soft_background_color = colors.HexColor("#F7F7F7")
        date_from_display = fields.Date.to_date(metadata["date_from"]).strftime("%d/%m/%Y")
        date_to_display = fields.Date.to_date(metadata["date_to"]).strftime("%d/%m/%Y")
        columns = self._report_export_columns(rows)
        wide_report = self.report_type in {
            "general_ledger", "closing_package", "french_tax_package", "depreciation_schedule",
        } or len(columns) > 7
        page_size = landscape(A4) if wide_report else A4
        document = BaseDocTemplate(
            output,
            pagesize=page_size,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=34 * mm,
            bottomMargin=19 * mm,
            title=self._report_type_label(),
            author=self.company_id.display_name,
            subject=(
                f"Rapport comptable du {date_from_display} au "
                f"{date_to_display}"
            ),
        )
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(
            name="USLTitle",
            parent=styles["Title"],
            fontName=bold_font_name,
            fontSize=18,
            leading=21,
            textColor=primary_color,
            alignment=TA_LEFT,
            spaceAfter=3 * mm,
        ))
        styles.add(ParagraphStyle(
            name="USLSubtitle",
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=9,
            leading=12,
            textColor=muted_color,
            spaceAfter=5 * mm,
        ))
        styles.add(ParagraphStyle(
            name="USLSection",
            parent=styles["Heading2"],
            fontName=bold_font_name,
            fontSize=12,
            leading=15,
            textColor=primary_color,
            spaceBefore=4 * mm,
            spaceAfter=2 * mm,
        ))
        styles.add(ParagraphStyle(
            name="USLBody",
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=7.2,
            leading=9,
            textColor=primary_color,
            alignment=TA_LEFT,
        ))
        styles.add(ParagraphStyle(
            name="USLBodyRight",
            parent=styles["USLBody"],
            alignment=TA_RIGHT,
        ))
        styles.add(ParagraphStyle(
            name="USLBodyBold",
            parent=styles["USLBody"],
            fontName=bold_font_name,
        ))
        styles.add(ParagraphStyle(
            name="USLBodyRightBold",
            parent=styles["USLBodyRight"],
            fontName=bold_font_name,
        ))
        styles.add(ParagraphStyle(
            name="USLHeaderCell",
            parent=styles["USLBody"],
            fontName=bold_font_name,
            textColor=primary_color,
            alignment=TA_CENTER,
        ))
        styles.add(ParagraphStyle(
            name="USLNote",
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=8,
            leading=11,
            textColor=primary_color,
            backColor=soft_background_color,
            borderColor=light_rule_color,
            borderWidth=0.5,
            borderPadding=7,
            spaceAfter=4 * mm,
        ))

        def clean_text(value):
            text = "" if value is None else str(value)
            if font_name == "Helvetica":
                return text.encode("latin-1", "replace").decode("latin-1")
            return text

        def amount_text(value, fieldname):
            if value in (None, "", False):
                return ""
            try:
                amount = Decimal(str(value))
                if fieldname in MONETARY_REPORT_FIELDS:
                    amount /= Decimal(
                        str(metadata["display_unit_factor"] or 1),
                    )
                    decimal_places = metadata["amount_decimal_places"]
                    quantum = Decimal(1).scaleb(-decimal_places)
                    amount = amount.quantize(
                        quantum,
                        rounding=ROUND_HALF_UP,
                    )
                    return (
                        f"{amount:,.{decimal_places}f}"
                        .replace(",", " ")
                        .replace(".", ",")
                    )
                return f"{amount:,.2f}".replace(",", " ").replace(".", ",")
            except (ArithmeticError, TypeError, ValueError):
                return clean_text(value)

        numeric_fields = MONETARY_REPORT_FIELDS | {
            "record_count",
            "quantity",
            "metric_value",
        }
        status_labels = {
            "pass": "Conforme",
            "warning": "Alerte",
            "block": "Bloquant",
            "not_applicable": "Sans objet",
            "blocked": "Bloqué",
            "ready": "Prêt",
            "open": "Ouvert",
            "internal_review": "Revue interne",
            "accountant_review": "Revue comptable",
            "data_missing": "Données manquantes",
            "matched": "Concordant",
            "mismatch": "Ecart",
            "review": "A revoir",
            "prefilled": "Prérempli",
            "unresolved": "Non résolu",
            "posted": "Ecritures comptabilisées",
        }

        def cell(value, fieldname="", presentation_role=""):
            if fieldname in {"status", "validation", "review_status"}:
                display = status_labels.get(str(value or ""), clean_text(value))
            else:
                display = (
                    amount_text(value, fieldname)
                    if fieldname in numeric_fields
                    else clean_text(value)
                )
            emphasized = presentation_role in {
                "section",
                "group",
                "subtotal",
                "total",
                "control",
            }
            if fieldname in numeric_fields:
                style = styles[
                    "USLBodyRightBold" if emphasized else "USLBodyRight"
                ]
            else:
                style = styles[
                    "USLBodyBold" if emphasized else "USLBody"
                ]
            return Paragraph(display.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), style)

        def table_style(extra=None):
            commands = [
                ("BACKGROUND", (0, 0), (-1, 0), section_background_color),
                ("TEXTCOLOR", (0, 0), (-1, 0), section_text_color),
                ("FONTNAME", (0, 0), (-1, 0), bold_font_name),
                ("BOX", (0, 0), (-1, -1), 0.45, light_rule_color),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, light_rule_color),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, primary_color),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, soft_background_color],
                ),
            ]
            return TableStyle([*commands, *(extra or [])])

        def append_chunked_table(
            section_title,
            header_labels,
            body_rows,
            column_widths,
            *,
            chunk_size,
            status_values=None,
            emphasis_values=None,
            presentation_roles=None,
            page_break_before=True,
        ):
            chunk_count = max(
                1,
                math.ceil(len(body_rows) / max(1, chunk_size)),
            )
            base_size, larger_chunk_count = divmod(
                len(body_rows),
                chunk_count,
            )
            chunk_sizes = [
                base_size + (1 if index < larger_chunk_count else 0)
                for index in range(chunk_count)
            ]
            chunks = []
            row_offset = 0
            for balanced_size in chunk_sizes:
                chunks.append(
                    body_rows[row_offset:row_offset + balanced_size],
                )
                row_offset += balanced_size
            row_offset = 0
            for chunk_index, chunk in enumerate(chunks):
                if page_break_before or chunk_index:
                    story.append(PageBreak())
                continuation = " (suite)" if chunk_index else ""
                story.append(Paragraph(clean_text(section_title + continuation), styles["USLSection"]))
                header = [Paragraph(clean_text(label), styles["USLHeaderCell"]) for label in header_labels]
                extra_style = []
                if status_values:
                    status_colors = {
                        "pass": "#E7F4EC",
                        "warning": "#FFF4D6",
                        "block": "#FDE8E8",
                        "not_applicable": "#EEF1F4",
                    }
                    chunk_statuses = status_values[
                        row_offset:row_offset + len(chunk)
                    ]
                    for row_index, status in enumerate(chunk_statuses, start=1):
                        if status in status_colors:
                            extra_style.append((
                                "BACKGROUND", (0, row_index), (0, row_index),
                                colors.HexColor(status_colors[status]),
                            ))
                if emphasis_values:
                    chunk_emphasis = emphasis_values[
                        row_offset:row_offset + len(chunk)
                    ]
                    for row_index, emphasized in enumerate(chunk_emphasis, start=1):
                        if emphasized:
                            extra_style.extend([
                                ("BACKGROUND", (0, row_index), (-1, row_index), colors.HexColor("#E8EDF2")),
                                ("FONTNAME", (0, row_index), (-1, row_index), bold_font_name),
                            ])
                if presentation_roles:
                    chunk_roles = presentation_roles[
                        row_offset:row_offset + len(chunk)
                    ]
                    for row_index, role in enumerate(chunk_roles, start=1):
                        role_commands = {
                            "section": [
                                (
                                    "BACKGROUND",
                                    (0, row_index),
                                    (-1, row_index),
                                    section_background_color,
                                ),
                                (
                                    "TEXTCOLOR",
                                    (0, row_index),
                                    (-1, row_index),
                                    section_text_color,
                                ),
                                ("FONTNAME", (0, row_index), (-1, row_index), bold_font_name),
                                (
                                    "LINEABOVE",
                                    (0, row_index),
                                    (-1, row_index),
                                    0.8,
                                    primary_color,
                                ),
                            ],
                            "group": [
                                (
                                    "BACKGROUND",
                                    (0, row_index),
                                    (-1, row_index),
                                    soft_background_color,
                                ),
                                ("FONTNAME", (0, row_index), (-1, row_index), bold_font_name),
                            ],
                            "subtotal": [
                                ("LINEABOVE", (0, row_index), (-1, row_index), 0.8, muted_color),
                                ("FONTNAME", (0, row_index), (-1, row_index), bold_font_name),
                            ],
                            "total": [
                                ("LINEABOVE", (0, row_index), (-1, row_index), 1.0, primary_color),
                                ("LINEBELOW", (0, row_index), (-1, row_index), 1.5, primary_color),
                                ("FONTNAME", (0, row_index), (-1, row_index), bold_font_name),
                            ],
                            "control": [
                                ("BACKGROUND", (0, row_index), (-1, row_index), soft_background_color),
                                ("LINEABOVE", (0, row_index), (-1, row_index), 0.8, muted_color),
                                ("FONTNAME", (0, row_index), (-1, row_index), bold_font_name),
                            ],
                            "detail": [
                                ("LEFTPADDING", (0, row_index), (0, row_index), 11),
                            ],
                        }.get(role, [])
                        extra_style.extend(role_commands)
                chunk_table = Table([header, *chunk], colWidths=column_widths, repeatRows=1)
                chunk_table.setStyle(table_style(extra_style))
                story.append(chunk_table)
                row_offset += len(chunk)

        def draw_page(canvas, doc):
            width, height = page_size
            canvas.saveState()
            if self.report_type == "french_annual" and doc.page == 1:
                canvas.setFont(font_name, 7.5)
                canvas.setFillColor(muted_color)
                canvas.drawRightString(
                    width - doc.rightMargin,
                    7.5 * mm,
                    clean_text("Page 1"),
                )
                canvas.restoreState()
                return
            canvas.setFillColor(primary_color)
            canvas.setFont(bold_font_name, 11)
            canvas.drawString(
                doc.leftMargin,
                height - 13 * mm,
                clean_text(self.company_id.display_name.upper()),
            )
            canvas.setFillColor(muted_color)
            canvas.setFont(font_name, 7.2)
            canvas.drawString(
                doc.leftMargin,
                height - 17.5 * mm,
                clean_text(
                    (
                        "COMPTES PRÉPARÉS PAR LA SOCIÉTÉ — NON ATTESTÉS"
                        if self.report_type == "french_annual"
                        else "DOCUMENT COMPTABLE OFFICIEL"
                    ),
                ),
            )
            identity_lines = [
                " - ".join(filter(None, [
                    metadata["company_registry"],
                    (
                        f"TVA {metadata['vat_number']}"
                        if metadata["vat_number"]
                        else ""
                    ),
                ])),
                metadata["address"],
                f"Arrêté au {date_to_display}",
            ]
            canvas.setFont(font_name, 6.8)
            for line_index, identity_line in enumerate(
                filter(None, identity_lines),
            ):
                display_line = clean_text(identity_line)
                if len(display_line) > 96:
                    display_line = f"{display_line[:93]}..."
                canvas.drawRightString(
                    width - doc.rightMargin,
                    height - (11.5 + line_index * 3.7) * mm,
                    display_line,
                )
            canvas.setStrokeColor(primary_color)
            canvas.setLineWidth(0.7)
            canvas.line(
                doc.leftMargin,
                height - 27 * mm,
                width - doc.rightMargin,
                height - 27 * mm,
            )
            canvas.setStrokeColor(light_rule_color)
            canvas.setLineWidth(0.5)
            canvas.line(doc.leftMargin, 11 * mm, width - doc.rightMargin, 11 * mm)
            canvas.setFont(font_name, 7.5)
            canvas.setFillColor(muted_color)
            canvas.drawString(
                doc.leftMargin,
                7.5 * mm,
                clean_text(
                    f"{document_theme['footer_label']} - "
                    f"{self._report_type_label()}",
                ),
            )
            canvas.drawCentredString(
                width / 2,
                7.5 * mm,
                clean_text(
                    "Généré le "
                    f"{self._display_export_datetime(metadata['generated_at'])}"
                ),
            )
            canvas.drawRightString(
                width - doc.rightMargin,
                7.5 * mm,
                clean_text(f"Page {doc.page}"),
            )
            canvas.restoreState()

        title = (
            "Dossier de clôture"
            if self.report_type == "closing_package"
            else self._report_type_label()
        )
        display_scale_context = (
            ""
            if metadata["display_unit"] == "units"
            else (
                f" - {metadata['display_unit_label']} "
                f"({metadata['display_unit_short_label']})"
            )
        )
        rounding_context = f" - {metadata['amount_rounding_label']}"
        zero_accounts_context = (
            " - Lignes à zéro masquées"
            if metadata["hide_zero_accounts"]
            else ""
        )
        story = [
            Paragraph(clean_text(title), styles["USLTitle"]),
            Paragraph(
                clean_text(
                    f"{metadata['company']} - Exercice du {date_from_display} au {date_to_display} - "
                    f"Monnaie {metadata['currency']}"
                    f"{display_scale_context}{rounding_context}"
                    f"{zero_accounts_context}",
                ),
                styles["USLSubtitle"],
            ),
        ]

        if self.report_type == "french_annual":
            story.insert(0, Spacer(1, 12 * mm))
            story.extend([
                Spacer(1, 25 * mm),
                Paragraph(
                    clean_text(
                        "Comptes annuels préparés par Unstatic Labs à partir "
                        "du grand livre sélectionné. Ce document n’est ni "
                        "une attestation professionnelle, ni un rapport de "
                        "mission d’un expert-comptable ou d’un commissaire "
                        "aux comptes."
                    ),
                    styles["USLNote"],
                ),
                Spacer(1, 20 * mm),
                Paragraph(
                    clean_text(
                        f"Exercice du {date_from_display} au "
                        f"{date_to_display}"
                    ),
                    styles["USLSection"],
                ),
                Paragraph(
                    clean_text(
                        f"Monnaie de présentation : "
                        f"{metadata['currency']}"
                    ),
                    styles["USLSubtitle"],
                ),
                PageBreak(),
                Paragraph("Sommaire", styles["USLTitle"]),
                Paragraph(
                    clean_text(
                        "1. Base de préparation et statut du document<br/>"
                        "2. Bilan — actif<br/>"
                        "3. Bilan — passif<br/>"
                        "4. Compte de résultat<br/>"
                        "5. Soldes intermédiaires de gestion et CAF<br/>"
                        "6. Ratios de gestion"
                    ),
                    styles["USLBody"],
                ),
                PageBreak(),
                Paragraph(
                    "Base de préparation et statut du document",
                    styles["USLTitle"],
                ),
                Paragraph(
                    clean_text(
                        "Les états sont produits depuis les écritures "
                        "comptabilisées du périmètre affiché, selon les "
                        "rubriques du Plan comptable général français. Les "
                        "montants restent traçables jusqu’aux comptes et aux "
                        "écritures sources dans le rapport interactif."
                    ),
                    styles["USLNote"],
                ),
                Paragraph(
                    clean_text(
                        "Les méthodes d’évaluation, travaux d’inventaire, "
                        "estimations et informations d’annexe doivent être "
                        "confirmés lors de la clôture. Cette édition de "
                        "gestion ne remplace pas l’annexe légale lorsque "
                        "celle-ci est requise et ne reproduit aucune "
                        "attestation du précédent professionnel."
                    ),
                    styles["USLNote"],
                ),
            ])

        if self.report_type == "closing_package":
            story.append(Paragraph(
                "Ce dossier prépare la revue de clôture et centralise les contrôles, obligations, sources et actions. "
                "Il ne constitue ni une attestation professionnelle ni une déclaration déposée. Les décisions d'acceptation "
                "restent enregistrées séparément par l'autorité habilitée.",
                styles["USLNote"],
            ))
            overview = next((row for row in rows if row.get("line_code") == "CLOSE_STATUS"), {})
            story.append(Paragraph("Synthèse de préparation", styles["USLSection"]))
            overview_data = [
                [Paragraph("Etat", styles["USLHeaderCell"]), Paragraph("Validation", styles["USLHeaderCell"]),
                 Paragraph("Contrôles", styles["USLHeaderCell"]), Paragraph("Synthèse", styles["USLHeaderCell"])],
                [cell(overview.get("status"), "status"), cell(overview.get("validation"), "validation"),
                 cell(overview.get("record_count")), cell(overview.get("details"))],
            ]
            overview_table = Table(overview_data, colWidths=[28 * mm, 28 * mm, 22 * mm, document.width - 78 * mm])
            overview_table.setStyle(table_style())
            story.extend([overview_table, Spacer(1, 3 * mm)])

            control_rows = [row for row in rows if str(row.get("section") or "").startswith("Closing control")]
            control_data = []
            for row in control_rows:
                control_data.append([
                    cell(row.get("status"), "status"), cell(row.get("label")), cell(row.get("record_count")),
                    cell(row.get("amount"), "amount"), cell(row.get("details")), cell(row.get("next_action")),
                ])
            control_widths = [22 * mm, 38 * mm, 12 * mm, 22 * mm, (document.width - 94 * mm) * 0.45, (document.width - 94 * mm) * 0.55]
            append_chunked_table(
                "Contrôles de clôture",
                ["Statut", "Contrôle", "Nb", "Montant", "Constat", "Action suivante"],
                control_data,
                control_widths,
                chunk_size=8,
                status_values=[row.get("status") for row in control_rows],
            )

            declaration_rows = [row for row in rows if row.get("section") == "Declaration schedule"]
            declaration_data = [[
                cell(row.get("line_code")), cell(row.get("label")), cell(row.get("status"), "status"),
                cell(row.get("validation"), "validation"), cell(row.get("amount"), "amount"), cell(row.get("evidence")),
            ] for row in declaration_rows]
            append_chunked_table(
                "Calendrier déclaratif",
                ["Formulaire", "Obligation", "Statut", "Contrôle", "Montant", "Echéance et source"],
                declaration_data,
                [24 * mm, 48 * mm, 25 * mm, 27 * mm, 25 * mm, document.width - 149 * mm],
                chunk_size=7,
            )

            field_rows = [row for row in rows if str(row.get("section") or "").startswith("Declaration fields")]
            field_data = []
            for row in field_rows:
                form_code = str(row.get("section") or "").replace("Declaration fields - ", "")
                source_action = " - ".join(filter(None, [str(row.get("evidence") or ""), str(row.get("next_action") or "")]))
                field_data.append([
                    cell(form_code), cell(f"{row.get('line_code', '')} - {row.get('label', '')}"),
                    cell(row.get("validation"), "validation"), cell(row.get("amount"), "amount"),
                    cell(row.get("details")), cell(source_action),
                ])
            append_chunked_table(
                "Traçabilité des champs déclaratifs",
                ["Formulaire", "Champ", "Statut", "Montant", "Valeur ou formule", "Source / action"],
                field_data,
                [22 * mm, 47 * mm, 25 * mm, 23 * mm, (document.width - 117 * mm) * 0.45, (document.width - 117 * mm) * 0.55],
                chunk_size=8,
            )

            lock_row = next((row for row in rows if row.get("line_code") == "LOCK_EVIDENCE"), {})
            lock_details = lock_row.get("details") or ""
            if lock_details.strip() == "previous=; final=":
                lock_details = "Aucune preuve de verrouillage final n'est encore enregistrée."
            story.extend([
                PageBreak(),
                Paragraph("Dates de verrouillage et responsabilité", styles["USLSection"]),
                Paragraph(clean_text(lock_details or "Aucune preuve de verrouillage enregistrée."), styles["USLNote"]),
                Paragraph(clean_text(lock_row.get("next_action") or ""), styles["USLNote"]),
            ])
        else:
            long_fields = {"label", "line_name", "field_label", "account_name", "partner_name", "details", "source_reference"}
            fixed_widths = []
            for fieldname, _label in columns:
                if fieldname == "depreciation_amount":
                    fixed_widths.append(29 * mm)
                elif fieldname in numeric_fields or fieldname in {
                    "date", "due_date", "depreciation_date", "acquisition_date",
                    "account_code", "journal_code", "line_code", "field_code", "form_code",
                    "currency", "status", "review_status",
                }:
                    fixed_widths.append(23 * mm)
                elif fieldname in long_fields:
                    fixed_widths.append(0)
                else:
                    fixed_widths.append(27 * mm)
            remaining = max(25 * mm, document.width - sum(fixed_widths))
            flexible_count = max(1, fixed_widths.count(0))
            column_widths = [width or (remaining / flexible_count) for width in fixed_widths]
            data_rows = rows or [{"label": "Aucune ligne pour les filtres sélectionnés"}]
            presentation_roles = [
                self._report_presentation_role(row)
                for row in data_rows
            ]
            table_rows = [
                [
                    cell(
                        self._report_export_row_value(row, fieldname),
                        fieldname,
                        presentation_role,
                    )
                    for fieldname, _label in columns
                ]
                for row, presentation_role in zip(
                    data_rows,
                    presentation_roles,
                    strict=True,
                )
            ]
            append_chunked_table(
                "État",
                [label for _field, label in columns],
                table_rows,
                column_widths,
                # A standard portrait statement fits up to 32 compact rows on
                # one A4 page. Longer reports still use balanced chunks so a
                # final subtotal or total is not stranded by itself.
                chunk_size=32,
                presentation_roles=presentation_roles,
                # The annual package reserves its preparation-status page for
                # narrative context; starting the statement table on a fresh
                # page prevents a split table from invading the repeated
                # legal header.
                page_break_before=self.report_type == "french_annual",
            )
            if self.report_type == "french_annual":
                ratio_rows = [
                    row
                    for row in self._management_summary_rows(
                        "executive_summary",
                    )
                    if row.get("section") == "Ratios de gestion"
                ]
                ratio_data = [
                    [
                        cell(row.get("line_name")),
                        cell(
                            row.get("metric_value"),
                            "metric_value",
                        ),
                        cell(row.get("unit")),
                        cell(row.get("details")),
                    ]
                    for row in ratio_rows
                ]
                append_chunked_table(
                    "Ratios de gestion",
                    ["Indicateur", "Valeur", "Unité", "Définition"],
                    ratio_data,
                    [
                        47 * mm,
                        22 * mm,
                        18 * mm,
                        document.width - 87 * mm,
                    ],
                    chunk_size=12,
                    page_break_before=True,
                )

        document.addPageTemplates([PageTemplate(
            id="USLAccountingReport",
            pagesize=page_size,
            frames=[Frame(
                document.leftMargin,
                document.bottomMargin,
                document.width,
                document.height,
                id="USLAccountingReportFrame",
            )],
            onPage=draw_page,
        )])
        document.build(story)
        return output.getvalue()

    def _pdf_payload(self, rows, *, return_result=False):
        """Render the shared semantic row tree through accounting_statement.v2."""
        self.ensure_one()
        metadata = self._export_metadata(len(rows))
        export_columns = self._report_export_columns(rows)
        statement_label_fields = {
            "balance_sheet": "account_name",
            "profit_loss": "line_name",
            "french_annual": "label",
            "french_balance_sheet_2024": "label",
            "french_profit_loss_2024": "label",
            "sig_caf_2024": "label",
        }
        label_candidates = (
            "label", "line_name", "field_label", "account_name",
            "partner_name", "asset_name", "details", "statement_name",
            "section",
        )
        preferred_label_field = statement_label_fields.get(self.report_type)
        label_field = (
            preferred_label_field
            if preferred_label_field
            and any(row.get(preferred_label_field) for row in rows)
            else next(
                (
                    field_name
                    for field_name in label_candidates
                    if any(row.get(field_name) for row in rows)
                ),
                export_columns[0][0],
            )
        )
        value_columns = [
            (field_name, label)
            for field_name, label in export_columns
            if field_name != label_field
        ]
        if preferred_label_field:
            value_columns = [
                (field_name, label)
                for field_name, label in value_columns
                if field_name in MONETARY_REPORT_FIELDS
            ]
        if not value_columns:
            value_columns = [("__value__", "Valeur")]

        def format_amount(value):
            try:
                amount = Decimal(str(value or 0))
                amount /= Decimal(str(metadata["display_unit_factor"] or 1))
                decimal_places = metadata["amount_decimal_places"]
                amount = amount.quantize(
                    Decimal(1).scaleb(-decimal_places),
                    rounding=ROUND_HALF_UP,
                )
                if amount == 0:
                    amount = abs(amount)
                return (
                    f"{amount:,.{decimal_places}f}"
                    .replace(",", " ")
                    .replace(".", ",")
                )
            except (ArithmeticError, TypeError, ValueError):
                return str(value or "")

        def display_value(row, field_name):
            if field_name == "__value__":
                return ""
            value = self._report_export_row_value(row, field_name)
            if value in (None, "", False):
                return ""
            return (
                format_amount(value)
                if field_name in MONETARY_REPORT_FIELDS
                else str(value)
            )

        def semantic_kind(field_name):
            if field_name in MONETARY_REPORT_FIELDS:
                return "amount"
            if field_name in DATE_REPORT_FIELDS:
                return "date"
            if field_name in {
                "account_code", "journal_code", "line_code", "field_code",
                "form_code", "currency",
            }:
                return "code"
            if field_name in {
                "status", "state", "validation", "review_status",
            }:
                return "status"
            if field_name in {"quantity", "record_count"}:
                return "quantity"
            return "text"

        semantic_columns = [{
            "key": "label",
            "label": dict(export_columns).get(
                label_field,
                self._report_client_label_column(),
            ),
            "kind": "label",
        }]
        for sequence, (field_name, label) in enumerate(value_columns, start=1):
            semantic_columns.append({
                "key": f"value_{sequence}",
                "label": label,
                "kind": semantic_kind(field_name),
            })

        statement_titles = {
            "bilan_actif": "Actif",
            "bilan_passif": "Passif",
            "compte_resultat": "Compte de résultat",
            "sig_caf": "SIG et CAF",
        }
        statement_reports = {
            "balance_sheet", "profit_loss", "french_annual",
            "french_balance_sheet_2024", "french_profit_loss_2024",
            "sig_caf_2024",
        }
        split_fields = {
            "tax_report": ("report_section", "section"),
            "tax_report_group_account_tax": ("account_code",),
            "tax_report_group_tax_account": ("tax_name",),
            "ec_sales_list": ("period", "country_code"),
            "oss_sales": ("country_code", "tax_treatment"),
            "oss_imports": ("country_code", "tax_treatment"),
            "bank_reconciliation": ("journal_code",),
            "currency_report": ("currency", "section"),
            "cash_flow": ("section",),
            "executive_summary": ("section",),
            "fixed_assets": ("account_code", "asset_category"),
            "fixed_asset_group_account": ("account_code",),
            "depreciation_schedule": ("asset_name",),
            "deferred_schedule": ("deferred_account_code", "section"),
            "french_tax_package": ("form_code", "section"),
            "closing_package": ("section",),
        }
        layout_variants = {
            "balance_sheet": "split_statement",
            "french_balance_sheet_2024": "split_statement",
            "general_ledger": "ledger",
            "partner_ledger": "ledger",
            "customer_statement": "ledger",
            "open_items": "ledger",
            "aged_receivable": "aging",
            "aged_payable": "aging",
            "executive_summary": "metrics",
            "cash_flow": "metrics",
            "depreciation_schedule": "schedule",
            "deferred_schedule": "schedule",
            "fixed_assets": "schedule",
            "fixed_asset_group_account": "schedule",
            "french_tax_package": "evidence",
            "closing_package": "evidence",
            "analytic_pivot": "pivot",
        }

        sections = {}
        pending_summaries = {}
        current_key = None
        controls = []

        def get_section(row):
            nonlocal current_key
            statement_key = row.get("statement_key")
            if self.report_type in statement_reports and statement_key:
                key = str(statement_key)
                title = statement_titles.get(key, row.get("statement_name") or key)
                break_before = key == "bilan_passif"
                current_key = key
                return key, str(title), break_before, False
            is_root_group = (
                row.get("is_group") in (True, "true")
                and int(row.get("row_level") or 0) == 0
                and row.get("group_key")
            )
            if is_root_group:
                key = str(row["group_key"])
                title = str(row.get("label") or self._report_type_label())
                current_key = key
                return key, title, False, True
            if current_key and row.get("parent_group_key"):
                section = sections.get(current_key)
                return (
                    current_key,
                    section["title"] if section else self._report_type_label(),
                    False,
                    False,
                )
            for field_name in split_fields.get(self.report_type, ()):
                value = row.get(field_name)
                if value:
                    key = f"{field_name}|{value}"
                    current_key = key
                    return key, str(value), False, False
            current_key = current_key or "main"
            return current_key, "", False, False

        for row in rows:
            role = self._report_presentation_role(row)
            row_label = (
                self._report_export_row_value(row, label_field)
                or self._report_export_row_value(row, "label")
                or row.get("account_name")
                or row.get("partner_name")
                or ""
            )
            values = {"label": str(row_label).strip()}
            for sequence, (field_name, _label) in enumerate(value_columns, start=1):
                values[f"value_{sequence}"] = display_value(row, field_name)
            rendered_row = {
                "role": role,
                "level": min(max(int(row.get("row_level") or row.get("level") or 0), 0), 6),
                "values": values,
            }
            if role in {"section", "group"}:
                rendered_row["keep_with_next"] = True
            if role == "control":
                control_value = next(
                    (
                        values[f"value_{sequence}"]
                        for sequence in range(1, len(value_columns) + 1)
                        if values[f"value_{sequence}"]
                    ),
                    "0",
                )
                controls.append({
                    "label": values["label"],
                    "value": control_value,
                    "status": row.get("control_status", "neutral"),
                })
                continue
            key, section_title, break_before, is_summary = get_section(row)
            section = sections.setdefault(key, {
                "key": f"section_{len(sections) + 1}",
                "title": section_title,
                "break_before": bool(break_before),
                "continuation_label": (
                    f"{section_title} — suite" if section_title else "Suite"
                ),
                "rows": [],
            })
            if (
                self.report_type == "balance_sheet"
                and role == "section"
                and str(row_label).strip() == section_title
            ):
                continue
            if is_summary and self.report_type not in statement_reports:
                rendered_row["role"] = "subtotal"
                rendered_row["values"]["label"] = f"Total — {section_title}"
                pending_summaries[key] = rendered_row
                continue
            section["rows"].append(rendered_row)

        for key, summary in pending_summaries.items():
            sections[key]["rows"].append(summary)
        if not sections:
            sections["main"] = {
                "key": "section_1",
                "title": "",
                "break_before": False,
                "continuation_label": "Suite",
                "rows": [{
                    "role": "empty",
                    "values": {
                        **{"label": "Aucune donnée pour le périmètre sélectionné."},
                        **{
                            f"value_{sequence}": ""
                            for sequence in range(1, len(value_columns) + 1)
                        },
                    },
                }],
            }

        companies = self._selected_companies()
        generated_on = self._display_export_date(fields.Date.context_today(self))
        currency = self.company_id.currency_id.with_context(lang="fr_FR")
        currency_units = currency.currency_unit_label or currency.name
        document_unit_label = {
            "units": currency_units,
            "thousands": (
                "Milliers d’euros"
                if currency.name == "EUR"
                else f"Milliers de {currency_units.lower()}"
            ),
            "millions": (
                "Millions d’euros"
                if currency.name == "EUR"
                else f"Millions de {currency_units.lower()}"
            ),
        }.get(self.display_unit, currency_units)
        context = [
            f"Société : {', '.join(companies.mapped('display_name'))}",
            (
                f"Période : {self._display_export_date(metadata['date_from'])}"
                f" – {self._display_export_date(metadata['date_to'])}"
            ),
            (
                f"Écritures comptabilisées et brouillons au {generated_on}"
                if self.target_move == "all"
                else f"Écritures comptabilisées au {generated_on}"
            ),
            (
                f"Unité : {document_unit_label}"
                f" · Arrondi : {metadata['amount_rounding_label']}"
            ),
        ]
        if self.comparison_mode != "none":
            context.append(
                "Comparaison : "
                f"{self._display_export_date(metadata['comparison_date_from'])}"
                " – "
                f"{self._display_export_date(metadata['comparison_date_to'])}"
            )
        basis_note = (
            "Document préparatoire produit à partir des écritures et contrôles "
            "du périmètre sélectionné. Il ne constitue ni une attestation "
            "professionnelle ni, à lui seul, une annexe légale."
            if self.report_type == "french_annual"
            else (
                "Dossier de préparation et de revue de clôture. Il ne vaut "
                "ni déclaration déposée ni validation par un professionnel externe."
                if self.report_type == "closing_package"
                else (
                    "État produit à partir de la session comptable affichée. "
                    "Les filtres, unités et règles d’arrondi font partie de sa traçabilité."
                )
            )
        )
        locale = "fr_FR" if self.company_id.country_code == "FR" else (
            "fr_FR"
            if (self.company_id.partner_id.lang or "").startswith("fr")
            else "en_US"
        )
        company_payload, assets = self.company_id._usl_document_renderer_company_payload(locale)
        company_payload.update({
            "primary_color": metadata["document"]["primary_color"],
            "footer_label": metadata["document"]["footer_label"],
        })
        front_matter = None
        if self.report_type == "french_annual":
            front_matter = {
                "eyebrow": "ÉTATS FINANCIERS FRANÇAIS",
                "title": metadata["report_name"],
                "status": "Document préparatoire — non attesté",
                "lead": (
                    "Présentation professionnelle des comptes annuels issue "
                    "des écritures comptables du périmètre sélectionné."
                ),
                "contents": [
                    "Situation de préparation",
                    "Bilan — Actif",
                    "Bilan — Passif",
                    "Compte de résultat",
                    "Soldes intermédiaires de gestion et CAF",
                    "Ratios et contrôles de cohérence",
                ],
                "facts": [
                    {
                        "label": "Périmètre",
                        "value": companies.mapped("display_name")[0]
                        if len(companies) == 1
                        else f"{len(companies)} sociétés",
                        "status": "neutral",
                    },
                    {
                        "label": "Préparation",
                        "value": "À faire valider avant diffusion externe",
                        "status": "warning",
                    },
                ],
            }
        elif self.report_type == "closing_package":
            status_row = next(
                (row for row in rows if row.get("line_code") == "CLOSE_STATUS"),
                {},
            )
            readiness = str(status_row.get("validation") or "À examiner")
            front_matter = {
                "eyebrow": "REVUE DE CLÔTURE",
                "title": metadata["report_name"],
                "status": f"Situation : {readiness}",
                "lead": (
                    "Synthèse contrôlée des travaux, déclarations, actions "
                    "non résolues, justificatifs et dates de verrouillage."
                ),
                "contents": [
                    "Situation de clôture",
                    "Synthèse des contrôles",
                    "Calendrier déclaratif",
                    "Actions non résolues",
                    "Références des justificatifs",
                    "Conclusion et dates de verrouillage",
                ],
                "facts": [
                    {
                        "label": "Contrôles",
                        "value": str(status_row.get("record_count") or "0"),
                        "status": "neutral",
                    },
                    {
                        "label": "Conclusion",
                        "value": readiness,
                        "status": (
                            "success"
                            if readiness in {"ready", "validated", "done"}
                            else "warning"
                        ),
                    },
                ],
            }
        template = self.env.ref(
            "usl_document_templates.template_accounting_statement_v2",
        )
        result = self.env["usl.document.renderer"].render(
            template,
            company_payload,
            {
                "title": metadata["report_name"],
                "reference": (
                    f"Exercice {fields.Date.to_date(metadata['date_from']).year}"
                    f"–{fields.Date.to_date(metadata['date_to']).year}"
                ),
                "date": (
                    f"{self._display_export_date(metadata['date_from'])}"
                    " – "
                    f"{self._display_export_date(metadata['date_to'])}"
                ),
                "layout_variant": layout_variants.get(self.report_type, "statement"),
                "orientation": (
                    "landscape"
                    if len(semantic_columns) > 5
                    or self.report_type in {"analytic_pivot", "french_tax_package"}
                    else "portrait"
                ),
                "columns": semantic_columns,
                "sections": list(sections.values()),
                "context": context,
                "controls": controls,
                "basis_note": basis_note,
                **({"front_matter": front_matter} if front_matter else {}),
            },
            locale,
            assets,
        )
        return result if return_result else result["pdf"]

    def _pdf_payload_v1(self, rows, *, return_result=False):
        """Render the canonical report rows without changing their business truth."""
        self.ensure_one()
        metadata = self._export_metadata(len(rows))
        columns = self._report_export_columns(rows)
        statement_label_fields = {
            "balance_sheet": "account_name",
            "profit_loss": "line_name",
            "french_annual": "label",
            "french_balance_sheet_2024": "label",
            "french_profit_loss_2024": "label",
            "sig_caf_2024": "label",
        }
        label_candidates = (
            "label",
            "line_name",
            "field_label",
            "account_name",
            "partner_name",
            "asset_name",
            "details",
            "statement_name",
            "section",
        )
        preferred_label_field = statement_label_fields.get(
            self.report_type,
        )
        label_field = (
            preferred_label_field
            if preferred_label_field
            and any(row.get(preferred_label_field) for row in rows)
            else next(
                (
                    field_name
                    for field_name in label_candidates
                    if any(
                        field_name == key
                        for key, _label in columns
                    )
                ),
                columns[0][0],
            )
        )
        value_columns = [
            (field_name, label)
            for field_name, label in columns
            if field_name != label_field
        ]
        if preferred_label_field:
            value_columns = [
                (field_name, label)
                for field_name, label in value_columns
                if field_name in MONETARY_REPORT_FIELDS
            ]
        if not value_columns:
            value_columns = [("__value__", "Valeur")]

        def display_value(row, field_name):
            if field_name == "__value__":
                return ""
            value = self._report_export_row_value(row, field_name)
            if value in (None, "", False):
                return ""
            if field_name not in MONETARY_REPORT_FIELDS:
                return str(value)
            try:
                amount = Decimal(str(value))
                amount /= Decimal(
                    str(metadata["display_unit_factor"] or 1),
                )
                decimal_places = metadata["amount_decimal_places"]
                amount = amount.quantize(
                    Decimal(1).scaleb(-decimal_places),
                    rounding=ROUND_HALF_UP,
                )
                if amount == 0:
                    amount = abs(amount)
                return (
                    f"{amount:,.{decimal_places}f}"
                    .replace(",", " ")
                    .replace(".", ",")
                )
            except (ArithmeticError, TypeError, ValueError):
                return str(value)

        rendered_rows = []
        previous_section = None
        for row in rows:
            section = str(row.get("section") or "").strip()
            if (
                self.report_type in {"balance_sheet", "profit_loss"}
                and section
                and section != previous_section
            ):
                rendered_rows.append({
                    "label": section,
                    "level": 0,
                    "emphasis": "section",
                    "values": ["" for _column in value_columns],
                })
                previous_section = section
            role = self._report_presentation_role(row)
            try:
                level = int(row.get("row_level") or row.get("level") or 0)
            except (TypeError, ValueError):
                level = 0
            if self.report_type in {"balance_sheet", "profit_loss"}:
                level = max(level, 1)
            rendered_rows.append({
                "label": str(
                    self._report_export_row_value(row, label_field) or ""
                ),
                "level": min(max(level, 0), 6),
                "emphasis": role,
                "values": [
                    display_value(row, field_name)
                    for field_name, _label in value_columns
                ],
            })
        if not rendered_rows:
            rendered_rows.append({
                "label": "Aucune donnée pour le périmètre sélectionné.",
                "level": 0,
                "emphasis": "empty",
                "values": ["" for _column in value_columns],
            })

        companies = self._selected_companies()
        generated_on = self._display_export_date(
            fields.Date.context_today(self),
        )
        currency = self.company_id.currency_id.with_context(lang="fr_FR")
        currency_units = currency.currency_unit_label or currency.name
        document_unit_label = {
            "units": currency_units,
            "thousands": (
                "Milliers d’euros"
                if currency.name == "EUR"
                else f"Milliers de {currency_units.lower()}"
            ),
            "millions": (
                "Millions d’euros"
                if currency.name == "EUR"
                else f"Millions de {currency_units.lower()}"
            ),
        }.get(self.display_unit, currency_units)
        filters = [
            f"Société : {', '.join(companies.mapped('display_name'))}",
            (
                f"Période : {self._display_export_date(metadata['date_from'])}"
                f" – {self._display_export_date(metadata['date_to'])}"
            ),
            (
                f"Écritures comptabilisées et brouillons au {generated_on}"
                if self.target_move == "all"
                else f"Écritures comptabilisées au {generated_on}"
            ),
            (
                f"Unité : {document_unit_label}"
                f" · Arrondi : {metadata['amount_rounding_label']}"
            ),
        ]
        if self.comparison_mode != "none":
            filters.append(
                "Comparaison : "
                f"{self._display_export_date(metadata['comparison_date_from'])}"
                " – "
                f"{self._display_export_date(metadata['comparison_date_to'])}"
            )
        basis_note = (
            "Document préparatoire produit à partir des écritures et contrôles "
            "du périmètre sélectionné. Il ne constitue ni une attestation "
            "professionnelle ni, à lui seul, une annexe légale."
            if self.report_type == "french_annual"
            else (
                "Dossier de préparation et de revue de clôture. Il ne vaut "
                "ni déclaration déposée ni validation par un professionnel "
                "externe."
                if self.report_type == "closing_package"
                else (
                    "État produit à partir des lignes de la session comptable "
                    "affichée. Les filtres, unités et règles d’arrondi ci-dessus "
                    "font partie de sa traçabilité."
                )
            )
        )
        if metadata["amount_decimal_places"] == 0:
            basis_note += (
                " Les montants sont arrondis individuellement ; les totaux "
                "affichés peuvent donc présenter un écart d’un euro."
            )
        locale = "fr_FR" if self.company_id.country_code == "FR" else (
            "fr_FR"
            if (self.company_id.partner_id.lang or "").startswith("fr")
            else "en_US"
        )
        company_payload, assets = (
            self.company_id._usl_document_renderer_company_payload(locale)
        )
        company_payload.update({
            "primary_color": metadata["document"]["primary_color"],
            "footer_label": metadata["document"]["footer_label"],
        })
        template = self.env.ref(
            "usl_document_templates.template_accounting_statement_v1",
        )
        result = self.env["usl.document.renderer"].render(
            template,
            company_payload,
            {
                "title": metadata["report_name"],
                "reference": (
                    f"Exercice {fields.Date.to_date(metadata['date_from']).year}"
                    f"–{fields.Date.to_date(metadata['date_to']).year}"
                ),
                "date": (
                    f"{self._display_export_date(metadata['date_from'])}"
                    " – "
                    f"{self._display_export_date(metadata['date_to'])}"
                ),
                "orientation": (
                    "landscape"
                    if len(value_columns) > 4
                    else "portrait"
                ),
                "label_column": dict(columns).get(
                    label_field,
                    self._report_client_label_column(),
                ),
                "columns": [label for _field_name, label in value_columns],
                "filters": filters,
                "rows": rendered_rows,
                "basis_note": basis_note,
            },
            locale,
            assets,
        )
        return result if return_result else result["pdf"]

    def _report_rows(self):
        self.ensure_one()
        current_rows = self._raw_report_rows(
            self.date_from,
            self.date_to,
        )
        current_rows = self._search_report_rows(current_rows)
        current_rows = self._group_report_rows(current_rows)
        comparison_rows = []
        if self.comparison_mode != "none":
            comparison_rows = self._raw_report_rows(
                self.comparison_date_from,
                self.comparison_date_to,
            )
            comparison_rows = self._search_report_rows(
                comparison_rows,
            )
            comparison_rows = self._group_report_rows(
                comparison_rows,
            )
        rows = self._attach_comparison_values(
            current_rows,
            comparison_rows,
        )
        rows = self._append_shared_control_rows(rows)
        return self._hide_zero_account_rows(rows)

    def _append_shared_control_rows(self, rows):
        """Add exact report controls once for screen, PDF, and readable XLSX."""
        self.ensure_one()
        detail_rows = [
            row
            for row in rows
            if row.get("is_group") not in (True, "true")
            and self._report_presentation_role(row) == "detail"
            and row.get("hierarchy_kind") not in {"pcg_group", "account"}
        ]
        additions = []
        if self.report_type in {"trial_balance", "journal_report"}:
            total_debit = sum(
                (_amount(row.get("debit")) for row in detail_rows),
                Decimal("0.00"),
            )
            total_credit = sum(
                (_amount(row.get("credit")) for row in detail_rows),
                Decimal("0.00"),
            )
            additions.extend([
                {
                    "label": "Total débit",
                    "debit": _amount_text(total_debit),
                    "presentation_role": "total",
                    "row_level": 0,
                },
                {
                    "label": "Total crédit",
                    "credit": _amount_text(total_credit),
                    "presentation_role": "total",
                    "row_level": 0,
                },
                {
                    "label": "Contrôle débit − crédit",
                    "closing_balance": _amount_text(
                        total_debit - total_credit,
                    ),
                    "balance": _amount_text(total_debit - total_credit),
                    "presentation_role": "control",
                    "control_status": (
                        "success"
                        if total_debit == total_credit
                        else "danger"
                    ),
                    "row_level": 0,
                },
            ])
        elif self.report_type == "balance_sheet":
            totals = {
                row.get("line_code"): _amount(row.get("amount"))
                for row in rows
                if row.get("line_code") in {
                    "ACTIF_TOTAL", "PASSIF_TOTAL",
                }
            }
            difference = totals.get("ACTIF_TOTAL", Decimal("0.00")) - totals.get(
                "PASSIF_TOTAL", Decimal("0.00"),
            )
            additions.append({
                "statement_key": "bilan_passif",
                "statement_side": "Passif",
                "label": "Contrôle d’équilibre Actif − Passif",
                "amount": _amount_text(difference),
                "presentation_role": "control",
                "control_status": "success" if difference == 0 else "danger",
                "row_level": 0,
            })
        elif self.report_type in {
            "customer_statement",
            "open_items",
            "aged_receivable",
            "aged_payable",
        }:
            value_field = (
                "total"
                if self.report_type in {"aged_receivable", "aged_payable"}
                else "presented_residual"
                if self.report_type == "open_items"
                else "residual"
            )
            total = sum(
                (_amount(row.get(value_field)) for row in detail_rows),
                Decimal("0.00"),
            )
            label = {
                "customer_statement": "Total du relevé client",
                "open_items": "Total des écritures ouvertes",
                "aged_receivable": "Total clients",
                "aged_payable": "Total fournisseurs",
            }[self.report_type]
            additions.append({
                "label": label,
                value_field: _amount_text(total),
                "presentation_role": "total",
                "row_level": 0,
            })
        elif self.report_type in {
            "fixed_assets",
            "fixed_asset_group_account",
        }:
            additions.append({
                "label": "Total des immobilisations",
                "original_value": _amount_text(sum(
                    (_amount(row.get("original_value")) for row in detail_rows),
                    Decimal("0.00"),
                )),
                "depreciation_amount": _amount_text(sum(
                    (_amount(row.get("depreciation_amount")) for row in detail_rows),
                    Decimal("0.00"),
                )),
                "imported_period_net_value": _amount_text(sum(
                    (
                        _amount(row.get("imported_period_net_value"))
                        for row in detail_rows
                    ),
                    Decimal("0.00"),
                )),
                "presentation_role": "total",
                "row_level": 0,
            })
        return [*rows, *additions]

    def _hide_zero_account_rows(self, rows):
        """Hide empty account leaves and their now-empty account branches."""
        self.ensure_one()
        if (
            not self.hide_zero_accounts
            or self.report_type not in ZERO_ACCOUNT_FILTER_REPORT_TYPES
        ):
            return rows
        hidden_group_keys = set()
        visible_rows = []
        for row in rows:
            parent_key = str(row.get("parent_group_key") or "")
            if parent_key in hidden_group_keys:
                if row.get("is_group") in (True, "true"):
                    hidden_group_keys.add(str(row.get("group_key") or ""))
                continue
            if self._is_zero_report_row(row):
                if row.get("is_group") in (True, "true"):
                    hidden_group_keys.add(str(row.get("group_key") or ""))
                continue
            visible_rows.append(row)
        retained_parent_keys = set()
        pruned_rows = []
        for row in reversed(visible_rows):
            group_key = str(row.get("group_key") or "")
            if (
                row.get("hierarchy_kind") == "pcg_group"
                and group_key not in retained_parent_keys
            ):
                continue
            if (
                row.get("hierarchy_kind") == "statement"
                and group_key not in retained_parent_keys
                and self._report_presentation_role(row) == "detail"
                and self._row_monetary_values_are_zero(row)
            ):
                continue
            pruned_rows.append(row)
            parent_key = str(row.get("parent_group_key") or "")
            if parent_key:
                retained_parent_keys.add(parent_key)
        return list(reversed(pruned_rows))

    def _is_zero_report_row(self, row):
        self.ensure_one()
        hierarchy_kind = row.get("hierarchy_kind")
        is_account_row = (
            hierarchy_kind == "account"
            or (
                self.report_type == "trial_balance"
                and bool(row.get("account_code"))
            )
            or (
                self.report_type in {
                    "tax_report",
                    "fixed_asset_group_account",
                }
                and bool(row.get("account_code"))
            )
            or (
                self.group_by == "account"
                and row.get("is_group") in (True, "true")
            )
        )
        is_zero_presentational_row = (
            row.get("is_group") not in (True, "true")
            and self._report_presentation_role(row) not in {
                "section",
                "total",
                "control",
            }
        )
        return (
            (is_account_row or is_zero_presentational_row)
            and self._row_monetary_values_are_zero(row)
        )

    @staticmethod
    def _row_monetary_values_are_zero(row):
        monetary_values = [
            _amount(row.get(field_name))
            for field_name in MONETARY_REPORT_FIELDS
            if row.get(field_name) not in (None, "")
        ]
        return bool(monetary_values) and all(
            abs(value) < Decimal("0.005")
            for value in monetary_values
        )

    def _raw_report_rows(self, date_from, date_to):
        self.ensure_one()
        rows = []
        for company in self._selected_companies():
            clone = self.env[self._name].with_company(company).create(
                self._report_clone_values(
                    company,
                    date_from,
                    date_to,
                ),
            )
            try:
                company_rows = clone._report_rows_single()
            finally:
                clone.sudo().unlink()
            for row in company_rows:
                row = dict(row)
                row.update({
                    "report_company_id": company.id,
                    "report_company_name": company.display_name,
                    "report_currency_id": company.currency_id.id,
                    "report_currency": company.currency_id.name,
                })
                rows.append(row)
        if (
            len(self._selected_companies()) > 1
            and self.report_type in MULTI_COMPANY_AGGREGATE_KEYS
        ):
            return self._aggregate_company_rows(rows)
        return rows

    def _aggregate_company_rows(self, rows):
        """Combine same-currency summary rows and retain their contributions."""
        self.ensure_one()
        key_fields = MULTI_COMPANY_AGGREGATE_KEYS[self.report_type]
        buckets = {}
        for row in rows:
            key = tuple(str(row.get(field_name) or "") for field_name in key_fields)
            bucket = buckets.setdefault(key, {"rows": [], "template": dict(row)})
            bucket["rows"].append(row)
        aggregated = []
        for bucket in buckets.values():
            company_rows = bucket["rows"]
            row = bucket["template"]
            row.update({
                "report_company_id": self.company_id.id,
                "report_company_ids": sorted({
                    int(company_row["report_company_id"])
                    for company_row in company_rows
                }),
                "report_company_name": ", ".join(
                    sorted({
                        company_row["report_company_name"]
                        for company_row in company_rows
                    }),
                ),
                "company_contributions": [
                    {
                        "company_id": company_row["report_company_id"],
                        "company_name": company_row["report_company_name"],
                        "values": {
                            field_name: company_row.get(field_name)
                            for field_name in self._summable_report_fields()
                            if company_row.get(field_name) not in (None, "")
                        },
                    }
                    for company_row in company_rows
                ],
            })
            for field_name in self._summable_report_fields():
                values = [
                    _amount(company_row.get(field_name))
                    for company_row in company_rows
                    if company_row.get(field_name) not in (None, "")
                ]
                if values:
                    row[field_name] = _amount_text(sum(values))
            counts = [
                int(company_row.get("move_line_count") or 0)
                for company_row in company_rows
                if company_row.get("move_line_count") not in (None, "")
            ]
            if counts:
                row["move_line_count"] = str(sum(counts))
            if any(company_row.get("account_breakdown") for company_row in company_rows):
                row["account_breakdown"] = self._aggregate_account_breakdown(
                    company_rows,
                )
            aggregated.append(row)
        return aggregated

    @staticmethod
    def _aggregate_account_breakdown(company_rows):
        accounts = {}
        for company_row in company_rows:
            company_id = int(company_row["report_company_id"])
            company_name = company_row["report_company_name"]
            for account in company_row.get("account_breakdown") or []:
                key = str(account.get("account_code") or "")
                bucket = accounts.setdefault(key, {
                    **account,
                    "amount": "0.00",
                    "move_line_count": 0,
                })
                bucket["amount"] = _amount_text(
                    _amount(bucket["amount"]) + _amount(account.get("amount")),
                )
                bucket["move_line_count"] += int(
                    account.get("move_line_count") or 0,
                )
                bucket.setdefault("company_contributions", []).append({
                    "company_id": company_id,
                    "company_name": company_name,
                    "values": {
                        "amount": account.get("amount") or "0.00",
                    },
                })
        return [accounts[key] for key in sorted(accounts)]

    def _report_clone_values(self, company, date_from, date_to):
        journals = self.journal_ids.filtered(
            lambda journal: not journal.company_id
            or journal.company_id == company,
        )
        accounts = self.account_ids.filtered(
            lambda account: company in account.company_ids,
        )
        values = {
            "report_type": self.report_type,
            "company_id": company.id,
            "company_ids": [Command.set([company.id])],
            "period_preset": "custom",
            "date_from": date_from,
            "date_to": date_to,
            "comparison_mode": "none",
            "target_move": self.target_move,
            "display_unit": self.display_unit,
            "amount_rounding": self.amount_rounding,
            "hide_zero_accounts": self.hide_zero_accounts,
            "export_format": self.export_format,
            "fec_test_mode": self.fec_test_mode,
            "journal_ids": [Command.set(journals.ids)],
            "account_ids": [Command.set(accounts.ids)],
            "partner_ids": [Command.set(self.partner_ids.ids)],
            "group_by": "none",
            "preview_limit": self.preview_limit,
        }
        if self.analytic_plan_ids:
            values["analytic_plan_ids"] = [
                Command.set(self.analytic_plan_ids.ids),
            ]
        if self.analytic_account_ids:
            analytic_accounts = self.analytic_account_ids.filtered(
                lambda account: not account.company_id
                or account.company_id == company,
            )
            values["analytic_account_ids"] = [
                Command.set(analytic_accounts.ids),
            ]
        return values

    def _search_report_rows(self, rows):
        self.ensure_one()
        query = (self.search_text or "").strip().casefold()
        if not query:
            return rows
        result = []
        for row in rows:
            searchable = " ".join(
                str(value)
                for key, value in row.items()
                if value not in (None, False)
                and not key.startswith("source_")
                and not key.endswith("_id")
            ).casefold()
            if query in searchable:
                result.append(row)
        return result

    def _group_report_rows(self, rows):
        self.ensure_one()
        if self.report_type == "balance_sheet":
            return self._balance_sheet_hierarchy_rows(rows)
        if self.report_type == "journal_report" and self.group_by == "journal":
            return self._journal_hierarchy_rows(rows)
        if self.report_type == "partner_ledger" and self.group_by == "partner":
            return self._partner_account_hierarchy_rows(rows)
        if self.report_type == "open_items" and self.group_by == "partner":
            return self._open_items_hierarchy_rows(rows)
        if self.report_type == "deferred_schedule" and self.group_by == "account":
            return self._deferred_hierarchy_rows(rows)
        if self.group_by == "none":
            return rows
        groups = {}
        for row in rows:
            group_key, label, group_values = self._report_group(row)
            bucket = groups.setdefault(
                group_key,
                {
                    "label": label,
                    "values": group_values,
                    "rows": [],
                },
            )
            bucket["rows"].append(row)
        result = []
        for group_key, bucket in groups.items():
            children = bucket["rows"]
            summary_code = (
                FRENCH_PROFIT_LOSS_SECTION_TOTALS.get(
                    bucket["label"],
                )
                if self.report_type in {"profit_loss", "french_annual"}
                else None
            ) or {
                "bilan_actif": "ACTIF_TOTAL",
                "bilan_passif": "PASSIF_TOTAL",
                "compte_resultat": "CR_RESULTAT_NET",
                "sig_caf": "SIG_CAPACITE_AUTOFINANCEMENT",
            }.get(children[0].get("statement_key") if children else "")
            summary_row = next(
                (
                    row
                    for row in children
                    if summary_code and row.get("line_code") == summary_code
                ),
                None,
            )
            group_row = {
                **bucket["values"],
                "is_group": "true",
                "row_level": 0,
                "group_key": group_key,
                "label": bucket["label"],
                "record_count": str(len(children)),
            }
            statement_keys = {
                child.get("statement_key")
                for child in children
                if child.get("statement_key")
            }
            if len(statement_keys) == 1:
                group_row["statement_key"] = statement_keys.pop()
            account_codes = sorted({
                code
                for child in children
                for code in self._row_account_codes(child)
            })
            if account_codes:
                group_row["drilldown_account_codes"] = ",".join(account_codes)
            account_prefixes = sorted({
                prefix.strip()
                for child in children
                for prefix in (
                    child.get("drilldown_account_prefixes") or ""
                ).split(",")
                if prefix.strip()
            })
            if account_prefixes:
                group_row["drilldown_account_prefixes"] = ",".join(
                    account_prefixes,
                )
            for field_name in self._summable_report_fields():
                if (
                    self.report_type in {"profit_loss", "french_annual"}
                    and not summary_row
                ):
                    continue
                if (
                    summary_row
                    and summary_row.get(field_name) not in (None, "")
                ):
                    group_row[field_name] = summary_row[field_name]
                    continue
                values = [
                    _amount(row.get(field_name))
                    for row in children
                    if row.get(field_name) not in (None, "")
                ]
                if values:
                    group_row[field_name] = _amount_text(sum(values))
            if (
                self.report_type == "general_ledger"
                and self.group_by == "account"
                and children
            ):
                group_row["opening_balance"] = (
                    children[0].get("opening_balance") or "0.00"
                )
                group_row["running_balance"] = (
                    children[-1].get("running_balance") or "0.00"
                )
            result.append(group_row)
            for row in children:
                result.extend(
                    self._statement_hierarchy_rows(
                        row,
                        section_group_key=group_key,
                    ),
                )
        return result

    def _shared_summary_row(
        self,
        label,
        children,
        *,
        role,
        level,
        parent_group_key="",
        group_key="",
        fields_to_sum=None,
    ):
        """Build a shared exact summary without presentation-side arithmetic."""
        fields_to_sum = fields_to_sum or self._summable_report_fields()
        row = {
            "label": label,
            "is_group": "true" if group_key else "false",
            "row_level": level,
            "presentation_role": role,
        }
        if parent_group_key:
            row["parent_group_key"] = parent_group_key
        if group_key:
            row["group_key"] = group_key
        for field_name in fields_to_sum:
            values = [
                _amount(child.get(field_name))
                for child in children
                if child.get(field_name) not in (None, "")
                and self._report_presentation_role(child) == "detail"
            ]
            if values:
                row[field_name] = _amount_text(sum(values, Decimal("0.00")))
        return row

    def _journal_hierarchy_rows(self, rows):
        labels = {
            "sale": "Journaux de ventes",
            "purchase": "Journaux d’achats",
            "bank": "Journaux de banque",
            "cash": "Journaux de caisse",
            "general": "Opérations diverses",
        }
        grouped = {}
        for row in rows:
            grouped.setdefault(row.get("journal_type") or "other", []).append(row)
        result = []
        for journal_type, children in grouped.items():
            title = labels.get(journal_type, "Autres journaux")
            group_key = f"journal-type|{journal_type}"
            result.append(self._shared_summary_row(
                title,
                children,
                role="section",
                level=0,
                group_key=group_key,
                fields_to_sum={"debit", "credit", "balance"},
            ))
            result.extend({
                **child,
                "label": (
                    f"{child.get('journal_code') or ''} — "
                    f"{child.get('journal_name') or ''}"
                ).strip(" —"),
                "is_group": "false",
                "parent_group_key": group_key,
                "row_level": 1,
                "presentation_role": "detail",
            } for child in children)
        return result

    def _partner_account_hierarchy_rows(self, rows):
        partners = {}
        for row in rows:
            partners.setdefault(row.get("partner_name") or "Partenaire non renseigné", []).append(row)
        result = []
        for partner_sequence, (partner_name, partner_rows) in enumerate(partners.items(), start=1):
            partner_key = f"partner|{partner_sequence}"
            result.append(self._shared_summary_row(
                partner_name,
                partner_rows,
                role="section",
                level=0,
                group_key=partner_key,
                fields_to_sum={"debit", "credit", "balance"},
            ))
            accounts = {}
            for row in partner_rows:
                account_key = (
                    row.get("account_code") or "",
                    row.get("account_name") or "Compte non renseigné",
                )
                accounts.setdefault(account_key, []).append(row)
            for account_sequence, ((code, name), account_rows) in enumerate(accounts.items(), start=1):
                account_key = f"{partner_key}|account|{account_sequence}"
                account_label = f"{code} — {name}".strip(" —")
                account_group = self._shared_summary_row(
                    account_label,
                    account_rows,
                    role="group",
                    level=1,
                    parent_group_key=partner_key,
                    group_key=account_key,
                    fields_to_sum={"debit", "credit", "balance"},
                )
                account_group["opening_balance"] = account_rows[0].get("opening_balance") or "0.00"
                account_group["running_balance"] = account_rows[-1].get("running_balance") or "0.00"
                result.append(account_group)
                result.extend({
                    **child,
                    "is_group": "false",
                    "parent_group_key": account_key,
                    "row_level": 2,
                    "presentation_role": "detail",
                } for child in account_rows)
                result.append({
                    **account_group,
                    "label": f"Clôture — {account_label}",
                    "is_group": "false",
                    "group_key": "",
                    "presentation_role": "subtotal",
                    "row_level": 1,
                })
        return result

    def _open_items_hierarchy_rows(self, rows):
        sections = (
            ("asset_receivable", "Clients"),
            ("liability_payable", "Fournisseurs"),
        )
        result = []
        for account_type, section_label in sections:
            section_rows = [
                row for row in rows if row.get("account_type") == account_type
            ]
            if not section_rows:
                continue
            section_key = f"open-items|{account_type}"
            result.append(self._shared_summary_row(
                section_label,
                section_rows,
                role="section",
                level=0,
                group_key=section_key,
                fields_to_sum={"presented_residual"},
            ))
            partners = {}
            for row in section_rows:
                partners.setdefault(row.get("partner_name") or "Partenaire non renseigné", []).append(row)
            for partner_sequence, (partner_name, partner_rows) in enumerate(partners.items(), start=1):
                partner_key = f"{section_key}|partner|{partner_sequence}"
                result.append(self._shared_summary_row(
                    partner_name,
                    partner_rows,
                    role="group",
                    level=1,
                    parent_group_key=section_key,
                    group_key=partner_key,
                    fields_to_sum={"presented_residual"},
                ))
                result.extend({
                    **child,
                    "is_group": "false",
                    "parent_group_key": partner_key,
                    "row_level": 2,
                    "presentation_role": "detail",
                } for child in partner_rows)
        return result

    def _deferred_hierarchy_rows(self, rows):
        result = []
        for section_sequence, section_label in enumerate(
            ("Charges constatées d’avance", "Produits constatés d’avance"),
            start=1,
        ):
            section_rows = [
                row for row in rows if row.get("section") == section_label
            ]
            if not section_rows:
                continue
            section_key = f"deferred|{section_sequence}"
            result.append(self._shared_summary_row(
                section_label,
                section_rows,
                role="section",
                level=0,
                group_key=section_key,
                fields_to_sum={"amount", "deferred_account_balance"},
            ))
            accounts = {}
            for row in section_rows:
                account = (
                    row.get("deferred_account_code") or "",
                    row.get("deferred_account_name") or "Compte non renseigné",
                )
                accounts.setdefault(account, []).append(row)
            for account_sequence, ((code, name), account_rows) in enumerate(
                accounts.items(),
                start=1,
            ):
                account_key = f"{section_key}|account|{account_sequence}"
                result.append(self._shared_summary_row(
                    f"{code} — {name}".strip(" —"),
                    account_rows,
                    role="group",
                    level=1,
                    parent_group_key=section_key,
                    group_key=account_key,
                    fields_to_sum={"amount", "deferred_account_balance"},
                ))
                result.extend({
                    **child,
                    "is_group": "false",
                    "parent_group_key": account_key,
                    "row_level": 2,
                    "presentation_role": "detail",
                } for child in account_rows)
        return result

    def _balance_sheet_hierarchy_rows(self, rows):
        """Expose Actif and Passif as an exact shared presentation tree."""
        self.ensure_one()
        result = []
        for side_key, side_label in (
            ("bilan_actif", "Actif"),
            ("bilan_passif", "Passif"),
        ):
            side_rows = [
                row
                for row in rows
                if row.get("statement_key") == side_key
                and row.get("presentation_role") != "total"
            ]
            total_row = next(
                (
                    row
                    for row in rows
                    if row.get("statement_key") == side_key
                    and row.get("presentation_role") == "total"
                ),
                None,
            )
            side_group_key = f"balance-sheet|{side_key}"
            result.append({
                "statement_key": side_key,
                "statement_side": side_label,
                "label": side_label,
                "is_group": "true",
                "group_key": side_group_key,
                "row_level": 0,
                "presentation_role": "section",
            })
            sections = {}
            for row in side_rows:
                sections.setdefault(row.get("section") or side_label, []).append(row)
            for sequence, (section_label, children) in enumerate(sections.items(), start=1):
                section_key = f"{side_group_key}|section|{sequence}"
                section_amount = sum(
                    (_amount(child.get("amount")) for child in children),
                    Decimal("0.00"),
                )
                result.append({
                    "statement_key": side_key,
                    "statement_side": side_label,
                    "section": section_label,
                    "label": section_label,
                    "amount": _amount_text(section_amount),
                    "is_group": "true",
                    "group_key": section_key,
                    "parent_group_key": side_group_key,
                    "row_level": 1,
                    "presentation_role": "group",
                })
                result.extend({
                    **child,
                    "statement_side": side_label,
                    "is_group": "false",
                    "parent_group_key": section_key,
                    "row_level": 2,
                    "presentation_role": "detail",
                } for child in children)
            if total_row:
                result.append({
                    **total_row,
                    "statement_side": side_label,
                    "is_group": "false",
                    "parent_group_key": side_group_key,
                    "row_level": 0,
                    "presentation_role": "total",
                })
        return result

    def _statement_hierarchy_rows(self, row, *, section_group_key):
        """Expand one statement line through PCG groups to account numbers."""
        self.ensure_one()
        breakdown = row.get("account_breakdown") or []
        statement_key = (
            f"{section_group_key}|statement|"
            f"{row.get('line_code') or row.get('line_name') or 'line'}"
        )
        statement_row = {
            **row,
            "is_group": "true" if breakdown else "false",
            "row_level": 1,
            "parent_group_key": section_group_key,
        }
        if not breakdown:
            return [statement_row]
        statement_row.update({
            "group_key": statement_key,
            "hierarchy_kind": "statement",
            "presentation_role": (
                row.get("presentation_role") or "detail"
            ),
        })

        tree = {"entries": {}}
        for account_row in breakdown:
            node = tree
            for group in account_row.get("group_chain") or []:
                entry_key = f"group:{group['id']}"
                entry = node["entries"].setdefault(entry_key, {
                    "kind": "group",
                    "sort_key": group.get("code") or "",
                    "group": group,
                    "entries": {},
                })
                node = entry
            account_key = (
                f"account:{account_row.get('account_id') or ''}:"
                f"{account_row.get('account_code') or ''}"
            )
            node["entries"][account_key] = {
                "kind": "account",
                "sort_key": account_row.get("account_code") or "",
                "account": account_row,
            }

        def descendant_accounts(node):
            accounts = []
            for entry in node["entries"].values():
                if entry["kind"] == "account":
                    accounts.append(entry["account"])
                else:
                    accounts.extend(descendant_accounts(entry))
            return accounts

        def flatten(node, *, parent_key, level):
            flattened = []
            entries = sorted(
                node["entries"].values(),
                key=lambda entry: (
                    entry.get("sort_key") or "",
                    entry["kind"] != "group",
                ),
            )
            for entry in entries:
                if entry["kind"] == "account":
                    account = entry["account"]
                    flattened.append({
                        "report_company_id": row.get("report_company_id"),
                        "report_company_ids": row.get("report_company_ids"),
                        "report_company_name": row.get("report_company_name"),
                        "report_currency_id": row.get("report_currency_id"),
                        "report_currency": row.get("report_currency"),
                        "statement_key": row.get("statement_key"),
                        "section": row.get("section"),
                        "line_code": row.get("line_code"),
                        "label": account.get("account_name") or "",
                        "account_code": account.get("account_code") or "",
                        "account_name": account.get("account_name") or "",
                        "source_account_id": (
                            account.get("source_account_id") or ""
                        ),
                        "drilldown_account_codes": (
                            account.get("account_code") or ""
                        ),
                        "move_line_count": str(
                            account.get("move_line_count") or 0,
                        ),
                        "company_contributions": (
                            account.get("company_contributions") or []
                        ),
                        "amount": _amount_text(account.get("amount")),
                        "net_amount": _amount_text(account.get("amount")),
                        "is_group": "false",
                        "row_level": level,
                        "parent_group_key": parent_key,
                        "presentation_role": "detail",
                        "hierarchy_kind": "account",
                    })
                    continue
                group = entry["group"]
                accounts = descendant_accounts(entry)
                group_key = f"{parent_key}|pcg_group|{group['id']}"
                amount = sum(
                    (_amount(account.get("amount")) for account in accounts),
                    Decimal("0.00"),
                )
                codes = sorted({
                    account.get("account_code") or ""
                    for account in accounts
                    if account.get("account_code")
                })
                flattened.append({
                    "report_company_id": row.get("report_company_id"),
                    "report_company_ids": row.get("report_company_ids"),
                    "report_company_name": row.get("report_company_name"),
                    "report_currency_id": row.get("report_currency_id"),
                    "report_currency": row.get("report_currency"),
                    "statement_key": row.get("statement_key"),
                    "section": row.get("section"),
                    "line_code": row.get("line_code"),
                    "label": group.get("name") or "",
                    "account_code": group.get("code") or "",
                    "drilldown_account_codes": ",".join(codes),
                    "move_line_count": str(sum(
                        int(account.get("move_line_count") or 0)
                        for account in accounts
                    )),
                    "amount": _amount_text(amount),
                    "net_amount": _amount_text(amount),
                    "is_group": "true",
                    "row_level": level,
                    "group_key": group_key,
                    "parent_group_key": parent_key,
                    "presentation_role": "group",
                    "hierarchy_kind": "pcg_group",
                })
                flattened.extend(
                    flatten(
                        entry,
                        parent_key=group_key,
                        level=level + 1,
                    ),
                )
            return flattened

        return [
            statement_row,
            *flatten(tree, parent_key=statement_key, level=2),
        ]

    def _report_group(self, row):
        report_company_ids = row.get("report_company_ids") or []
        company_key = (
            "aggregate"
            if len(report_company_ids) > 1
            else str(row.get("report_company_id") or "")
        )
        company_name = row.get("report_company_name") or ""
        field_map = {
            "section": (
                "section",
                row.get("section")
                or row.get("statement_name")
                or row.get("statement_key")
                or row.get("report_section")
                or row.get("form_code")
                or row.get("tax_name")
                or row.get("country_code")
                or row.get("currency")
                or row.get("asset_name"),
            ),
            "account": (
                "account_code",
                row.get("account_code")
                or row.get("asset_account")
                or row.get("deferred_account_code"),
            ),
            "partner": ("partner_name", row.get("partner_name")),
            "journal": (
                "journal_code",
                row.get("journal_code")
                or row.get("journal_name"),
            ),
            "analytic": (
                "analytic_name",
                row.get("analytic_name")
                or row.get("analytic_account_name")
                or row.get("analytic_code"),
            ),
        }
        if self.group_by == "month":
            raw_date = (
                row.get("date")
                or row.get("due_date")
                or row.get("deferred_date")
                or row.get("depreciation_date")
                or ""
            )
            value = str(raw_date)[:7] or "No date"
            field_name = "report_month"
        else:
            field_name, value = field_map.get(
                self.group_by,
                ("section", ""),
            )
            value = str(value or "Not specified")
        if self.group_by == "section":
            french_statement = self.report_type in {
                "profit_loss",
                "french_annual",
                "french_balance_sheet_2024",
                "french_profit_loss_2024",
                "sig_caf_2024",
            }
            value = (
                {
                    "bilan_actif": "Bilan - Actif",
                    "bilan_passif": "Bilan - Passif",
                    "compte_resultat": "Compte de résultat",
                    "sig_caf": "SIG et CAF",
                }
                if french_statement
                else {
                    "bilan_actif": "Balance Sheet - Assets",
                    "bilan_passif": "Balance Sheet - Liabilities",
                    "compte_resultat": "Profit and Loss",
                    "sig_caf": "SIG and CAF",
                }
            ).get(value, value)
        group_key = f"{company_key}|{self.group_by}|{value}"
        label = (
            f"{company_name} — {value}"
            if company_name
            and len(self.company_ids or self.company_id) > 1
            and len(report_company_ids) <= 1
            else value
        )
        values = {
            "report_company_id": row.get("report_company_id"),
            "report_company_ids": report_company_ids,
            "report_company_name": company_name,
            "report_currency_id": row.get("report_currency_id"),
            "report_currency": row.get("report_currency"),
            field_name: value,
        }
        if field_name == "account_code":
            values["account_name"] = row.get("account_name") or ""
            label = " — ".join(
                part
                for part in (value, row.get("account_name") or "")
                if part
            )
        return group_key, label, values

    @staticmethod
    def _summable_report_fields():
        return {
            "opening_balance",
            "debit",
            "credit",
            "balance",
            "closing_balance",
            "movement",
            "amount",
            "gross_amount",
            "depreciation_amount",
            "net_amount",
            "residual",
            "presented_residual",
            "amount_residual",
            "imported_period_net_value",
            "original_value",
            "amount_currency",
            "rounded_amount",
            "statement_balance",
            "not_due",
            "bucket_1_30",
            "bucket_31_60",
            "bucket_61_90",
            "bucket_over_90",
            "total",
            "allocated_debit",
            "allocated_credit",
            "allocated_balance",
            "presented_tax_base",
            "presented_tax_amount",
        }

    def _attach_comparison_values(self, current_rows, comparison_rows):
        self.ensure_one()
        comparison_by_key = {
            self._comparison_key(row): row
            for row in comparison_rows
        }
        result = []
        seen_keys = set()
        for row in current_rows:
            row = dict(row)
            key = self._comparison_key(row)
            seen_keys.add(key)
            period_value = self._row_period_value(row)
            row["period_value"] = _amount_text(period_value)
            if self.comparison_mode == "none":
                result.append(row)
                continue
            comparison_row = comparison_by_key.get(key, {})
            comparison_value = self._row_period_value(comparison_row)
            row.update({
                "comparison_value": _amount_text(comparison_value),
                "difference": _amount_text(
                    period_value - comparison_value,
                ),
            })
            result.append(row)
        for key, comparison_row in comparison_by_key.items():
            if key in seen_keys:
                continue
            comparison_value = self._row_period_value(comparison_row)
            row = dict(comparison_row)
            current_amount_fields = (
                MONETARY_REPORT_FIELDS
                | self._summable_report_fields()
            ) - {
                "comparison_value",
                "difference",
            }
            for field_name in current_amount_fields:
                if field_name in row:
                    row[field_name] = "0.00"
            row.update({
                "period_value": "0.00",
                "comparison_value": _amount_text(comparison_value),
                "difference": _amount_text(-comparison_value),
                "comparison_only": "true",
            })
            result.append(row)
        return result

    def _comparison_key(self, row):
        if row.get("group_key"):
            return ("group", row["group_key"])
        values = [
            row.get("report_company_id"),
            row.get("section")
            or row.get("statement_name")
            or row.get("report_section")
            or row.get("form_code"),
            row.get("line_code") or row.get("field_code"),
            row.get("account_code")
            or row.get("asset_account")
            or row.get("deferred_account_code"),
            row.get("journal_code"),
            row.get("source_asset_id"),
            row.get("source_partner_id") or row.get("partner_name"),
            row.get("analytic_key")
            or row.get("analytic_name")
            or row.get("analytic_account_name"),
            row.get("source_line_id")
            or row.get("source_statement_line_id")
            or row.get("move_name"),
        ]
        return tuple(str(value or "") for value in values)

    def _row_period_value(self, row):
        if not row:
            return Decimal("0.00")
        preferred = {
            "aged_receivable": ("total", "residual"),
            "aged_payable": ("total", "residual"),
            "fixed_assets": (
                "imported_period_net_value",
                "net_amount",
            ),
            "fixed_asset_group_account": (
                "imported_period_net_value",
                "net_amount",
            ),
            "depreciation_schedule": (
                "depreciation_amount",
                "amount",
            ),
            "analytic_report": (
                "allocated_balance",
                "balance",
            ),
        }.get(self.report_type, ())
        keys = (
            *preferred,
            "closing_balance",
            "amount",
            "net_amount",
            "balance",
            "statement_balance",
            "presented_residual",
            "residual",
            "total",
        )
        for key in keys:
            if row.get(key) not in (None, ""):
                return _amount(row[key])
        debit = _amount(row.get("debit"))
        credit = _amount(row.get("credit"))
        return debit - credit

    def _report_rows_single(self):
        if self.report_type == "trial_balance":
            return self._trial_balance_rows()
        if self.report_type == "general_ledger":
            return self._general_ledger_rows()
        if self.report_type == "journal_report":
            return self._journal_report_rows()
        if self.report_type == "partner_ledger":
            return self._partner_ledger_rows()
        if self.report_type == "customer_statement":
            return self._customer_statement_rows()
        if self.report_type == "open_items":
            return self._open_item_rows()
        if self.report_type in ("aged_receivable", "aged_payable"):
            return self._aged_partner_rows(self.report_type == "aged_receivable")
        if self.report_type == "balance_sheet":
            return self._balance_sheet_rows()
        if self.report_type == "profit_loss":
            return self._french_annual_rows(
                statement_keys={"compte_resultat"},
                report_variant=self._report_variant_key(),
            )
        if self.report_type == "tax_report":
            return self._tax_report_rows()
        if self.report_type == "tax_report_group_account_tax":
            return self._localized_tax_group_rows("account_tax")
        if self.report_type == "tax_report_group_tax_account":
            return self._localized_tax_group_rows("tax_account")
        if self.report_type in ("ec_sales_list", "oss_sales", "oss_imports"):
            return self._eu_tax_report_rows()
        if self.report_type == "bank_reconciliation":
            return self._bank_reconciliation_rows()
        if self.report_type == "currency_report":
            return self._currency_report_rows()
        if self.report_type == "cash_flow":
            return self._management_summary_rows("cash_flow")
        if self.report_type == "executive_summary":
            return self._management_summary_rows("executive_summary")
        if self.report_type == "analytic_report":
            return self._analytic_report_rows()
        if self.report_type == "fixed_assets":
            return self._fixed_asset_rows()
        if self.report_type == "fixed_asset_group_account":
            return self._fixed_asset_group_account_rows()
        if self.report_type == "depreciation_schedule":
            return self._depreciation_schedule_rows()
        if self.report_type == "deferred_schedule":
            return self._deferred_schedule_rows()
        if self.report_type == "french_annual":
            return self._french_annual_rows()
        if self.report_type == "french_balance_sheet_2024":
            return self._french_annual_rows(
                statement_keys={"bilan_actif", "bilan_passif"},
                report_variant=self._report_variant_key(),
            )
        if self.report_type == "french_profit_loss_2024":
            return self._french_annual_rows(
                statement_keys={"compte_resultat"},
                report_variant=self._report_variant_key(),
            )
        if self.report_type == "sig_caf_2024":
            return self._french_annual_rows(
                statement_keys={"sig_caf"},
                report_variant=self._report_variant_key(),
            )
        if self.report_type == "french_tax_package":
            return self._french_tax_package_rows()
        if self.report_type == "closing_package":
            return self._closing_package_rows()
        message = "Unsupported report type."
        raise UserError(message)

    def _state_sql(self):
        return "" if self.target_move == "all" else "AND move.state = 'posted'"

    def _ledger_scope_sql(self):
        return ""

    def _bank_scope_sql(self):
        return ""

    def _analytic_scope_sql(self):
        return ""

    def _validate_filter_scope(self, for_drilldown=False):
        if for_drilldown:
            return
        companies = self._selected_companies()
        if self.company_id not in companies:
            message = "The primary company must be included in Companies."
            raise UserError(message)
        if self.report_type == "fec":
            if len(companies) != 1:
                message = "Generate one FEC per company."
                raise UserError(message)
            if self.company_id not in self.env.companies:
                message = (
                    "You cannot export a FEC for a company outside your "
                    "allowed companies."
                )
                raise AccessError(message)
            if not self.fec_test_mode and not self.env.user.has_group("account.group_account_manager"):
                message = (
                    "Only an Accounting Manager can generate an official "
                    "non-test FEC because it may update lock dates."
                )
                raise UserError(message)
            if self.export_format != "txt":
                message = "FEC exports must use the FEC TXT format."
                raise UserError(message)
            if self.target_move != "posted":
                message = "Official FEC generation uses posted entries only."
                raise UserError(message)
            if self.journal_ids or self.account_ids or self.partner_ids:
                message = (
                    "FEC exports cannot be filtered by journal, account or "
                    "partner. Use General Ledger for filtered review."
                )
                raise UserError(message)
            return
        if self.report_type in {
            "french_tax_package",
            "closing_package",
        } and len(companies) != 1:
            message = (
                "French statutory and closing packages must be generated "
                "for one company at a time."
            )
            raise UserError(message)
        if len(companies.mapped("currency_id")) != 1:
            message = (
                "Combined reports require companies with the same company "
                "currency. Run this report separately for each currency."
            )
            raise UserError(message)
        if self.export_format == "txt":
            message = "The FEC TXT format is only available for FEC exports."
            raise UserError(message)
        if self.report_type in {"french_tax_package", "closing_package"} and (self.journal_ids or self.account_ids or self.partner_ids):
            message = (
                "French statutory benchmark mapping and closing packages "
                "use company and period filters only."
            )
            raise UserError(message)
        if self.report_type in ("fixed_assets", "fixed_asset_group_account", "depreciation_schedule") and (self.journal_ids or self.partner_ids):
            message = (
                "Journal and partner filters are not applicable to "
                "fixed-asset and depreciation-schedule exports."
            )
            raise UserError(message)
        if self.report_type == "bank_reconciliation" and self.account_ids:
            message = (
                "Account filters are not applicable to bank reconciliation "
                "exports. Use the journal, partner and period filters."
            )
            raise UserError(message)

    def _fec_export_payload(self):
        if "l10n_fr.fec.export.wizard" not in self.env:
            message = (
                "French FEC generation requires the l10n_fr_account module."
            )
            raise UserError(message)
        Wizard = self.env["l10n_fr.fec.export.wizard"].sudo().with_company(self.company_id).with_context(
            allowed_company_ids=self.company_id.ids,
            fec_test_mode=self.fec_test_mode,
        )
        fec_wizard = Wizard.create({
            "date_from": self.date_from,
            "date_to": self.date_to,
            "test_file": self.fec_test_mode,
            "export_type": "official",
        })
        result = fec_wizard.with_context(
            allowed_company_ids=self.company_id.ids,
            # The native generator otherwise opens a second cursor while
            # lazily streaming the file. This wrapper creates the transient
            # and consumes that stream in one request, so the second cursor
            # cannot see the uncommitted wizard. Official/test behavior is
            # governed by test_file; this context only keeps stream reads on
            # the current transaction cursor.
            fec_test_mode=True,
        ).generate_fec()
        content = b"".join(result["file_content"])
        stats = self._fec_file_stats(content)
        metadata = self._export_metadata(stats["row_count"])
        metadata.update({
            "file_name": result["file_name"],
            "file_type": result["file_type"],
            "sha256": hashlib.sha256(content).hexdigest(),
            "debit": stats["debit"],
            "credit": stats["credit"],
            "header": stats["header"],
            "validation": "not_official_dgfip_validation",
        })
        return content, result["file_name"], metadata

    def _fec_file_stats(self, content):
        decoded = content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(decoded), delimiter="|"))
        header = rows[0] if rows else []
        data_rows = rows[1:]
        debit = sum(_fec_amount(row[11]) for row in data_rows if len(row) > 12)
        credit = sum(_fec_amount(row[12]) for row in data_rows if len(row) > 12)
        return {
            "header": header,
            "row_count": len(data_rows),
            "debit": _amount_text(debit),
            "credit": _amount_text(credit),
        }

    def _line_filter_sql(self):
        clauses = []
        params = []
        if self.journal_ids:
            clauses.append("AND move.journal_id IN %s")
            params.append(tuple(self.journal_ids.ids))
        if self.account_ids:
            clauses.append("AND line.account_id IN %s")
            params.append(tuple(self.account_ids.ids))
        if self.partner_ids:
            clauses.append("AND line.partner_id IN %s")
            params.append(tuple(self.partner_ids.ids))
        if self.analytic_plan_ids:
            plan_account_ids = self.env["account.analytic.account"].search([
                ("plan_id", "in", self.analytic_plan_ids.ids),
            ]).ids
            clauses.append(
                "AND EXISTS ("
                "SELECT 1 FROM jsonb_object_keys("
                "COALESCE(line.analytic_distribution, '{}'::jsonb)"
                ") AS analytic_key "
                "WHERE string_to_array(analytic_key, ',') "
                "&& %s::text[])",
            )
            params.append([str(record_id) for record_id in plan_account_ids])
        if self.analytic_account_ids:
            clauses.append(
                "AND EXISTS ("
                "SELECT 1 FROM jsonb_object_keys("
                "COALESCE(line.analytic_distribution, '{}'::jsonb)"
                ") AS analytic_key "
                "WHERE string_to_array(analytic_key, ',') "
                "&& %s::text[])",
            )
            params.append([
                str(record_id)
                for record_id in self.analytic_account_ids.ids
            ])
        return "\n               ".join(clauses), params

    def _analytic_filter_sql(self):
        clauses = []
        params = []
        if self.journal_ids:
            clauses.append("AND line.journal_id IN %s")
            params.append(tuple(self.journal_ids.ids))
        if self.account_ids:
            clauses.append("AND analytic.general_account_id IN %s")
            params.append(tuple(self.account_ids.ids))
        if self.partner_ids:
            clauses.append("AND analytic.partner_id IN %s")
            params.append(tuple(self.partner_ids.ids))
        if self.analytic_plan_ids:
            clauses.append("AND analytic_account.plan_id IN %s")
            params.append(tuple(self.analytic_plan_ids.ids))
        if self.analytic_account_ids:
            clauses.append("AND analytic.account_id IN %s")
            params.append(tuple(self.analytic_account_ids.ids))
        return "\n               ".join(clauses), params

    def _analytic_state_sql(self):
        return "" if self.target_move == "all" else "AND (move.id IS NULL OR move.state = 'posted')"

    def _asset_account_filter_sql(self):
        if not self.account_ids:
            return "", []
        account_ids = tuple(self.account_ids.ids)
        return (
            "AND (profile.account_asset_id IN %s "
            "OR profile.account_depreciation_id IN %s "
            "OR profile.account_expense_depreciation_id IN %s)"
        ), [account_ids, account_ids, account_ids]

    def _bank_filter_sql(self):
        clauses = []
        params = []
        if self.journal_ids:
            clauses.append("AND bsl.journal_id IN %s")
            params.append(tuple(self.journal_ids.ids))
        if self.partner_ids:
            clauses.append("AND bsl.partner_id IN %s")
            params.append(tuple(self.partner_ids.ids))
        return "\n               ".join(clauses), params

    def _deferred_schedule_filter_sql(self):
        clauses = []
        params = []
        if self.journal_ids:
            clauses.append("AND deferral.journal_id IN %s")
            params.append(tuple(self.journal_ids.ids))
        if self.partner_ids:
            clauses.append("AND schedule.partner_id IN %s")
            params.append(tuple(self.partner_ids.ids))
        if self.account_ids:
            account_ids = tuple(self.account_ids.ids)
            clauses.append(
                "AND (deferral.deferral_account_id IN %s "
                "OR schedule.recognition_account_id IN %s)"
            )
            params.extend([account_ids, account_ids])
        return "\n               ".join(clauses), params

    def _closing_package_rows(self):
        closing = self.env["rebuild.account.closing.period"].search([
            ("company_id", "=", self.company_id.id),
            ("date_from", "=", self.date_from),
            ("date_to", "=", self.date_to),
        ], order="period_type desc, id desc", limit=1)
        if not closing:
            message = (
                "No closing workspace matches the selected company and exact dates. "
                "Open Closing Workspaces, synchronize the company profile and use "
                "Generate Package from that period."
            )
            raise UserError(message)
        rows = [{
            "section": "Closing overview",
            "line_code": "CLOSE_STATUS",
            "label": closing.name,
            "status": closing.state,
            "validation": closing.readiness_status,
            "record_count": str(len(closing.control_line_ids)),
            "amount": "0.00",
            "details": closing.readiness_summary or "",
            "next_action": closing.actions_awaiting_valentin or "",
            "evidence": closing.package_reference or "",
        }]
        rows.extend({
            "section": "Accepted closing snapshots",
            "line_code": f"SNAPSHOT_{snapshot.id}",
            "label": snapshot.name,
            "status": snapshot.decision_conclusion,
            "validation": "immutable_sha256",
            "record_count": "1",
            "amount": "0.00",
            "details": (
                f"{snapshot.file_size} byte(s); "
                f"captured={fields.Datetime.to_string(snapshot.captured_at)}"
            ),
            "next_action": (
                "Retain this immutable payload and recorded decision with "
                "the closing archive."
            ),
            "evidence": (
                f"sha256={snapshot.sha256}; "
                f"decision={snapshot.review_decision_id.display_name}; "
                f"reviewer={snapshot.reviewer_name}"
            ),
        } for snapshot in closing.snapshot_ids)
        rows.extend({
            "section": f"Closing control - {control.category}",
            "line_code": control.code,
            "label": control.name,
            "status": control.status,
            "validation": control.status,
            "record_count": str(control.record_count),
            "amount": _amount_text(control.amount),
            "details": control.summary or "",
            "next_action": control.next_action or "",
            "evidence": f"owner={control.owner}; accountant_visible={control.accountant_visible}",
        } for control in closing.control_line_ids.sorted(lambda line: (line.category, line.code)))
        declarations = self.env["rebuild.account.declaration"].search([
            ("company_id", "=", self.company_id.id),
            ("fiscalyear_start", "=", closing.fiscalyear_start),
            ("fiscalyear_end", "=", closing.fiscalyear_end),
        ])
        for declaration in declarations:
            rows.append({
                "section": "Declaration schedule",
                "line_code": declaration.form_code,
                "label": declaration.name,
                "status": declaration.status,
                "validation": declaration.validation_status,
                "record_count": str(declaration.prefilled_line_count),
                "amount": _amount_text(declaration.amount_due),
                "details": declaration.validation_summary or "",
                "next_action": declaration.unresolved_information or "",
                "evidence": (
                    f"rule={declaration.rule_id.code}/{declaration.rule_version}; "
                    f"deadline={fields.Date.to_string(declaration.deadline_date)}; "
                    f"official_source={declaration.official_url}; filing_reference={declaration.external_filing_reference or ''}"
                ),
            })
            rows.extend({
                "section": f"Declaration fields - {declaration.form_code}",
                "line_code": field.field_code,
                "label": field.field_label,
                "status": "unresolved" if field.is_unresolved else "prefilled",
                "validation": field.validation_status,
                "record_count": "1",
                "amount": _amount_text(field.amount),
                "details": field.value_text or field.source_formula or "",
                "next_action": field.unresolved_reason or "",
                "evidence": f"{field.source_kind}: {field.source_reference or ''}",
            } for field in declaration.field_line_ids)
        rows.append({
            "section": "Lock dates",
            "line_code": "LOCK_EVIDENCE",
            "label": "Standard Odoo lock-date evidence",
            "status": closing.state,
            "validation": "recorded" if closing.final_lock_dates else "pending",
            "record_count": "0",
            "amount": "0.00",
            "details": f"previous={closing.previous_lock_dates or ''}; final={closing.final_lock_dates or ''}",
            "next_action": "Final lock dates are applied only by an Accounting Manager after closing controls and reviewer approval.",
            "evidence": f"closed_by={closing.closed_by_id.name or ''}; closed_at={closing.closed_at or ''}",
        })
        return rows

    def _trial_balance_rows(self):
        filter_sql, filter_params = self._line_filter_sql()
        self.env.cr.execute(
            f"""
            SELECT {ACCOUNT_CODE_SQL} AS account_code,
                   {ACCOUNT_NAME_SQL} AS account_name,
                   account.id::text AS account_id,
                   account.account_type AS account_type,
                   account.id::text AS source_account_id,
                   count(line.id) FILTER (
                       WHERE move.date BETWEEN %s AND %s
                   )::text AS move_line_count,
                   round(sum(
                       CASE
                           WHEN move.date < %s THEN line.balance
                           ELSE 0
                       END
                   )::numeric, 2)::text AS opening_balance,
                   round(sum(
                       CASE
                           WHEN move.date BETWEEN %s AND %s
                           THEN line.debit
                           ELSE 0
                       END
                   )::numeric, 2)::text AS debit,
                   round(sum(
                       CASE
                           WHEN move.date BETWEEN %s AND %s
                           THEN line.credit
                           ELSE 0
                       END
                   )::numeric, 2)::text AS credit,
                   round(sum(
                       CASE
                           WHEN move.date BETWEEN %s AND %s
                           THEN line.balance
                           ELSE 0
                       END
                   )::numeric, 2)::text AS movement,
                   round(sum(
                       CASE
                           WHEN move.date BETWEEN %s AND %s
                           THEN line.balance
                           ELSE 0
                       END
                   )::numeric, 2)::text AS balance,
                   round(sum(line.balance)::numeric, 2)::text AS closing_balance
              FROM account_move_line line
              JOIN account_move move ON move.id = line.move_id
              JOIN res_company company ON company.id = line.company_id
              JOIN account_account account ON account.id = line.account_id
             WHERE line.company_id = %s
               AND move.date <= %s
               {self._ledger_scope_sql()}
               {self._state_sql()}
               {filter_sql}
             GROUP BY account.id, company.id, {ACCOUNT_CODE_SQL}, {ACCOUNT_NAME_SQL}, account.account_type, account.id
             ORDER BY {ACCOUNT_CODE_SQL}
            """,
            [
                self.date_from,
                self.date_to,
                self.date_from,
                self.date_from,
                self.date_to,
                self.date_from,
                self.date_to,
                self.date_from,
                self.date_to,
                self.date_from,
                self.date_to,
                self.company_id.id,
                self.date_to,
                *filter_params,
            ],
        )
        rows = [dict(row) for row in self.env.cr.dictfetchall()]
        for row in rows:
            row["section"] = self._account_class_label(row["account_code"])
        return rows

    @staticmethod
    def _account_class_label(account_code):
        labels = {
            "1": "Classe 1 — Comptes de capitaux",
            "2": "Classe 2 — Comptes d'immobilisations",
            "3": "Classe 3 — Comptes de stocks et en-cours",
            "4": "Classe 4 — Comptes de tiers",
            "5": "Classe 5 — Comptes financiers",
            "6": "Classe 6 — Comptes de charges",
            "7": "Classe 7 — Comptes de produits",
            "8": "Classe 8 — Comptes spéciaux",
        }
        code = str(account_code or "")
        return labels.get(code[:1], "Autres comptes")

    def _general_ledger_rows(self):
        filter_sql, filter_params = self._line_filter_sql()
        self.env.cr.execute(
            f"""
            SELECT {ACCOUNT_CODE_SQL} AS account_code,
                   {ACCOUNT_NAME_SQL} AS account_name,
                   move.date::text AS date,
                   journal.code AS journal_code,
                   move.name AS move_name,
                   move.ref AS move_ref,
                   line.id::text AS source_line_id,
                   move.id::text AS source_move_id,
                   COALESCE(partner.name::text, '') AS partner_name,
                   COALESCE(line.name::text, '') AS label,
                   round(line.debit::numeric, 2)::text AS debit,
                   round(line.credit::numeric, 2)::text AS credit,
                   round(line.balance::numeric, 2)::text AS balance,
                   COALESCE(currency.name::text, '') AS currency,
                   round(line.amount_currency::numeric, 2)::text AS amount_currency,
                   COALESCE(line.matching_number::text, '') AS matching_number
              FROM account_move_line line
              JOIN account_move move ON move.id = line.move_id
              JOIN res_company company ON company.id = line.company_id
              JOIN account_account account ON account.id = line.account_id
              JOIN account_journal journal ON journal.id = move.journal_id
              LEFT JOIN res_partner partner ON partner.id = line.partner_id
              LEFT JOIN res_currency currency ON currency.id = line.currency_id
             WHERE line.company_id = %s
               AND move.date BETWEEN %s AND %s
               {self._ledger_scope_sql()}
               {self._state_sql()}
               {filter_sql}
             ORDER BY {ACCOUNT_CODE_SQL}, move.date, move.name, line.id
            """,
            [self.company_id.id, self.date_from, self.date_to, *filter_params],
        )
        rows = [dict(row) for row in self.env.cr.dictfetchall()]
        opening_by_account = {
            row["account_code"]: _amount(row["opening_balance"])
            for row in self._trial_balance_rows()
        }
        running_by_account = {}
        for row in rows:
            account_code = row["account_code"]
            opening = opening_by_account.get(account_code, Decimal("0.00"))
            running = running_by_account.get(account_code, opening)
            running += _amount(row["balance"])
            row["opening_balance"] = (
                _amount_text(opening)
                if account_code not in running_by_account
                else ""
            )
            row["running_balance"] = _amount_text(running)
            running_by_account[account_code] = running
        return rows

    def _journal_report_rows(self):
        filter_sql, filter_params = self._line_filter_sql()
        self.env.cr.execute(
            f"""
            SELECT journal.code AS journal_code,
                   COALESCE(journal.name->>'fr_FR', journal.name->>'en_US', journal.name::text) AS journal_name,
                   journal.type AS journal_type,
                   count(DISTINCT move.id)::text AS move_count,
                   count(line.id)::text AS move_line_count,
                   round(sum(line.debit)::numeric, 2)::text AS debit,
                   round(sum(line.credit)::numeric, 2)::text AS credit,
                   round(sum(line.balance)::numeric, 2)::text AS balance
              FROM account_move_line line
              JOIN account_move move ON move.id = line.move_id
              JOIN account_journal journal ON journal.id = move.journal_id
             WHERE line.company_id = %s
               AND move.date BETWEEN %s AND %s
               {self._ledger_scope_sql()}
               {self._state_sql()}
               {filter_sql}
             GROUP BY journal.id, journal.code, journal.name, journal.type
             ORDER BY journal.code
            """,
            [self.company_id.id, self.date_from, self.date_to, *filter_params],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _partner_ledger_rows(self):
        rows = self._general_ledger_rows()
        return [row for row in rows if row.get("partner_name")]

    def _customer_statement_rows(self):
        filter_sql, filter_params = self._line_filter_sql()
        customer_filter_sql = "" if self.partner_ids else "AND partner.customer_rank > 0"
        self.env.cr.execute(
            f"""
            SELECT COALESCE(partner.name::text, '') AS partner_name,
                   COALESCE(partner.id::text, '') AS source_partner_id,
                   move.date::text AS date,
                   COALESCE(line.date_maturity::text, '') AS due_date,
                   journal.code AS journal_code,
                   move.name AS move_name,
                   move.ref AS move_ref,
                   line.id::text AS source_line_id,
                   move.id::text AS source_move_id,
                   {ACCOUNT_CODE_SQL} AS account_code,
                   {ACCOUNT_NAME_SQL} AS account_name,
                   account.account_type AS account_type,
                   COALESCE(line.name::text, '') AS label,
                   round(line.debit::numeric, 2)::text AS debit,
                   round(line.credit::numeric, 2)::text AS credit,
                   round(line.balance::numeric, 2)::text AS balance,
                   round(line.amount_residual::numeric, 2)::text AS residual,
                   round(
                       sum(line.balance) OVER (
                           PARTITION BY partner.id
                           ORDER BY move.date, move.name, line.id
                           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                       )::numeric,
                       2
                   )::text AS running_balance,
                   COALESCE(currency.name::text, '') AS currency,
                   round(line.amount_currency::numeric, 2)::text AS amount_currency,
                   COALESCE(line.matching_number::text, '') AS matching_number,
                   CASE WHEN line.reconciled THEN 'reconciled' ELSE 'open' END AS payment_status
              FROM account_move_line line
              JOIN account_move move ON move.id = line.move_id
              JOIN res_company company ON company.id = line.company_id
              JOIN account_account account ON account.id = line.account_id
              JOIN account_journal journal ON journal.id = move.journal_id
              JOIN res_partner partner ON partner.id = line.partner_id
              LEFT JOIN res_currency currency ON currency.id = line.currency_id
             WHERE line.company_id = %s
               AND move.date BETWEEN %s AND %s
               AND account.account_type = 'asset_receivable'
               {customer_filter_sql}
               {self._ledger_scope_sql()}
               {self._state_sql()}
               {filter_sql}
             ORDER BY partner.name, move.date, move.name, line.id
            """,
            [self.company_id.id, self.date_from, self.date_to, *filter_params],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _open_item_rows(self):
        filter_sql, filter_params = self._line_filter_sql()
        self.env.cr.execute(
            f"""
            SELECT move.date::text AS date,
                   COALESCE(line.date_maturity::text, '') AS due_date,
                   {ACCOUNT_CODE_SQL} AS account_code,
                   {ACCOUNT_NAME_SQL} AS account_name,
                   account.account_type AS account_type,
                   move.name AS move_name,
                   line.id::text AS source_line_id,
                   COALESCE(partner.name::text, '') AS partner_name,
                   round(line.amount_residual::numeric, 2)::text AS residual,
                   CASE
                       WHEN account.account_type = 'liability_payable' THEN round((-line.amount_residual)::numeric, 2)::text
                       ELSE round(line.amount_residual::numeric, 2)::text
                   END AS presented_residual,
                   COALESCE(line.matching_number::text, '') AS matching_number
              FROM account_move_line line
              JOIN account_move move ON move.id = line.move_id
              JOIN res_company company ON company.id = line.company_id
              JOIN account_account account ON account.id = line.account_id
              LEFT JOIN res_partner partner ON partner.id = line.partner_id
             WHERE line.company_id = %s
               AND move.date BETWEEN %s AND %s
               AND account.account_type IN ('asset_receivable', 'liability_payable')
               AND (line.reconciled IS NOT TRUE OR abs(line.amount_residual) > 0.004)
               {self._ledger_scope_sql()}
               {self._state_sql()}
               {filter_sql}
             ORDER BY line.date_maturity, partner.name, move.name, line.id
            """,
            [self.company_id.id, self.date_from, self.date_to, *filter_params],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _aged_partner_rows(self, receivable):
        account_type = "asset_receivable" if receivable else "liability_payable"
        sign_sql = "line.amount_residual" if receivable else "-line.amount_residual"
        filter_sql, filter_params = self._line_filter_sql()
        self.env.cr.execute(
            f"""
            WITH open_lines AS (
                SELECT COALESCE(partner.name::text, '') AS partner_name,
                       COALESCE(partner.id::text, '') AS source_partner_id,
                       (%s::date - COALESCE(line.date_maturity, move.date)) AS age_days,
                       ({sign_sql})::numeric AS residual
                  FROM account_move_line line
                  JOIN account_move move ON move.id = line.move_id
                  JOIN account_account account ON account.id = line.account_id
                  LEFT JOIN res_partner partner ON partner.id = line.partner_id
                 WHERE line.company_id = %s
                   AND move.date BETWEEN %s AND %s
                   AND account.account_type = %s
                   AND (line.reconciled IS NOT TRUE OR abs(line.amount_residual) > 0.004)
                   {self._ledger_scope_sql()}
                   {self._state_sql()}
                   {filter_sql}
            )
            SELECT partner_name,
                   source_partner_id,
                   count(*)::text AS open_item_count,
                   round(sum(CASE WHEN age_days <= 0 THEN residual ELSE 0 END)::numeric, 2)::text AS not_due,
                   round(sum(CASE WHEN age_days BETWEEN 1 AND 30 THEN residual ELSE 0 END)::numeric, 2)::text AS bucket_1_30,
                   round(sum(CASE WHEN age_days BETWEEN 31 AND 60 THEN residual ELSE 0 END)::numeric, 2)::text AS bucket_31_60,
                   round(sum(CASE WHEN age_days BETWEEN 61 AND 90 THEN residual ELSE 0 END)::numeric, 2)::text AS bucket_61_90,
                   round(sum(CASE WHEN age_days > 90 THEN residual ELSE 0 END)::numeric, 2)::text AS bucket_over_90,
                   round(sum(residual)::numeric, 2)::text AS total
              FROM open_lines
             GROUP BY partner_name, source_partner_id
             ORDER BY partner_name
            """,
            [self.date_to, self.company_id.id, self.date_from, self.date_to, account_type, *filter_params],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _balance_sheet_rows(self):
        rows = []
        for row in self._trial_balance_rows():
            account_type = row["account_type"] or ""
            if account_type.startswith(("income", "expense")):
                continue
            amount = _amount(row["balance"])
            is_asset = account_type.startswith("asset")
            rows.append({
                "statement": "Bilan",
                "statement_key": (
                    "bilan_actif" if is_asset else "bilan_passif"
                ),
                "statement_side": "Actif" if is_asset else "Passif",
                "section": self._balance_sheet_section(account_type),
                "account_code": row["account_code"],
                "account_name": row["account_name"],
                "account_type": account_type,
                "amount": _amount_text(amount if not account_type.startswith(("liability", "equity")) else -amount),
            })
        result = sum(
            -_amount(row["balance"])
            for row in self._trial_balance_rows()
            if (row["account_type"] or "").startswith(("income", "expense"))
        )
        rows.append({
            "statement": "Bilan",
            "statement_key": "bilan_passif",
            "statement_side": "Passif",
            "section": "Capitaux propres",
            "account_code": "RESULT",
            "account_name": "Résultat de l’exercice",
            "account_type": "equity_current_year_result",
            "amount": _amount_text(result),
        })
        for side_key, label in (
            ("bilan_actif", "Total Actif"),
            ("bilan_passif", "Total Passif"),
        ):
            rows.append({
                "statement": "Bilan",
                "statement_key": side_key,
                "statement_side": (
                    "Actif" if side_key == "bilan_actif" else "Passif"
                ),
                "section": label,
                "line_code": (
                    "ACTIF_TOTAL"
                    if side_key == "bilan_actif"
                    else "PASSIF_TOTAL"
                ),
                "account_code": "",
                "account_name": label,
                "label": label,
                "amount": _amount_text(sum(
                    _amount(item.get("amount"))
                    for item in rows
                    if item.get("statement_key") == side_key
                    and item.get("presentation_role") != "total"
                )),
                "presentation_role": "total",
            })
        return rows

    def _profit_loss_rows(self):
        rows = []
        for row in self._trial_balance_rows():
            account_type = row["account_type"] or ""
            if not account_type.startswith(("income", "expense")):
                continue
            balance = _amount(row["balance"])
            amount = -balance if account_type.startswith("income") else balance
            rows.append({
                "statement": "Compte de résultat",
                "section": "Produits" if account_type.startswith("income") else "Charges",
                "account_code": row["account_code"],
                "account_name": row["account_name"],
                "account_type": account_type,
                "amount": _amount_text(amount),
            })
        rows.append({
            "statement": "Compte de résultat",
            "section": "Résultat",
            "account_code": "RESULT",
            "account_name": "Résultat net",
            "account_type": "result",
            "amount": _amount_text(sum(_amount(row["amount"]) for row in rows if row["section"] == "Produits") - sum(_amount(row["amount"]) for row in rows if row["section"] == "Charges")),
        })
        return rows

    @staticmethod
    def _balance_sheet_section(account_type):
        if account_type in ("asset_fixed", "asset_non_current"):
            return "Immobilisations"
        if account_type.startswith("asset"):
            return "Actif circulant"
        if account_type.startswith("equity"):
            return "Capitaux propres"
        if account_type.startswith("liability"):
            return "Dettes et passifs"
        return "Autres postes"

    def _tax_report_rows(self):
        rows = self._tax_report_group_rows("account_tax")
        for row in rows:
            raw_tag = row.get("tax_tag_name") or ""
            is_ledger = row.get("report_section") == "VAT accounts"
            row["report_section"] = (
                "Comptes de TVA"
                if is_ledger
                else "Grille fiscale"
            )
            row["tax_name"] = (
                "Compte de TVA"
                if is_ledger
                else self._tax_tag_display_name(raw_tag)
            )
            if not is_ledger:
                row["tax_tag_name"] = row["tax_name"]
            balance = _amount(row.get("balance"))
            tax_base = _amount(row.get("tax_base_amount"))
            base_tag = "_base" in raw_tag.casefold() or (
                raw_tag[:1].isalpha()
                and "_taxe" not in raw_tag.casefold()
            )
            row["presented_tax_base"] = _amount_text(
                abs(balance) if base_tag else abs(tax_base),
            )
            row["presented_tax_amount"] = _amount_text(
                Decimal("0.00") if base_tag else abs(balance),
            )
        return rows

    @staticmethod
    def _tax_tag_display_name(raw_name):
        raw_name = str(raw_name or "").strip()
        if not raw_name:
            return "Rubrique fiscale sans libellé"
        normalized = raw_name.replace("_", " ").strip()
        suffixes = {
            " base rc": " — base taxable (autoliquidation)",
            " base": " — base taxable",
            " taxe": " — montant de taxe",
        }
        lowered = normalized.casefold()
        for suffix, label in suffixes.items():
            if lowered.endswith(suffix):
                return normalized[: -len(suffix)].strip() + label
        if normalized.isdigit():
            return f"Ligne {normalized}"
        if normalized[:1].isalpha() and normalized[1:].isdigit():
            return f"Rubrique {normalized}"
        return normalized

    def _localized_tax_group_rows(self, group_mode):
        rows = self._tax_report_group_rows(group_mode)
        for row in rows:
            row["report_section"] = (
                "Comptes de TVA"
                if row.get("report_section") == "VAT accounts"
                else "Grille fiscale"
            )
            row["tax_name"] = (
                self._tax_tag_display_name(row.get("tax_tag_name"))
                if row.get("tax_tag_name")
                else "Compte de TVA"
            )
        return rows

    def _tax_report_group_rows(self, group_mode):
        filter_sql, filter_params = self._line_filter_sql()
        order_sql = (
            "account_code, COALESCE(tax_tag_name, ''), report_section"
            if group_mode == "account_tax"
            else "COALESCE(tax_tag_name, ''), account_code, report_section"
        )
        grouping_label = "Account > Tax" if group_mode == "account_tax" else "Tax > Account"
        self.env.cr.execute(
            f"""
            WITH tax_grid_lines AS (
                SELECT 'Tax grid tags' AS report_section,
                       tag.id AS source_tax_tag_id,
                       COALESCE(tag.name->>'fr_FR', tag.name->>'en_US', tag.name::text) AS tax_tag_name,
                       account.id AS source_account_id,
                       {ACCOUNT_CODE_SQL} AS account_code,
                       {ACCOUNT_NAME_SQL} AS account_name,
                       count(line.id) AS move_line_count,
                       sum(line.debit) AS debit,
                       sum(line.credit) AS credit,
                       sum(line.balance) AS balance,
                       sum(line.tax_base_amount) AS tax_base_amount
                  FROM account_account_tag_account_move_line_rel rel
                  JOIN account_account_tag tag ON tag.id = rel.account_account_tag_id
                  JOIN account_move_line line ON line.id = rel.account_move_line_id
                  JOIN account_move move ON move.id = line.move_id
                  JOIN res_company company ON company.id = line.company_id
                  JOIN account_account account ON account.id = line.account_id
                 WHERE line.company_id = %s
                   AND move.date BETWEEN %s AND %s
                   {self._ledger_scope_sql()}
                   {self._state_sql()}
                   {filter_sql}
                 GROUP BY tag.id,
                          COALESCE(tag.name->>'fr_FR', tag.name->>'en_US', tag.name::text),
                          account.id,
                          {ACCOUNT_CODE_SQL},
                          {ACCOUNT_NAME_SQL}
            ),
            vat_account_lines AS (
                SELECT 'VAT accounts' AS report_section,
                       NULL::integer AS source_tax_tag_id,
                       NULL::text AS tax_tag_name,
                       account.id AS source_account_id,
                       {ACCOUNT_CODE_SQL} AS account_code,
                       {ACCOUNT_NAME_SQL} AS account_name,
                       count(line.id) AS move_line_count,
                       sum(line.debit) AS debit,
                       sum(line.credit) AS credit,
                       sum(line.balance) AS balance,
                       sum(line.tax_base_amount) AS tax_base_amount
                  FROM account_move_line line
                  JOIN account_move move ON move.id = line.move_id
                  JOIN res_company company ON company.id = line.company_id
                  JOIN account_account account ON account.id = line.account_id
                 WHERE line.company_id = %s
                   AND move.date BETWEEN %s AND %s
                   AND {ACCOUNT_CODE_SQL} LIKE '445%%'
                   {self._ledger_scope_sql()}
                   {self._state_sql()}
                   {filter_sql}
                 GROUP BY account.id,
                          {ACCOUNT_CODE_SQL},
                          {ACCOUNT_NAME_SQL}
            ),
            combined AS (
                SELECT * FROM tax_grid_lines
                UNION ALL
                SELECT * FROM vat_account_lines
            )
            SELECT %s AS grouping,
                   report_section,
                   COALESCE(source_tax_tag_id::text, '') AS source_tax_tag_id,
                   COALESCE(tax_tag_name, '') AS tax_tag_name,
                   source_account_id::text AS source_account_id,
                   account_code,
                   account_name,
                   sum(move_line_count)::text AS move_line_count,
                   round(sum(debit)::numeric, 2)::text AS debit,
                   round(sum(credit)::numeric, 2)::text AS credit,
                   round(sum(balance)::numeric, 2)::text AS balance,
                   round(sum(tax_base_amount)::numeric, 2)::text AS tax_base_amount
              FROM combined
             GROUP BY report_section,
                      source_tax_tag_id,
                      tax_tag_name,
                      source_account_id,
                      account_code,
                      account_name
             ORDER BY {order_sql}
            """,
            [
                self.company_id.id,
                self.date_from,
                self.date_to,
                *filter_params,
                self.company_id.id,
                self.date_from,
                self.date_to,
                *filter_params,
                grouping_label,
            ],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _eu_tax_report_rows(self):
        period_keys = []
        if self.company_id.id == 8:
            period_keys.append("All posted accounting")
        else:
            if fields.Date.to_date(self.date_from) <= fields.Date.to_date("2025-09-30"):
                period_keys.append("Fiscal year 2024-01-10 to 2025-09-30")
            if fields.Date.to_date(self.date_to) >= fields.Date.to_date("2025-10-01"):
                period_keys.append("Fiscal year from 2025-10-01")
        if not period_keys:
            period_keys = ["Other posted accounting"]
        clauses = [
            "company_id = %s",
            "report_type = %s",
            "period_key IN %s",
        ]
        params = [self.company_id.id, self.report_type, tuple(period_keys)]
        if self.account_ids:
            clauses.append("account_id IN %s")
            params.append(tuple(self.account_ids.ids))
        if self.journal_ids:
            clauses.append("journal_id IN %s")
            params.append(tuple(self.journal_ids.ids))
        if self.partner_ids:
            clauses.append("partner_id IN %s")
            params.append(tuple(self.partner_ids.ids))
        self.env.cr.execute(
            f"""
            SELECT report_type,
                   report_name,
                   period_key,
                   country_code,
                   country_name,
                   partner_name,
                   vat_number,
                   tax_name,
                   tax_tag_name,
                   journal_code,
                   account_code,
                   account_name,
                   move_count::text AS move_count,
                   move_line_count::text AS move_line_count,
                   round(taxable_amount::numeric, 2)::text AS taxable_amount,
                   round(tax_amount::numeric, 2)::text AS tax_amount,
                   round(balance::numeric, 2)::text AS balance,
                   review_status
              FROM rebuild_account_eu_tax_report_line
             WHERE {" AND ".join(clauses)}
             ORDER BY period_key, country_code, partner_name, tax_name, tax_tag_name, journal_code, account_code
            """,
            params,
        )
        rows = [dict(row) for row in self.env.cr.dictfetchall()]
        for row in rows:
            country = (
                row.get("country_name")
                or row.get("country_code")
                or "Pays non renseigné"
            )
            if self.report_type == "ec_sales_list":
                row["section"] = (
                    f"{row.get('period_key') or 'Période'} — {country}"
                )
            else:
                row["tax_treatment"] = (
                    row.get("tax_name") or "Traitement non renseigné"
                )
                row["section"] = f"{country} — {row['tax_treatment']}"
        return rows

    def _bank_reconciliation_rows(self):
        filter_sql, filter_params = self._bank_filter_sql()
        self.env.cr.execute(
            f"""
            SELECT move.date::text AS date,
                   journal.code AS journal_code,
                   move.name AS move_name,
                   bsl.id::text AS source_statement_line_id,
                   COALESCE(bsl.payment_ref::text, '') AS payment_ref,
                   COALESCE(partner.name::text, bsl.partner_name::text, '') AS partner_name,
                   COALESCE(bsl.transaction_type::text, '') AS transaction_type,
                   COALESCE(bsl.account_number::text, '') AS account_number,
                   COALESCE(bsl.internal_index::text, '') AS internal_index,
                   round(bsl.amount::numeric, 2)::text AS amount,
                   COALESCE(currency.name::text, '') AS currency,
                   COALESCE(foreign_currency.name::text, '') AS foreign_currency,
                   round(bsl.amount_currency::numeric, 2)::text AS amount_currency,
                   round(bsl.amount_residual::numeric, 2)::text AS amount_residual,
                   bsl.is_reconciled::text AS is_reconciled,
                   CASE
                       WHEN bsl.is_reconciled THEN 'Reconciled'
                       WHEN abs(bsl.amount_residual) > 0.004 THEN 'Open residual'
                       ELSE 'Not reconciled'
                   END AS reconciliation_status,
                   count(line.id)::text AS move_line_count
              FROM account_bank_statement_line bsl
              JOIN account_move move ON move.id = bsl.move_id
              JOIN account_journal journal ON journal.id = bsl.journal_id
              LEFT JOIN res_partner partner ON partner.id = bsl.partner_id
              LEFT JOIN res_currency currency ON currency.id = bsl.currency_id
              LEFT JOIN res_currency foreign_currency ON foreign_currency.id = bsl.foreign_currency_id
              LEFT JOIN account_move_line line ON line.move_id = move.id
             WHERE bsl.company_id = %s
               AND move.date BETWEEN %s AND %s
               {self._bank_scope_sql()}
               {self._state_sql()}
               {filter_sql}
             GROUP BY bsl.id,
                      move.date,
                      journal.code,
                      move.name,
                      bsl.id,
                      bsl.payment_ref,
                      COALESCE(partner.name::text, bsl.partner_name::text, ''),
                      bsl.transaction_type,
                      bsl.account_number,
                      bsl.internal_index,
                      bsl.amount,
                      currency.name,
                      foreign_currency.name,
                      bsl.amount_currency,
                      bsl.amount_residual,
                      bsl.is_reconciled
             ORDER BY journal.code, move.date, bsl.id
            """,
            [self.company_id.id, self.date_from, self.date_to, *filter_params],
        )
        rows = [dict(row) for row in self.env.cr.dictfetchall()]
        statuses = {
            "Reconciled": "Rapprochée",
            "Open residual": "Résiduel ouvert",
            "Not reconciled": "Non rapprochée",
        }
        for row in rows:
            row["status"] = statuses.get(
                row.get("reconciliation_status"),
                row.get("reconciliation_status") or "À examiner",
            )
        return rows

    def _currency_report_rows(self):
        filter_sql, filter_params = self._line_filter_sql()
        self.env.cr.execute(
            f"""
            WITH base_lines AS (
                SELECT 'Foreign currency ledger' AS report_section,
                       currency.name::text AS currency,
                       {ACCOUNT_CODE_SQL} AS account_code,
                       {ACCOUNT_NAME_SQL} AS account_name,
                       account.account_type,
                       COALESCE(partner.name::text, '') AS partner_name,
                       line.id,
                       line.debit,
                       line.credit,
                       line.balance,
                       line.amount_currency,
                       line.amount_residual,
                       line.amount_residual_currency
                  FROM account_move_line line
                  JOIN account_move move ON move.id = line.move_id
                  JOIN res_company company ON company.id = line.company_id
                  JOIN account_account account ON account.id = line.account_id
                  JOIN res_currency currency ON currency.id = line.currency_id
                  LEFT JOIN res_partner partner ON partner.id = line.partner_id
                 WHERE line.company_id = %s
                   AND move.date BETWEEN %s AND %s
                   AND line.currency_id IS NOT NULL
                   AND line.currency_id != company.currency_id
                   {self._ledger_scope_sql()}
                   {self._state_sql()}
                   {filter_sql}
                UNION ALL
                SELECT 'Realized exchange gains and losses',
                       COALESCE(currency.name::text, '') AS currency,
                       {ACCOUNT_CODE_SQL},
                       {ACCOUNT_NAME_SQL},
                       account.account_type,
                       COALESCE(partner.name::text, ''),
                       line.id,
                       line.debit,
                       line.credit,
                       line.balance,
                       line.amount_currency,
                       line.amount_residual,
                       line.amount_residual_currency
                  FROM account_move_line line
                  JOIN account_move move ON move.id = line.move_id
                  JOIN res_company company ON company.id = line.company_id
                  JOIN account_account account ON account.id = line.account_id
                  LEFT JOIN res_currency currency ON currency.id = line.currency_id
                  LEFT JOIN res_partner partner ON partner.id = line.partner_id
                 WHERE line.company_id = %s
                   AND move.date BETWEEN %s AND %s
                   AND ({ACCOUNT_CODE_SQL} LIKE '666%%' OR {ACCOUNT_CODE_SQL} LIKE '766%%')
                   {self._ledger_scope_sql()}
                   {self._state_sql()}
                   {filter_sql}
                UNION ALL
                SELECT 'Unrealized foreign-currency open items',
                       currency.name::text AS currency,
                       {ACCOUNT_CODE_SQL},
                       {ACCOUNT_NAME_SQL},
                       account.account_type,
                       COALESCE(partner.name::text, ''),
                       line.id,
                       line.debit,
                       line.credit,
                       line.balance,
                       line.amount_currency,
                       line.amount_residual,
                       line.amount_residual_currency
                  FROM account_move_line line
                  JOIN account_move move ON move.id = line.move_id
                  JOIN res_company company ON company.id = line.company_id
                  JOIN account_account account ON account.id = line.account_id
                  JOIN res_currency currency ON currency.id = line.currency_id
                  LEFT JOIN res_partner partner ON partner.id = line.partner_id
                 WHERE line.company_id = %s
                   AND move.date BETWEEN %s AND %s
                   AND line.currency_id IS NOT NULL
                   AND line.currency_id != company.currency_id
                   AND account.account_type IN ('asset_receivable', 'liability_payable')
                   AND (line.reconciled IS NOT TRUE OR abs(line.amount_residual) > 0.004 OR abs(line.amount_residual_currency) > 0.004)
                   {self._ledger_scope_sql()}
                   {self._state_sql()}
                   {filter_sql}
            )
            SELECT report_section,
                   currency,
                   account_code,
                   account_name,
                   account_type,
                   partner_name,
                   count(id)::text AS move_line_count,
                   round(sum(debit)::numeric, 2)::text AS debit,
                   round(sum(credit)::numeric, 2)::text AS credit,
                   round(sum(balance)::numeric, 2)::text AS balance,
                   round(sum(amount_currency)::numeric, 2)::text AS amount_currency,
                   round(sum(amount_residual)::numeric, 2)::text AS amount_residual,
                   round(sum(amount_residual_currency)::numeric, 2)::text AS amount_residual_currency
              FROM base_lines
             GROUP BY report_section, currency, account_code, account_name, account_type, partner_name
             ORDER BY report_section, currency, account_code, partner_name
            """,
            [
                self.company_id.id,
                self.date_from,
                self.date_to,
                *filter_params,
                self.company_id.id,
                self.date_from,
                self.date_to,
                *filter_params,
                self.company_id.id,
                self.date_from,
                self.date_to,
                *filter_params,
            ],
        )
        rows = [dict(row) for row in self.env.cr.dictfetchall()]
        section_labels = {
            "Foreign currency ledger": "Écritures en devise d’origine",
            "Realized exchange gains and losses": (
                "Gains et pertes de change réalisés"
            ),
            "Unrealized foreign-currency open items": (
                "Exposition de change non réalisée"
            ),
        }
        for row in rows:
            row["report_section"] = section_labels.get(
                row.get("report_section"),
                row.get("report_section") or "Change",
            )
        return rows

    def _management_summary_rows(self, report_key):
        filter_sql, filter_params = self._line_filter_sql()
        self.env.cr.execute(
            f"""
            SELECT count(line.id)::integer AS all_line_count,
                   greatest(COALESCE(max(move.date) - min(move.date) + 1, 0), 1)::numeric AS day_count,
                   count(line.id) FILTER (
                       WHERE account.account_type IN ('asset_cash', 'liability_credit_card')
                   )::integer AS cash_line_count,
                   round(COALESCE(sum(line.debit) FILTER (
                       WHERE account.account_type IN ('asset_cash', 'liability_credit_card')
                   ), 0)::numeric, 2) AS cash_received,
                   round(COALESCE(sum(line.credit) FILTER (
                       WHERE account.account_type IN ('asset_cash', 'liability_credit_card')
                   ), 0)::numeric, 2) AS cash_spent,
                   round(COALESCE(sum(line.balance) FILTER (
                       WHERE account.account_type IN ('asset_cash', 'liability_credit_card')
                   ), 0)::numeric, 2) AS closing_cash,
                   count(line.id) FILTER (
                       WHERE account.account_type IN ('income', 'income_other')
                   )::integer AS revenue_line_count,
                   round(-COALESCE(sum(line.balance) FILTER (
                       WHERE account.account_type IN ('income', 'income_other')
                   ), 0)::numeric, 2) AS revenue,
                   count(line.id) FILTER (
                       WHERE account.account_type = 'expense_direct_cost'
                   )::integer AS cost_line_count,
                   round(COALESCE(sum(line.balance) FILTER (
                       WHERE account.account_type = 'expense_direct_cost'
                   ), 0)::numeric, 2) AS cost_of_revenue,
                   count(line.id) FILTER (
                       WHERE account.account_type IN ('expense', 'expense_depreciation')
                   )::integer AS expense_line_count,
                   round(COALESCE(sum(line.balance) FILTER (
                       WHERE account.account_type IN ('expense', 'expense_depreciation')
                   ), 0)::numeric, 2) AS expenses,
                   count(line.id) FILTER (
                       WHERE account.account_type IN ('income', 'income_other', 'expense', 'expense_direct_cost', 'expense_depreciation')
                   )::integer AS profit_loss_line_count,
                   round(-COALESCE(sum(line.balance) FILTER (
                       WHERE account.account_type IN ('income', 'income_other', 'expense', 'expense_direct_cost', 'expense_depreciation')
                   ), 0)::numeric, 2) AS net_profit,
                   count(line.id) FILTER (
                       WHERE account.account_type = 'asset_receivable'
                   )::integer AS receivable_line_count,
                   round(COALESCE(sum(line.balance) FILTER (
                       WHERE account.account_type = 'asset_receivable'
                   ), 0)::numeric, 2) AS receivables,
                   count(line.id) FILTER (
                       WHERE account.account_type = 'liability_payable'
                   )::integer AS payable_line_count,
                   round(-COALESCE(sum(line.balance) FILTER (
                       WHERE account.account_type = 'liability_payable'
                   ), 0)::numeric, 2) AS payables,
                   count(line.id) FILTER (
                       WHERE account.account_type LIKE 'asset%%'
                          OR account.account_type LIKE 'liability%%'
                   )::integer AS net_asset_line_count,
                   round((
                       COALESCE(sum(line.balance) FILTER (WHERE account.account_type LIKE 'asset%%'), 0)
                       + COALESCE(sum(line.balance) FILTER (WHERE account.account_type LIKE 'liability%%'), 0)
                   )::numeric, 2) AS net_assets,
                   count(line.id) FILTER (
                       WHERE account.account_type IN ('asset_current', 'asset_receivable', 'asset_cash')
                          OR account.account_type IN ('liability_current', 'liability_payable', 'liability_credit_card')
                   )::integer AS current_line_count,
                   round(COALESCE(sum(line.balance) FILTER (
                       WHERE account.account_type IN ('asset_current', 'asset_receivable', 'asset_cash')
                   ), 0)::numeric, 2) AS current_assets,
                   round(-COALESCE(sum(line.balance) FILTER (
                       WHERE account.account_type IN ('liability_current', 'liability_payable', 'liability_credit_card')
                   ), 0)::numeric, 2) AS current_liabilities
              FROM account_move_line line
              JOIN account_move move ON move.id = line.move_id
              JOIN account_account account ON account.id = line.account_id
             WHERE line.company_id = %s
               AND move.date BETWEEN %s AND %s
               {self._ledger_scope_sql()}
               {self._state_sql()}
               {filter_sql}
            """,
            [self.company_id.id, self.date_from, self.date_to, *filter_params],
        )
        data = dict(self.env.cr.dictfetchone() or {})

        def decimal_value(key):
            return _amount(data.get(key))

        def count_value(key):
            return int(data.get(key) or 0)

        def metric_text(value):
            if value is None:
                return ""
            return (
                f"{Decimal(str(value)).quantize(Decimal('0.01')):.2f}"
            )

        def safe_ratio(numerator, denominator, multiplier=Decimal("1")):
            numerator = Decimal(str(numerator or "0"))
            denominator = Decimal(str(denominator or "0"))
            if not denominator:
                return None
            return numerator / denominator * multiplier

        day_count = Decimal(str(data.get("day_count") or "1"))
        cash_received = decimal_value("cash_received")
        cash_spent = decimal_value("cash_spent")
        closing_cash = decimal_value("closing_cash")

        rows = []

        def add(
            line_code,
            line_name,
            metric_type,
            source_formula,
            move_line_count,
            amount,
            metric_value=None,
            *,
            section="Indicateurs clés",
            unit=None,
        ):
            metric = amount if metric_type == "currency" else metric_value
            if metric_type == "currency":
                metric = Decimal(str(metric or "0")) / Decimal(
                    str(self._display_unit_metadata()["factor"]),
                )
                unit = self._display_unit_metadata()["short_label"]
            elif unit is None:
                unit = {
                    "percent": "%",
                    "days": "jours",
                    "ratio": "x",
                }.get(metric_type, "")
            rows.append({
                "report_key": report_key,
                "report_name": (
                    "Tableau des flux de trésorerie"
                    if report_key == "cash_flow"
                    else "Synthèse de gestion"
                ),
                "section": section,
                "line_code": line_code,
                "line_name": line_name,
                "metric_type": metric_type,
                "source_formula": source_formula,
                "details": source_formula,
                "move_line_count": str(move_line_count),
                "amount": _amount_text(amount),
                "metric_value": metric_text(metric),
                "unit": unit,
                "presentation_role": (
                    "total"
                    if line_code in {
                        "CLOSING_CASH",
                        "NET_PROFIT",
                    }
                    else "subtotal"
                    if line_code in {
                        "CASH_SURPLUS",
                        "OPERATING_RESULT",
                    }
                    else "detail"
                ),
            })

        if report_key == "cash_flow":
            add("CASH_RECEIVED", "Encaissements", "currency", "Mouvements au débit des comptes de trésorerie", count_value("cash_line_count"), cash_received)
            add("CASH_SPENT", "Décaissements", "currency", "Mouvements au crédit des comptes de trésorerie", count_value("cash_line_count"), cash_spent)
            add("CASH_SURPLUS", "Surplus de trésorerie", "currency", "Encaissements moins décaissements", count_value("cash_line_count"), cash_received - cash_spent)
            add("CLOSING_CASH", "Trésorerie de clôture", "currency", "Solde de clôture des comptes de trésorerie", count_value("cash_line_count"), closing_cash)
            return rows

        statement_rows = self._french_annual_rows()
        statement_by_code = {
            row["line_code"]: _amount(row.get("amount"))
            for row in statement_rows
        }
        trial_balance = self._trial_balance_rows()

        def balance_for(
            prefixes,
            *,
            positive=False,
            negative=False,
            excluded_prefixes=(),
            account_types=(),
        ):
            total = Decimal("0.00")
            count = 0
            for row in trial_balance:
                code = str(row.get("account_code") or "")
                balance = _amount(row.get("balance"))
                if not any(code.startswith(prefix) for prefix in prefixes):
                    continue
                if any(
                    code.startswith(prefix)
                    for prefix in excluded_prefixes
                ):
                    continue
                if account_types and row.get("account_type") not in account_types:
                    continue
                if positive and balance <= 0:
                    continue
                if negative and balance >= 0:
                    continue
                total += balance
                count += int(row.get("move_line_count") or 0)
            return total, count

        turnover = statement_by_code.get(
            "CR_CHIFFRE_AFFAIRES",
            Decimal("0.00"),
        )
        total_products = statement_by_code.get(
            "CR_TOTAL_PRODUITS",
            Decimal("0.00"),
        )
        total_charges = statement_by_code.get(
            "CR_TOTAL_CHARGES",
            Decimal("0.00"),
        )
        operating_result = statement_by_code.get(
            "CR_RESULTAT_EXPLOITATION",
            Decimal("0.00"),
        )
        net_result = statement_by_code.get(
            "CR_RESULTAT_NET",
            Decimal("0.00"),
        )
        equity = statement_by_code.get(
            "PASSIF_CAPITAUX_PROPRES",
            Decimal("0.00"),
        )
        financial_debt = statement_by_code.get(
            "PASSIF_DETTES_FINANCIERES",
            Decimal("0.00"),
        )
        fixed_assets = statement_by_code.get(
            "ACTIF_IMMO_CORP",
            Decimal("0.00"),
        )
        value_added = statement_by_code.get(
            "SIG_VALEUR_AJOUTEE",
            Decimal("0.00"),
        )
        ebe = statement_by_code.get("SIG_EBE", Decimal("0.00"))
        caf = statement_by_code.get(
            "SIG_CAPACITE_AUTOFINANCEMENT",
            Decimal("0.00"),
        )
        trade_receivables, trade_receivable_count = balance_for(
            ["411", "413", "416", "4181"],
            positive=True,
        )
        supplier_balance, supplier_count = balance_for(
            ["401", "403", "4081", "4088"],
            negative=True,
        )
        supplier_debt = -supplier_balance
        purchases = statement_by_code.get(
            "CR_ACHATS_MARCHANDISES",
            Decimal("0.00"),
        ) + statement_by_code.get(
            "CR_CHARGES_EXTERNES",
            Decimal("0.00"),
        )
        operating_assets, operating_asset_count = balance_for(
            ["3", "4"],
            positive=True,
            excluded_prefixes=["455"],
        )
        operating_liability_balance, operating_liability_count = balance_for(
            ["4"],
            negative=True,
            # Corporate-income-tax debt is outside operating working capital.
            excluded_prefixes=["444", "455"],
        )
        working_capital_requirement = (
            operating_assets + operating_liability_balance
        )
        overdraft_balance, overdraft_count = balance_for(
            ["5"],
            negative=True,
            account_types={"asset_cash", "liability_credit_card"},
        )
        bank_overdraft = -overdraft_balance

        add(
            "TURNOVER",
            "Chiffre d’affaires net",
            "currency",
            "Comptes 70 présentés selon le PCG.",
            count_value("revenue_line_count"),
            turnover,
        )
        add(
            "TOTAL_PRODUCTS",
            "Total des produits",
            "currency",
            "Total des comptes de classe 7.",
            count_value("revenue_line_count"),
            total_products,
        )
        add(
            "TOTAL_CHARGES",
            "Total des charges",
            "currency",
            "Total des comptes de classe 6.",
            count_value("profit_loss_line_count"),
            total_charges,
        )
        add(
            "OPERATING_RESULT",
            "Résultat d’exploitation",
            "currency",
            "Produits d’exploitation moins charges d’exploitation.",
            count_value("profit_loss_line_count"),
            operating_result,
        )
        add(
            "NET_RESULT",
            "Résultat net de l’exercice",
            "currency",
            "Total des produits moins total des charges.",
            count_value("profit_loss_line_count"),
            net_result,
        )
        add(
            "CLOSING_CASH",
            "Trésorerie disponible",
            "currency",
            "Solde débiteur des comptes classés en trésorerie.",
            count_value("cash_line_count"),
            closing_cash,
        )
        add(
            "EQUITY",
            "Capitaux propres",
            "currency",
            "Capitaux propres, report à nouveau et résultat de l’exercice.",
            count_value("net_asset_line_count"),
            equity,
        )
        add(
            "FINANCIAL_DEBT",
            "Dettes financières",
            "currency",
            "Comptes 16/17 et comptes courants d’associés 455 créditeurs.",
            count_value("payable_line_count"),
            financial_debt,
        )
        add(
            "VALUE_ADDED",
            "Valeur ajoutée",
            "currency",
            "Marge commerciale + production - consommations de tiers.",
            count_value("profit_loss_line_count"),
            value_added,
        )
        add(
            "CAF",
            "Capacité d’autofinancement",
            "currency",
            "Résultat net retraité des charges et produits calculés.",
            count_value("profit_loss_line_count"),
            caf,
        )

        ratio_section = "Ratios de gestion"
        add(
            "FIXED_ASSET_COVERAGE",
            "Couverture des immobilisations",
            "ratio",
            "(Capitaux propres + dettes financières) / immobilisations nettes.",
            count_value("net_asset_line_count"),
            0,
            safe_ratio(equity + financial_debt, fixed_assets),
            section=ratio_section,
        )
        add(
            "DEBT_RATIO",
            "Taux d’endettement",
            "ratio",
            "Dettes financières / capitaux propres.",
            count_value("net_asset_line_count"),
            0,
            safe_ratio(financial_debt, equity),
            section=ratio_section,
        )
        add(
            "OVERDRAFT_IMPORTANCE",
            "Importance du découvert bancaire",
            "ratio",
            "Découverts bancaires / chiffre d’affaires net HT.",
            overdraft_count,
            0,
            safe_ratio(bank_overdraft, turnover),
            section=ratio_section,
        )
        add(
            "REPAYMENT_CAPACITY",
            "Capacité de remboursement",
            "ratio",
            "Capacité d’autofinancement / dettes financières.",
            count_value("net_asset_line_count"),
            0,
            safe_ratio(caf, financial_debt),
            section=ratio_section,
        )
        add(
            "EBE_MARGIN",
            "Taux de marge brute",
            "ratio",
            "Excédent brut d’exploitation / chiffre d’affaires net HT.",
            count_value("profit_loss_line_count"),
            0,
            safe_ratio(ebe, turnover),
            section=ratio_section,
        )
        add(
            "COMMERCIAL_PROFITABILITY",
            "Rentabilité commerciale",
            "ratio",
            "Résultat net / chiffre d’affaires net HT.",
            count_value("profit_loss_line_count"),
            0,
            safe_ratio(net_result, turnover),
            section=ratio_section,
        )
        add(
            "ECONOMIC_PROFITABILITY",
            "Rentabilité économique",
            "ratio",
            "Résultat net / immobilisations nettes.",
            count_value("net_asset_line_count"),
            0,
            safe_ratio(net_result, fixed_assets),
            section=ratio_section,
        )
        add(
            "FINANCIAL_PROFITABILITY",
            "Rentabilité financière",
            "ratio",
            "Résultat net / capitaux propres.",
            count_value("net_asset_line_count"),
            0,
            safe_ratio(net_result, equity),
            section=ratio_section,
        )
        add(
            "CUSTOMER_CREDIT_DAYS",
            "Crédit clients",
            "days",
            "Créances clients / chiffre d’affaires net HT × jours de la période.",
            trade_receivable_count,
            0,
            safe_ratio(trade_receivables, turnover, day_count),
            section=ratio_section,
        )
        add(
            "SUPPLIER_CREDIT_DAYS",
            "Crédit fournisseurs",
            "days",
            "Dettes fournisseurs / achats et charges externes × jours de la période.",
            supplier_count,
            0,
            safe_ratio(supplier_debt, purchases, day_count),
            section=ratio_section,
        )
        add(
            "WORKING_CAPITAL_REQUIREMENT",
            "Importance du besoin en fonds de roulement",
            "ratio",
            "Actifs circulants d’exploitation nets des passifs d’exploitation, hors impôt sur les bénéfices, / chiffre d’affaires net HT.",
            operating_asset_count + operating_liability_count,
            0,
            safe_ratio(working_capital_requirement, turnover),
            section=ratio_section,
        )
        return rows

    def _analytic_report_rows(self):
        filter_sql, filter_params = self._analytic_filter_sql()
        self.env.cr.execute(
            f"""
            WITH analytic_lines AS (
                SELECT COALESCE(
                           analytic_account.id::text,
                           analytic.account_id::text,
                           analytic_account.id::text,
                           ''
                       ) AS analytic_key,
                       COALESCE(analytic_account.code::text, '') AS analytic_code,
                       COALESCE(analytic_account.name->>'fr_FR', analytic_account.name->>'en_US', analytic_account.name::text, analytic.name::text) AS analytic_name,
                       {ACCOUNT_CODE_SQL} AS account_code,
                       {ACCOUNT_NAME_SQL} AS account_name,
                       analytic.id,
                       analytic.amount
                  FROM account_analytic_line analytic
                  JOIN res_company company ON company.id = analytic.company_id
                  LEFT JOIN account_analytic_account analytic_account
                    ON analytic_account.id = COALESCE(
                        analytic.account_id,
                        analytic.account_id
                    )
                  LEFT JOIN account_account account ON account.id = analytic.general_account_id
                  LEFT JOIN account_move_line line ON line.id = analytic.move_line_id
                  LEFT JOIN account_move move ON move.id = line.move_id
                 WHERE analytic.company_id = %s
                   AND analytic.date BETWEEN %s AND %s
                   {self._analytic_scope_sql()}
                   {self._analytic_state_sql()}
                   {filter_sql}
            )
            SELECT analytic_key,
                   analytic_code,
                   analytic_name,
                   account_code,
                   account_name,
                   count(id)::text AS move_line_count,
                   '100.0000' AS percentage,
                   round(sum(CASE WHEN amount > 0 THEN amount ELSE 0 END)::numeric, 2)::text AS allocated_debit,
                   round(sum(CASE WHEN amount < 0 THEN -amount ELSE 0 END)::numeric, 2)::text AS allocated_credit,
                   round(sum(amount)::numeric, 2)::text AS allocated_balance
              FROM analytic_lines
             GROUP BY analytic_key, analytic_code, analytic_name, account_code, account_name
             ORDER BY analytic_name, account_code
            """,
            [self.company_id.id, self.date_from, self.date_to, *filter_params],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _fixed_asset_rows(self):
        filter_sql, filter_params = self._asset_account_filter_sql()
        self.env.cr.execute(
            f"""
            WITH asset_values AS (
                SELECT asset.id,
                       COALESCE(
                           sum(schedule.amount) FILTER (
                               WHERE schedule.type = 'depreciate'
                                 AND schedule.line_date <= %s
                                 AND (
                                     schedule.init_entry
                                     OR depreciation_move.state = 'posted'
                                 )
                           ),
                           0
                       ) AS accumulated_depreciation
                  FROM account_asset asset
                  LEFT JOIN account_asset_line schedule
                    ON schedule.asset_id = asset.id
                  LEFT JOIN account_move depreciation_move
                    ON depreciation_move.id = schedule.move_id
                 GROUP BY asset.id
            )
            SELECT asset.id::text AS source_asset_id,
                   asset.name,
                   asset.name AS asset_name,
                   COALESCE(asset.date_start::text, '') AS acquisition_date,
                   asset.state,
                   ''::text AS asset_group_name,
                   round(asset.purchase_value::numeric, 2)::text AS original_value,
                   round(asset_values.accumulated_depreciation::numeric, 2)::text AS accumulated_depreciation,
                   round(asset_values.accumulated_depreciation::numeric, 2)::text AS depreciation_amount,
                   round((
                       asset.purchase_value
                       - asset_values.accumulated_depreciation
                   )::numeric, 2)::text AS imported_period_net_value,
                   round(asset.value_residual::numeric, 2)::text AS source_book_value,
                   COALESCE(asset_account.code_store->>company.id::text, asset_account.code_store->>'1', asset_account.code_store::text, '') AS asset_account,
                   COALESCE(asset_account.code_store->>company.id::text, asset_account.code_store->>'1', asset_account.code_store::text, '') AS account_code,
                   COALESCE(depreciation_account.code_store->>company.id::text, depreciation_account.code_store->>'1', depreciation_account.code_store::text, '') AS depreciation_account,
                   COALESCE(expense_account.code_store->>company.id::text, expense_account.code_store->>'1', expense_account.code_store::text, '') AS depreciation_expense_account
              FROM account_asset asset
              JOIN res_company company ON company.id = asset.company_id
              JOIN account_asset_profile profile ON profile.id = asset.profile_id
              JOIN asset_values ON asset_values.id = asset.id
              LEFT JOIN account_account asset_account ON asset_account.id = profile.account_asset_id
              LEFT JOIN account_account depreciation_account ON depreciation_account.id = profile.account_depreciation_id
              LEFT JOIN account_account expense_account ON expense_account.id = profile.account_expense_depreciation_id
             WHERE asset.company_id = %s
               AND asset.date_start <= %s
               {filter_sql}
             ORDER BY asset.id
            """,
            [
                self.date_to,
                self.company_id.id,
                self.date_to,
                *filter_params,
            ],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _fixed_asset_group_account_rows(self):
        filter_sql, filter_params = self._asset_account_filter_sql()
        self.env.cr.execute(
            f"""
            WITH asset_values AS (
                SELECT asset.id,
                       COALESCE(
                           sum(schedule.amount) FILTER (
                               WHERE schedule.type = 'depreciate'
                                 AND schedule.line_date <= %s
                                 AND (
                                     schedule.init_entry
                                     OR depreciation_move.state = 'posted'
                                 )
                           ),
                           0
                       ) AS accumulated_depreciation
                  FROM account_asset asset
                  LEFT JOIN account_asset_line schedule
                    ON schedule.asset_id = asset.id
                  LEFT JOIN account_move depreciation_move
                    ON depreciation_move.id = schedule.move_id
                 GROUP BY asset.id
            )
            SELECT asset_account.id::text AS source_account_id,
                   COALESCE(asset_account.code_store->>company.id::text, asset_account.code_store->>'1', asset_account.code_store::text, '') AS account_code,
                   COALESCE(asset_account.name->>'fr_FR', asset_account.name->>'en_US', asset_account.name::text, '') AS account_name,
                   count(asset.id)::text AS asset_count,
                   string_agg(asset.name, '; ' ORDER BY asset.id) AS asset_names,
                   round(sum(asset.purchase_value)::numeric, 2)::text AS original_value,
                   round(sum(asset_values.accumulated_depreciation)::numeric, 2)::text AS accumulated_depreciation,
                   round(sum(asset_values.accumulated_depreciation)::numeric, 2)::text AS depreciation_amount,
                   round(sum(
                       asset.purchase_value
                       - asset_values.accumulated_depreciation
                   )::numeric, 2)::text AS imported_period_net_value,
                   round(sum(asset.value_residual)::numeric, 2)::text AS source_book_value
              FROM account_asset asset
              JOIN res_company company ON company.id = asset.company_id
              JOIN account_asset_profile profile ON profile.id = asset.profile_id
              JOIN asset_values ON asset_values.id = asset.id
              LEFT JOIN account_account asset_account ON asset_account.id = profile.account_asset_id
             WHERE asset.company_id = %s
               AND asset.date_start <= %s
               {filter_sql}
             GROUP BY asset_account.id,
                      asset_account.id,
                      COALESCE(asset_account.code_store->>company.id::text, asset_account.code_store->>'1', asset_account.code_store::text, ''),
                      COALESCE(asset_account.name->>'fr_FR', asset_account.name->>'en_US', asset_account.name::text, '')
             ORDER BY account_code
            """,
            [
                self.date_to,
                self.company_id.id,
                self.date_to,
                *filter_params,
            ],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _depreciation_schedule_rows(self):
        filter_sql, filter_params = self._asset_account_filter_sql()
        self.env.cr.execute(
            f"""
            SELECT asset.id::text AS source_asset_id,
                   asset.name AS asset_name,
                   schedule.line_date::text AS depreciation_date,
                   schedule.id::text AS source_move_id,
                   COALESCE(imported_move.name::text, '') AS source_move_name,
                   COALESCE(imported_move.state::text, '') AS source_move_state,
                   CASE
                       WHEN imported_move.state = 'posted' THEN 'Comptabilisée'
                       WHEN imported_move.id IS NOT NULL THEN 'Écriture brouillon'
                       ELSE 'Planifiée'
                   END AS representation_status,
                   CASE
                       WHEN imported_move.state = 'posted' THEN 'Comptabilisée'
                       WHEN imported_move.id IS NOT NULL THEN 'Écriture brouillon'
                       ELSE 'Planifiée'
                   END AS status,
                   COALESCE(imported_move.ref::text, '') AS move_ref,
                   round(schedule.amount::numeric, 2)::text AS expense_amount,
                   round(schedule.amount::numeric, 2)::text AS depreciation_amount,
                   round((schedule.depreciated_value + schedule.amount)::numeric, 2)::text AS accumulated_depreciation_amount,
                   round((schedule.depreciated_value + schedule.amount)::numeric, 2)::text AS accumulated_depreciation,
                   round(schedule.remaining_value::numeric, 2)::text AS net_book_value_after_line,
                   round(schedule.remaining_value::numeric, 2)::text AS imported_period_net_value,
                   COALESCE(imported_move.name::text, '') AS imported_move_name,
                   COALESCE(imported_move.id::text, '') AS imported_source_move_id
              FROM account_asset_line schedule
              JOIN account_asset asset ON asset.id = schedule.asset_id
              JOIN account_asset_profile profile ON profile.id = asset.profile_id
              LEFT JOIN account_move imported_move ON imported_move.id = schedule.move_id
             WHERE asset.company_id = %s
               AND schedule.line_date BETWEEN %s AND %s
               {filter_sql}
             ORDER BY asset.id, schedule.line_date, schedule.id
            """,
            [self.company_id.id, self.date_from, self.date_to, *filter_params],
        )
        rows = [dict(row) for row in self.env.cr.dictfetchall()]
        for row in rows:
            row["section"] = row.get("asset_name") or "Immobilisation"
        return rows

    def _deferred_schedule_rows(self):
        filter_sql, filter_params = self._deferred_schedule_filter_sql()
        self.env.cr.execute(
            f"""
            SELECT deferral.original_move_id::text AS source_original_move_id,
                   COALESCE(schedule.move_id, 0)::text AS source_deferred_move_id,
                   COALESCE(original_move.name::text, '') AS source_original_name,
                   COALESCE(deferred_move.name::text, '') AS source_deferred_name,
                   original_move.state AS source_original_state,
                   COALESCE(deferred_move.state, 'draft') AS source_deferred_state,
                   original_move.move_type AS source_original_move_type,
                   COALESCE(deferred_move.move_type, 'entry') AS source_deferred_move_type,
                   original_move.date::text AS original_date,
                   schedule.date::text AS deferred_date,
                   deferral.start_date::text AS deferred_start_date,
                   deferral.end_date::text AS deferred_end_date,
                   deferral.schedule_type,
                   schedule.phase AS schedule_phase,
                   CASE WHEN schedule.state = 'posted' THEN 'posted' ELSE 'scheduled' END AS representation_status,
                   CASE WHEN schedule.state = 'posted' THEN 'represented' ELSE 'review_required' END AS review_status,
                   COALESCE(deferral_account.code_store->>company.id::text, deferral_account.code_store->>'1', deferral_account.code_store::text, '') AS deferred_account_code,
                   COALESCE(deferral_account.name->>'fr_FR', deferral_account.name->>'en_US', deferral_account.name::text, '') AS deferred_account_name,
                   COALESCE(recognition_account.code_store->>company.id::text, recognition_account.code_store->>'1', recognition_account.code_store::text, '') AS counterpart_account_codes,
                   COALESCE(recognition_account.name->>'fr_FR', recognition_account.name->>'en_US', recognition_account.name::text, '') AS counterpart_account_names,
                   round(abs(schedule.recognition_balance)::numeric, 2)::text AS amount,
                   round(schedule.deferral_balance::numeric, 2)::text AS deferred_account_balance,
                   round(schedule.recognition_balance::numeric, 2)::text AS counterpart_balance,
                   COALESCE(original_move.name::text, '') AS imported_original_move_name,
                   COALESCE(deferred_move.name::text, '') AS imported_deferred_move_name
              FROM rebuild_account_deferral_line schedule
              JOIN rebuild_account_deferral deferral ON deferral.id = schedule.deferral_id
              JOIN res_company company ON company.id = schedule.company_id
              JOIN account_move original_move ON original_move.id = deferral.original_move_id
         LEFT JOIN account_move deferred_move ON deferred_move.id = schedule.move_id
              JOIN account_account deferral_account ON deferral_account.id = deferral.deferral_account_id
              JOIN account_account recognition_account ON recognition_account.id = schedule.recognition_account_id
             WHERE schedule.company_id = %s
               AND schedule.date BETWEEN %s AND %s
               {filter_sql}
             ORDER BY schedule.date, deferral.original_move_id, schedule.id
            """,
            [self.company_id.id, self.date_from, self.date_to, *filter_params],
        )
        rows = [dict(row) for row in self.env.cr.dictfetchall()]
        for row in rows:
            row["section"] = (
                "Produits constatés d’avance"
                if row.get("schedule_type") in {"revenue", "income"}
                else "Charges constatées d’avance"
            )
        return rows

    def _french_annual_rows(self, statement_keys=None, report_variant=""):
        tb = self._trial_balance_rows()

        def sum_bal(
            prefixes,
            account_types=None,
            positive=None,
            negative=None,
            excluded_prefixes=None,
        ):
            total = Decimal("0.00")
            count = 0
            for row in tb:
                balance = _amount(row["balance"])
                if not _matches(row, prefixes):
                    continue
                if excluded_prefixes and _matches(
                    row,
                    excluded_prefixes,
                ):
                    continue
                if account_types and row["account_type"] not in account_types:
                    continue
                if positive and balance <= 0:
                    continue
                if negative and balance >= 0:
                    continue
                total += balance
                count += int(row.get("move_line_count") or 0)
            return total, count

        def sum_types(
            account_types,
            prefixes=None,
            excluded_prefixes=None,
            positive=None,
            negative=None,
        ):
            total = Decimal("0.00")
            count = 0
            for item in tb:
                code = item.get("account_code") or ""
                if item.get("account_type") not in account_types:
                    continue
                if prefixes and not any(code.startswith(prefix) for prefix in prefixes):
                    continue
                if excluded_prefixes and any(
                    code.startswith(prefix)
                    for prefix in excluded_prefixes
                ):
                    continue
                balance = _amount(item["balance"])
                if positive and balance <= 0:
                    continue
                if negative and balance >= 0:
                    continue
                total += balance
                count += int(item.get("move_line_count") or 0)
            return total, count

        def row(
            statement_key,
            line_code,
            line_name,
            amount,
            formula,
            prefixes,
            count=0,
            gross=0,
            depreciation=0,
            presentation_role=None,
        ):
            return {
                "statement_key": statement_key,
                "line_code": line_code,
                "line_name": line_name,
                "source_formula": formula,
                "drilldown_account_prefixes": ",".join(prefixes),
                "move_line_count": str(count),
                "gross_amount": _amount_text(gross),
                "depreciation_amount": _amount_text(depreciation),
                "net_amount": _amount_text(amount),
                "amount": _amount_text(amount),
                "presentation_role": presentation_role,
            }

        fixed_types = {"asset_fixed", "asset_non_current"}
        fixed_net, fixed_count = sum_types(fixed_types)
        fixed_gross, fixed_gross_count = sum_types(
            fixed_types,
            excluded_prefixes=["28", "29"],
        )
        fixed_depr_balance, fixed_depr_count = sum_types(
            fixed_types,
            prefixes=["28", "29"],
        )
        current_asset_debits, current_asset_debit_count = sum_types(
            {"asset_current", "asset_receivable"},
            positive=True,
        )
        liability_debits, liability_debit_count = sum_types(
            {
                "liability_payable",
                "liability_current",
                "liability_non_current",
            },
            positive=True,
        )
        other_receivables = current_asset_debits + liability_debits
        other_receivable_count = (
            current_asset_debit_count + liability_debit_count
        )
        cash, cash_count = sum_types({"asset_cash"}, positive=True)
        total_assets = fixed_net + other_receivables + cash
        depreciation = -fixed_depr_balance

        capital, capital_count = sum_bal(["101"])
        other_equity_balance, other_equity_count = sum_types(
            {"equity", "equity_unaffected"},
            excluded_prefixes=["101"],
        )
        result_balance, result_count = sum_bal(["6", "7"])
        financial_debt_balance, financial_debt_count = sum_bal(
            ["16", "17", "455"],
            negative=True,
        )
        trade_payables, trade_payable_count = sum_bal(
            ["401", "403", "4081", "4088"],
            negative=True,
        )
        tax_social_debt, tax_social_count = sum_bal(
            ["42", "43", "44"],
            negative=True,
        )
        other_liability_credits, other_liability_credit_count = sum_types(
            {
                "liability_payable",
                "liability_current",
                "liability_non_current",
                "liability_credit_card",
            },
            excluded_prefixes=[
                "16",
                "17",
                "401",
                "403",
                "4081",
                "4088",
                "42",
                "43",
                "44",
                "455",
            ],
            negative=True,
        )
        asset_credits, asset_credit_count = sum_types(
            {"asset_current", "asset_receivable", "asset_cash"},
            negative=True,
        )
        other_debt = other_liability_credits + asset_credits
        other_debt_count = (
            other_liability_credit_count + asset_credit_count
        )
        financial_debt = -financial_debt_balance
        current_result = -result_balance
        equity = -capital - other_equity_balance + current_result
        total_debt = (
            financial_debt
            - trade_payables
            - tax_social_debt
            - other_debt
        )
        total_passif = equity + total_debt

        goods_sales, goods_sales_count = sum_bal(["707", "7097"])
        goods_production_sales, goods_production_sales_count = sum_bal(
            ["701", "702", "703", "704", "705", "7091", "7092", "7094", "7095"],
        )
        service_sales, service_sales_count = sum_bal(["706"])
        turnover_balance, turnover_count = sum_bal(["70"])
        turnover = -turnover_balance
        operating_income_balance, operating_income_count = sum_bal(
            ["70", "71", "72", "74", "75"],
            excluded_prefixes=["755"],
        )
        operating_income = -operating_income_balance
        goods_purchases, goods_purchases_count = sum_bal(["607"])
        external_charge_prefixes = [
            "601",
            "602",
            "603",
            "604",
            "605",
            "606",
            "608",
            "609",
            "61",
            "62",
        ]
        external_charges, external_charges_count = sum_bal(
            external_charge_prefixes,
        )
        taxes, taxes_count = sum_bal(["631", "633"])
        salaries, salaries_count = sum_bal(["641"])
        social_charges, social_charges_count = sum_bal(["645"])
        depreciation_expense, depreciation_expense_count = sum_bal(["681"])
        other_operating_charge_prefixes = [
            "651",
            "652",
            "653",
            "654",
            "656",
            "657",
            "658",
            "659",
        ]
        other_expenses, other_expenses_count = sum_bal(
            other_operating_charge_prefixes,
        )
        personnel_charges, personnel_charges_count = sum_bal(["64"])
        production_stocked_balance, production_stocked_count = sum_bal(["71"])
        production_capitalized_balance, production_capitalized_count = sum_bal(
            ["72"],
        )
        operating_subsidies_balance, operating_subsidies_count = sum_bal(
            ["74"],
        )
        operating_other_income_balance, operating_other_income_count = sum_bal(
            ["75"],
            excluded_prefixes=["755"],
        )
        operating_reversals_balance, operating_reversals_count = sum_bal(
            ["781"],
        )
        investment_grant_transfer_balance, investment_grant_transfer_count = (
            sum_bal(["777"])
        )
        disposal_proceeds_balance, disposal_proceeds_count = sum_bal(["775"])
        disposal_carrying_value, disposal_carrying_count = sum_bal(["675"])
        operating_expenses, operating_expense_count = sum_bal(
            ["60", "61", "62", "63", "64", "65", "68"],
        )
        financial_income, financial_income_count = sum_bal(["76"])
        financial_charges, financial_charges_count = sum_bal(["66"])
        financial_result = -financial_income - financial_charges
        exceptional_income, exceptional_income_count = sum_bal(["77"])
        exceptional_charges, exceptional_charges_count = sum_bal(["67"])
        sig_exceptional_income, sig_exceptional_income_count = sum_bal(
            ["77"],
            excluded_prefixes=["775", "777"],
        )
        sig_exceptional_charges, sig_exceptional_charges_count = sum_bal(
            ["67"],
            excluded_prefixes=["675"],
        )
        joint_operation_income, joint_operation_income_count = sum_bal(["755"])
        joint_operation_charges, joint_operation_charges_count = sum_bal(
            ["655"],
        )
        employee_profit_sharing, employee_profit_sharing_count = sum_bal(
            ["691"],
        )
        exceptional_result = -exceptional_income - exceptional_charges
        sig_exceptional_result = (
            -sig_exceptional_income
            - sig_exceptional_charges
        )
        income_tax, income_tax_count = sum_bal(["695"])
        total_products_balance, total_products_count = sum_bal(["7"])
        total_charges, total_charges_count = sum_bal(["6"])
        total_products = -total_products_balance
        goods_sales_amount = -goods_sales
        production_sold = turnover - goods_sales_amount
        production_stocked = -production_stocked_balance
        production_capitalized = -production_capitalized_balance
        production_for_period = (
            production_sold
            + production_stocked
            + production_capitalized
        )
        operating_subsidies = -operating_subsidies_balance
        operating_other_income = -operating_other_income_balance
        operating_reversals = -operating_reversals_balance
        investment_grant_transfer = -investment_grant_transfer_balance
        disposal_proceeds = -disposal_proceeds_balance
        joint_operation_income_amount = -joint_operation_income
        joint_operation_charges_amount = joint_operation_charges
        commercial_margin = goods_sales_amount - goods_purchases
        value_added = (
            commercial_margin
            + production_for_period
            - external_charges
        )
        ebe = (
            value_added
            + operating_subsidies
            - taxes
            - personnel_charges
        )
        operating_result = (
            value_added
            - taxes
            - personnel_charges
            + operating_subsidies
            + operating_other_income
            + operating_reversals
            + investment_grant_transfer
            + disposal_proceeds
            - depreciation_expense
            - other_expenses
            - disposal_carrying_value
        )
        current_result_before_tax = (
            operating_result
            + joint_operation_income_amount
            - joint_operation_charges_amount
            + financial_result
        )
        net_result = -result_balance
        all_depreciation_expense, all_depreciation_expense_count = sum_bal(
            ["68"],
        )
        all_reversals_balance, all_reversals_count = sum_bal(["78"])
        all_reversals = -all_reversals_balance
        caf = (
            net_result
            + all_depreciation_expense
            - all_reversals
            + disposal_carrying_value
            - disposal_proceeds
            - investment_grant_transfer
        )

        rows = [
            row("bilan_actif", "ACTIF_IMMO_CORP", "Immobilisations", fixed_net, "Comptes d’actif immobilisé, nets des amortissements et provisions", ["2"], fixed_count, fixed_gross, depreciation),
            row("bilan_actif", "ACTIF_AUTRES_CREANCES", "Stocks, créances et autres actifs courants", other_receivables, "Soldes débiteurs des comptes d’actif courant, de créances et de dettes", ["3", "4"], other_receivable_count),
            row("bilan_actif", "ACTIF_DISPONIBILITES", "Disponibilités", cash, "Comptes classés en trésorerie", ["5"], cash_count),
            row("bilan_actif", "ACTIF_TOTAL", "Total actif", total_assets, "Tous les comptes d’actif selon leur type comptable", ["2", "3", "4", "5"], fixed_count + other_receivable_count + cash_count, fixed_gross + other_receivables + cash, depreciation),
            row("bilan_passif", "PASSIF_CAPITAL", "Capital social", -capital, "101", ["101"], capital_count),
            row("bilan_passif", "PASSIF_RESERVES_REPORT", "Réserves, report à nouveau et autres capitaux propres", -other_equity_balance, "Autres comptes classés en capitaux propres", ["10", "11", "12", "13", "14"], other_equity_count),
            row("bilan_passif", "PASSIF_RESULTAT", "Résultat de l’exercice", current_result, "6 et 7", ["6", "7"], result_count),
            row("bilan_passif", "PASSIF_CAPITAUX_PROPRES", "Total des capitaux propres", equity, "Capitaux propres + résultat de l’exercice", ["1", "6", "7"], capital_count + other_equity_count + result_count, presentation_role="subtotal"),
            row("bilan_passif", "PASSIF_DETTES_FINANCIERES", "Emprunts et dettes financières diverses", financial_debt, "Soldes créditeurs 16/17 et comptes courants d’associés 455", ["16", "17", "455"], financial_debt_count),
            row("bilan_passif", "PASSIF_DETTES_FOURNISSEURS", "Dettes fournisseurs et comptes rattachés", -trade_payables, "Soldes créditeurs 401/403/4081/4088", ["401", "403", "4081", "4088"], trade_payable_count),
            row("bilan_passif", "PASSIF_DETTES_FISCALES_SOCIALES", "Dettes fiscales et sociales", -tax_social_debt, "Soldes créditeurs 42/43/44", ["42", "43", "44"], tax_social_count),
            row("bilan_passif", "PASSIF_AUTRES_DETTES", "Autres dettes et découverts", -other_debt, "Autres soldes créditeurs classés en passif", ["1", "4", "5"], other_debt_count),
            row("bilan_passif", "PASSIF_TOTAL_DETTES", "Total des dettes", total_debt, "Dettes financières, fournisseurs, fiscales, sociales et autres", ["1", "4", "5"], financial_debt_count + trade_payable_count + tax_social_count + other_debt_count, presentation_role="subtotal"),
            row("bilan_passif", "PASSIF_TOTAL", "Total passif", total_passif, "Capitaux propres + résultat + dettes", ["1", "4", "5", "6", "7"], capital_count + other_equity_count + result_count + financial_debt_count + trade_payable_count + tax_social_count + other_debt_count, presentation_role="total"),
            row("compte_resultat", "CR_VENTES_PRODUITS", "Production vendue — biens", -goods_production_sales, "701 à 705 nets des réductions correspondantes", ["701", "702", "703", "704", "705", "7091", "7092", "7094", "7095"], goods_production_sales_count),
            row("compte_resultat", "CR_SERVICES", "Prestations de services", -service_sales, "706", ["706"], service_sales_count),
            row("compte_resultat", "CR_CHIFFRE_AFFAIRES", "Chiffre d’affaires net", turnover, "70", ["70"], turnover_count),
            row("compte_resultat", "CR_AUTRES_PRODUITS_EXPLOITATION", "Autres produits d’exploitation", operating_other_income, "75 hors opérations en commun 755", ["75"], operating_other_income_count),
            row("compte_resultat", "CR_TOTAL_PRODUITS_EXPLOITATION", "Total des produits d’exploitation", operating_income, "70/71/72/74/75 hors opérations en commun", ["70", "71", "72", "74", "75"], operating_income_count),
            row("compte_resultat", "CR_ACHATS_MARCHANDISES", "Achats de marchandises", goods_purchases, "607", ["607"], goods_purchases_count),
            row("compte_resultat", "CR_CHARGES_EXTERNES", "Autres achats et charges externes", external_charges, "60 hors 607 + 61 + 62", external_charge_prefixes, external_charges_count),
            row("compte_resultat", "CR_IMPOTS_TAXES", "Impôts, taxes et versements assimilés", taxes, "631 + 633", ["631", "633"], taxes_count),
            row("compte_resultat", "CR_SALAIRES", "Salaires et traitements", salaries, "641", ["641"], salaries_count),
            row("compte_resultat", "CR_CHARGES_SOCIALES", "Charges sociales", social_charges, "645", ["645"], social_charges_count),
            row("compte_resultat", "CR_DOTATIONS_AMORTISSEMENTS", "Dotations aux amortissements", depreciation_expense, "681", ["681"], depreciation_expense_count),
            row("compte_resultat", "CR_AUTRES_CHARGES_EXPLOITATION", "Autres charges d’exploitation", other_expenses, "65 hors 655 et 675", other_operating_charge_prefixes, other_expenses_count),
            row("compte_resultat", "CR_TOTAL_CHARGES_EXPLOITATION", "Total charges d’exploitation", operating_expenses, "60 à 65 et 68", ["60", "61", "62", "63", "64", "65", "68"], operating_expense_count),
            row("compte_resultat", "CR_RESULTAT_EXPLOITATION", "Résultat d’exploitation", operating_result, "Produits d’exploitation - charges d’exploitation", ["70", "71", "72", "74", "75", "60", "61", "62", "63", "64", "65", "68"]),
            row("compte_resultat", "CR_PRODUITS_FINANCIERS", "Produits financiers", -financial_income, "76", ["76"], financial_income_count),
            row("compte_resultat", "CR_CHARGES_FINANCIERES", "Charges financières", financial_charges, "66", ["66"], financial_charges_count),
            row("compte_resultat", "CR_RESULTAT_FINANCIER", "Résultat financier", financial_result, "76 - 66", ["76", "66"], financial_income_count + financial_charges_count),
            row("compte_resultat", "CR_RESULTAT_COURANT_AVANT_IMPOT", "Résultat courant avant impôts", current_result_before_tax, "Résultat exploitation + résultat financier", ["70", "758", "60", "61", "62", "63", "64", "658", "681", "76", "66"]),
            row("compte_resultat", "CR_RESULTAT_EXCEPTIONNEL", "Résultat exceptionnel", exceptional_result, "77 - 67", ["77", "67"], exceptional_income_count + exceptional_charges_count),
            row("compte_resultat", "CR_IMPOTS_BENEFICES", "Impôts sur les bénéfices", income_tax, "695", ["695"], income_tax_count),
            row("compte_resultat", "CR_TOTAL_PRODUITS", "Total des produits", total_products, "Total des comptes de classe 7", ["7"], total_products_count, presentation_role="subtotal"),
            row("compte_resultat", "CR_TOTAL_CHARGES", "Total des charges", total_charges, "Total des comptes de classe 6", ["6"], total_charges_count, presentation_role="subtotal"),
            row("compte_resultat", "CR_RESULTAT_NET", "Résultat net de l’exercice", net_result, "Total des produits - total des charges", ["6", "7"], result_count, presentation_role="total"),
            row("sig_caf", "SIG_VALEUR_AJOUTEE", "Valeur ajoutée (I + II - III)", value_added, "Marge commerciale + production de l’exercice - consommations externes", ["70", "607", "606", "61", "62"], turnover_count + goods_purchases_count + external_charges_count, presentation_role="subtotal"),
            row("sig_caf", "SIG_MARGE_COMMERCIALE", "Marge commerciale (I)", commercial_margin, "Ventes de marchandises - coût d’achat des marchandises vendues", ["707", "7097", "607"], goods_sales_count + goods_purchases_count, presentation_role="subtotal"),
            row("sig_caf", "SIG_VENTES_MARCHANDISES", "Ventes de marchandises", goods_sales_amount, "707 net de 7097", ["707", "7097"], goods_sales_count),
            row("sig_caf", "SIG_COUT_ACHAT_MARCHANDISES", "Coût d’achat des marchandises vendues", goods_purchases, "607", ["607"], goods_purchases_count),
            row("sig_caf", "SIG_PRODUCTION_EXERCICE", "Production de l’exercice (II)", production_for_period, "Production vendue + production stockée + production immobilisée", ["70", "71", "72"], turnover_count + production_stocked_count + production_capitalized_count, presentation_role="subtotal"),
            row("sig_caf", "SIG_PRODUCTION_VENDUE", "Production vendue", production_sold, "Chiffre d’affaires hors ventes de marchandises", ["70"], turnover_count),
            row("sig_caf", "SIG_CHIFFRE_AFFAIRES_NET", "Montant net du chiffre d’affaires", turnover, "70", ["70"], turnover_count),
            row("sig_caf", "SIG_PRODUCTION_STOCKEE", "Production (dé)stockée", production_stocked, "71", ["71"], production_stocked_count),
            row("sig_caf", "SIG_PRODUCTION_IMMOBILISEE", "Production immobilisée", production_capitalized, "72", ["72"], production_capitalized_count),
            row("sig_caf", "SIG_CONSOMMATIONS_TIERS", "Consommations de l’exercice en provenance de tiers (III)", external_charges, "60 hors 607 + 61 + 62", external_charge_prefixes, external_charges_count),
            row("sig_caf", "SIG_EBE", "Excédent brut d’exploitation", ebe, "Valeur ajoutée + subventions - impôts et taxes - charges de personnel", ["70", "71", "72", "74", "60", "61", "62", "63", "64"], turnover_count + external_charges_count + operating_subsidies_count + taxes_count + personnel_charges_count, presentation_role="subtotal"),
            row("sig_caf", "SIG_SUBVENTIONS_EXPLOITATION", "Subventions d’exploitation", operating_subsidies, "74", ["74"], operating_subsidies_count),
            row("sig_caf", "SIG_IMPOTS_TAXES", "Impôts, taxes et versements assimilés", taxes, "631 + 633", ["631", "633"], taxes_count),
            row("sig_caf", "SIG_CHARGES_PERSONNEL", "Charges de personnel", personnel_charges, "64", ["64"], personnel_charges_count),
            row("sig_caf", "SIG_RESULTAT_EXPLOITATION", "Résultat d’exploitation", operating_result, "EBE + autres produits et reprises - dotations et autres charges", ["70", "71", "72", "74", "75", "77", "78", "60", "61", "62", "63", "64", "65", "67", "68"], operating_income_count + operating_expense_count + operating_reversals_count + disposal_proceeds_count + disposal_carrying_count, presentation_role="subtotal"),
            row("sig_caf", "SIG_AUTRES_PRODUITS_EXPLOITATION", "Autres produits d’exploitation", operating_other_income, "758", ["758"], operating_other_income_count),
            row("sig_caf", "SIG_REPRISES_EXPLOITATION", "Reprises sur amortissements, dépréciations et provisions d’exploitation", operating_reversals, "781", ["781"], operating_reversals_count),
            row("sig_caf", "SIG_TRANSFERT_SUBVENTIONS", "Quote-part des subventions d’investissement transférée au résultat", investment_grant_transfer, "777", ["777"], investment_grant_transfer_count),
            row("sig_caf", "SIG_PRODUITS_CESSIONS", "Produits des cessions d’immobilisations", disposal_proceeds, "775", ["775"], disposal_proceeds_count),
            row("sig_caf", "SIG_DOTATIONS_EXPLOITATION", "Dotations aux amortissements, dépréciations et provisions d’exploitation", depreciation_expense, "681", ["681"], depreciation_expense_count),
            row("sig_caf", "SIG_AUTRES_CHARGES", "Autres charges d’exploitation", other_expenses, "65 hors 655 et 675", other_operating_charge_prefixes, other_expenses_count),
            row("sig_caf", "SIG_VNC_CESSIONS", "Valeur comptable des immobilisations cédées", disposal_carrying_value, "675", ["675"], disposal_carrying_count),
            row("sig_caf", "SIG_RESULTAT_COURANT_AVANT_IMPOT", "Résultat courant avant impôts", current_result_before_tax, "Résultat d’exploitation + résultat financier + opérations en commun", ["70", "60", "61", "62", "63", "64", "65", "66", "68", "75", "76"], operating_income_count + operating_expense_count + financial_income_count + financial_charges_count + joint_operation_income_count + joint_operation_charges_count, presentation_role="subtotal"),
            row("sig_caf", "SIG_QUOTE_PART_PRODUITS_COMMUN", "Quote-part de résultat sur opérations faites en commun — produits", joint_operation_income_amount, "755", ["755"], joint_operation_income_count),
            row("sig_caf", "SIG_PRODUITS_FINANCIERS", "Produits financiers", -financial_income, "76", ["76"], financial_income_count),
            row("sig_caf", "SIG_QUOTE_PART_CHARGES_COMMUN", "Quote-part de résultat sur opérations faites en commun — charges", joint_operation_charges_amount, "655", ["655"], joint_operation_charges_count),
            row("sig_caf", "SIG_CHARGES_FINANCIERES", "Charges financières", financial_charges, "66", ["66"], financial_charges_count),
            row("sig_caf", "SIG_RESULTAT_EXCEPTIONNEL", "Résultat exceptionnel", sig_exceptional_result, "Produits exceptionnels - charges exceptionnelles", ["77", "67"], sig_exceptional_income_count + sig_exceptional_charges_count, presentation_role="subtotal"),
            row("sig_caf", "SIG_PRODUITS_EXCEPTIONNELS", "Produits exceptionnels", -sig_exceptional_income, "77 hors 775 et 777", ["77"], sig_exceptional_income_count),
            row("sig_caf", "SIG_CHARGES_EXCEPTIONNELLES", "Charges exceptionnelles", sig_exceptional_charges, "67 hors 675", ["67"], sig_exceptional_charges_count),
            row("sig_caf", "SIG_RESULTAT_NET", "Résultat de l’exercice", net_result, "Solde des comptes 6 et 7", ["6", "7"], result_count, presentation_role="total"),
            row("sig_caf", "SIG_PARTICIPATION_SALARIES", "Participation des salariés", employee_profit_sharing, "691", ["691"], employee_profit_sharing_count),
            row("sig_caf", "SIG_IMPOT_BENEFICES", "Impôt sur les bénéfices", income_tax, "695", ["695"], income_tax_count),
            row("sig_caf", "CAF_RESULTAT_NET", "CAF — Résultat net comptable", net_result, "Point de départ de la CAF", ["6", "7"], result_count, presentation_role="subtotal"),
            row("sig_caf", "CAF_DOTATIONS", "CAF — (+) Dotations aux amortissements, dépréciations et provisions", all_depreciation_expense, "68", ["68"], all_depreciation_expense_count),
            row("sig_caf", "CAF_REPRISES", "CAF — (-) Reprises sur amortissements, dépréciations et provisions", all_reversals, "78", ["78"], all_reversals_count),
            row("sig_caf", "CAF_VNC_CESSIONS", "CAF — (+) Valeur comptable des immobilisations cédées", disposal_carrying_value, "675", ["675"], disposal_carrying_count),
            row("sig_caf", "CAF_PRODUITS_CESSIONS", "CAF — (-) Produits des cessions d’immobilisations", disposal_proceeds, "775", ["775"], disposal_proceeds_count),
            row("sig_caf", "CAF_TRANSFERT_SUBVENTIONS", "CAF — (-) Quote-part des subventions d’investissement transférée", investment_grant_transfer, "777", ["777"], investment_grant_transfer_count),
            row("sig_caf", "SIG_CAPACITE_AUTOFINANCEMENT", "Capacité d’autofinancement", caf, "Résultat net + charges non décaissées - produits non encaissés", ["6", "7", "68", "78", "675", "775", "777"], result_count + all_depreciation_expense_count + all_reversals_count + disposal_carrying_count + disposal_proceeds_count + investment_grant_transfer_count, presentation_role="total"),
        ]
        for item in rows:
            if item["statement_key"] != "compte_resultat":
                continue
            item["section"] = FRENCH_PROFIT_LOSS_SECTIONS.get(
                item["line_code"],
                "Compte de résultat",
            )
            if item["line_code"] == "CR_RESULTAT_NET":
                item["presentation_role"] = "total"
            elif item["line_code"] in FRENCH_PROFIT_LOSS_SUBTOTALS:
                item["presentation_role"] = "subtotal"
        self._attach_statement_account_breakdowns(rows, tb)
        if statement_keys:
            rows = [item for item in rows if item["statement_key"] in statement_keys]
        if report_variant:
            for item in rows:
                item["report_variant"] = report_variant
                item["applicability_basis"] = self._report_variant_basis()
        return rows

    def _attach_statement_account_breakdowns(self, rows, trial_balance_rows):
        """Attach only account contributions that reconcile to their line."""
        self.ensure_one()
        account_ids = [
            int(item["account_id"])
            for item in trial_balance_rows
            if item.get("account_id")
        ]
        accounts = self.env["account.account"].browse(account_ids)
        account_by_id = {
            account.id: account.with_company(self.company_id)
            for account in accounts
        }
        for row in rows:
            if row.get("presentation_role") in {"subtotal", "total"}:
                continue
            prefixes = [
                prefix.strip()
                for prefix in (
                    row.get("drilldown_account_prefixes") or ""
                ).split(",")
                if prefix.strip()
            ]
            if not prefixes:
                continue
            contributions = [
                item
                for item in trial_balance_rows
                if _matches(item, prefixes)
                and _amount(item.get("balance"))
                and int(item.get("move_line_count") or 0)
            ]
            if not contributions:
                continue
            raw_total = sum(
                (_amount(item.get("balance")) for item in contributions),
                Decimal("0.00"),
            )
            statement_amount = _amount(
                row.get("amount") or row.get("net_amount"),
            )
            if abs(statement_amount - raw_total) <= Decimal("0.01"):
                sign = Decimal("1.00")
            elif abs(statement_amount + raw_total) <= Decimal("0.01"):
                sign = Decimal("-1.00")
            else:
                # Derived totals and conditionally filtered balances remain
                # calculation rows until their exact source rule is available.
                continue
            breakdown = []
            for item in contributions:
                account = account_by_id.get(int(item["account_id"]))
                groups = []
                group = account.group_id if account else False
                while group:
                    group_code = str(group.code_prefix_start or "")
                    if (
                        group.code_prefix_end
                        and group.code_prefix_end != group.code_prefix_start
                    ):
                        group_code += f"-{group.code_prefix_end}"
                    groups.append({
                        "id": group.id,
                        "code": group_code,
                        "name": group.with_context(lang="fr_FR").name,
                    })
                    group = group.parent_id
                breakdown.append({
                    "account_id": int(item["account_id"]),
                    "source_account_id": item.get("source_account_id") or "",
                    "account_code": item.get("account_code") or "",
                    "account_name": item.get("account_name") or "",
                    "move_line_count": int(item.get("move_line_count") or 0),
                    "amount": _amount_text(
                        _amount(item.get("balance")) * sign,
                    ),
                    "group_chain": list(reversed(groups)),
                })
            row["account_breakdown"] = breakdown

    def _french_tax_package_rows(self):
        period_key = "Fiscal year 2024-01-10 to 2025-09-30"
        if fields.Date.to_string(self.date_from) != "2024-01-10" or fields.Date.to_string(self.date_to) != "2025-09-30":
            return []
        self.env.cr.execute(
            """
            SELECT form_code,
                   form_name,
                   field_code,
                   field_label,
                   source_kind,
                   source_formula,
                   COALESCE(source_report_line_code, '') AS source_report_line_code,
                   COALESCE(drilldown_account_prefixes, '') AS drilldown_account_prefixes,
                   move_line_count::text AS move_line_count,
                   quantity::text AS quantity,
                   round(amount::numeric, 2)::text AS amount,
                   round(rounded_amount::numeric, 2)::text AS rounded_amount,
                   COALESCE(round(benchmark_amount::numeric, 2)::text, '') AS benchmark_amount,
                   COALESCE(round(ledger_amount::numeric, 2)::text, '') AS ledger_amount,
                   COALESCE(round(difference_amount::numeric, 2)::text, '') AS difference_amount,
                   COALESCE(difference_classification, '') AS difference_classification,
                   COALESCE(value_text, '') AS value_text,
                   review_status
              FROM rebuild_account_french_tax_package_line
             WHERE company_id = %s
               AND period_key = %s
             ORDER BY form_code, line_sequence, field_code
            """,
            [self.company_id.id, period_key],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]


class RebuildAccountReportPreviewLine(models.TransientModel):
    _name = "rebuild.account.report.preview.line"
    _description = "USL Dynamic Accounting Report Preview Line"
    _order = "sequence, id"

    wizard_id = fields.Many2one(
        "rebuild.account.report.export.wizard",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    date = fields.Date(readonly=True)
    section = fields.Char(readonly=True)
    line_code = fields.Char(readonly=True)
    label = fields.Char(readonly=True)
    account_code = fields.Char(readonly=True)
    account_name = fields.Char(readonly=True)
    partner_name = fields.Char(readonly=True)
    move_name = fields.Char(readonly=True)
    opening_balance = fields.Monetary(readonly=True)
    debit = fields.Monetary(readonly=True)
    credit = fields.Monetary(readonly=True)
    movement = fields.Monetary(readonly=True)
    closing_balance = fields.Monetary(readonly=True)
    balance = fields.Monetary(readonly=True)
    residual = fields.Monetary(readonly=True)
    comparison_value = fields.Monetary(readonly=True)
    difference = fields.Monetary(readonly=True)
    record_count = fields.Integer(readonly=True)
    is_group = fields.Boolean(readonly=True)
    level = fields.Integer(readonly=True)
    group_key = fields.Char(readonly=True)
    parent_group_key = fields.Char(readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    row_json = fields.Text(readonly=True)

    def action_open_sources(self):
        self.ensure_one()
        if not self.wizard_id:
            message = (
                "Preview source drill-down requires the report wizard context."
            )
            raise UserError(message)
        return self.wizard_id._preview_source_action(self)

    def action_toggle_group(self):
        self.ensure_one()
        if not self.is_group:
            return self.wizard_id.action_preview_report()
        return self.wizard_id._toggle_preview_group(self.group_key)

    def _row_payload(self):
        self.ensure_one()
        if not self.row_json:
            return {}
        try:
            payload = json.loads(self.row_json)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}
