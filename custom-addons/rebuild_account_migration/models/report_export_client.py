"""Interactive report workbench payloads consumed by the browser client."""

from odoo import (
    Command,
    api,
    fields,
    models,
)
from odoo.exceptions import AccessError, UserError

from .report_export_wizard import (
    CANONICAL_REPORT_TYPES,
    MULTI_COMPANY_AGGREGATE_KEYS,
    ZERO_ACCOUNT_FILTER_REPORT_TYPES,
    _amount,
)


class RebuildAccountReportExportWizard(models.TransientModel):
    _inherit = "rebuild.account.report.export.wizard"

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
                    and line.group_key in collapsed_groups,
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
                (export_format == "pdf" and not definition.supports_pdf)
                or (export_format == "xlsx" and not definition.supports_xlsx)
            )
        ):
            raise UserError(
                f"{export_format.upper()} is disabled by the active "
                f"{definition.name} definition.",
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
