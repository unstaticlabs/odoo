"""Shared row pipeline: source dispatch, zero filtering, company aggregation, grouping, hierarchy and comparison columns."""

from decimal import Decimal

from odoo import Command, models
from odoo.exceptions import UserError

from .report_export_wizard import (
    FRENCH_PROFIT_LOSS_SECTION_TOTALS,
    MONETARY_REPORT_FIELDS,
    MULTI_COMPANY_AGGREGATE_KEYS,
    ZERO_ACCOUNT_FILTER_REPORT_TYPES,
    _amount,
    _amount_text,
)


class RebuildAccountReportExportWizard(models.TransientModel):
    _inherit = "rebuild.account.report.export.wizard"

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
        elif self.report_type in {
            "balance_sheet",
            "french_annual",
            "french_balance_sheet_2024",
        }:
            value_field = (
                "amount"
                if self.report_type == "balance_sheet"
                else "net_amount"
            )
            totals = {
                row.get("line_code"): _amount(row.get(value_field))
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
                "label": "Écart actif − passif",
                value_field: _amount_text(difference),
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
            side_header = {
                "statement_key": side_key,
                "statement_side": side_label,
                "label": side_label,
                "is_group": "true",
                "group_key": side_group_key,
                "row_level": 0,
                "presentation_role": "section",
            }
            if total_row:
                side_header["amount"] = total_row.get("amount")
            result.append(side_header)
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
