"""Tax report, grouped tax and EU tax row sources."""

from decimal import Decimal

from odoo import fields, models

from .report_export_wizard import (
    ACCOUNT_CODE_SQL,
    ACCOUNT_NAME_SQL,
    _amount,
    _amount_text,
)


class RebuildAccountReportExportWizard(models.TransientModel):
    _inherit = "rebuild.account.report.export.wizard"

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
