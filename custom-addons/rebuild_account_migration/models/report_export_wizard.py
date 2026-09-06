import calendar
import csv
import hashlib
import io
import json
from datetime import date, timedelta
from decimal import Decimal

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

FRENCH_BALANCE_SUPPLIER_PREFIXES = ("400", "401", "402", "403", "408")
FRENCH_BALANCE_SUSPENSE_ASSET_PREFIXES = ("471", "472", "474", "475")

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
    "adjustment",
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
                "linked closing workspace.",
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
