"""PDF rendering of the shared report rows through the governed accounting statement template."""

from decimal import ROUND_HALF_UP, Decimal

from odoo import fields, models

from .report_export_wizard import DATE_REPORT_FIELDS, MONETARY_REPORT_FIELDS


class RebuildAccountReportExportWizard(models.TransientModel):
    _inherit = "rebuild.account.report.export.wizard"

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
            "sig_caf_2024": "metrics",
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
        final_rows = []
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
            if (
                self.report_type in {"fixed_assets", "fixed_asset_group_account"}
                and role == "total"
                and str(row_label).strip() == "Total des immobilisations"
            ):
                final_rows.append(rendered_row)
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
        if final_rows and sections:
            list(sections.values())[-1]["rows"].extend(final_rows)

        if self.report_type in {"balance_sheet", "french_balance_sheet_2024"}:
            total_field = (
                "net_amount"
                if self.report_type == "french_balance_sheet_2024"
                else "amount"
            )
            summary_controls = []
            for line_code, label in (
                ("ACTIF_TOTAL", "Total actif"),
                ("PASSIF_TOTAL", "Total passif"),
            ):
                total_row = next(
                    (row for row in rows if row.get("line_code") == line_code),
                    None,
                )
                if total_row:
                    summary_controls.append({
                        "label": label,
                        "value": display_value(total_row, total_field),
                        "status": "neutral",
                    })
            controls = [*summary_controls, *controls]
        if not sections:
            sections["main"] = {
                "key": "section_1",
                "title": "",
                "break_before": False,
                "continuation_label": "Suite",
                "rows": [{
                    "role": "empty",
                    "values": {
                        "label": "Aucune donnée pour le périmètre sélectionné.",
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
        if self.hide_zero_accounts:
            context.append("Lignes à zéro masquées")
        if self.comparison_mode != "none":
            context.append(
                "Comparaison : "
                f"{self._display_export_date(metadata['comparison_date_from'])}"
                " – "
                f"{self._display_export_date(metadata['comparison_date_to'])}",
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
        qualification_label = self.env.context.get(
            "usl_document_qualification_label",
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
                **(
                    {"qualification_label": str(qualification_label)}
                    if qualification_label
                    else {}
                ),
                **({"front_matter": front_matter} if front_matter else {}),
            },
            locale,
            assets,
        )
        return result if return_result else result["pdf"]
