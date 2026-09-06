"""Column definitions and XLSX rendering of the shared report rows."""

import io
from decimal import ROUND_HALF_UP, Decimal

from odoo import fields, models
from odoo.exceptions import UserError

from .report_export_wizard import DATE_REPORT_FIELDS, MONETARY_REPORT_FIELDS


class RebuildAccountReportExportWizard(models.TransientModel):
    _inherit = "rebuild.account.report.export.wizard"

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
            "acquisition_date": "Date d’acquisition",
            "original_value": "Valeur d’origine",
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
            "state": "Statut",
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
            f"{self._display_export_datetime(metadata['generated_at'])}",
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
