"""SQL scope helpers, FEC payload and ledger-family row sources (trial balance, ledgers, partners, open items, aged balances, balance sheet)."""

import csv
import hashlib
import io
from decimal import Decimal

from odoo import models
from odoo.exceptions import AccessError, UserError

from .report_export_wizard import (
    ACCOUNT_CODE_SQL,
    ACCOUNT_NAME_SQL,
    FRENCH_BALANCE_SUPPLIER_PREFIXES,
    FRENCH_BALANCE_SUSPENSE_ASSET_PREFIXES,
    _amount,
    _amount_text,
    _fec_amount,
)


class RebuildAccountReportExportWizard(models.TransientModel):
    _inherit = "rebuild.account.report.export.wizard"

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
                "OR schedule.recognition_account_id IN %s)",
            )
            params.extend([account_ids, account_ids])
        return "\n               ".join(clauses), params

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
            amount = _amount(row["closing_balance"])
            statement_key = self._french_balance_sheet_side(
                row["account_code"],
                account_type,
                amount,
            )
            if not statement_key:
                continue
            is_asset = statement_key == "bilan_actif"
            rows.append({
                "statement": "Bilan",
                "statement_key": statement_key,
                "statement_side": "Actif" if is_asset else "Passif",
                "section": self._balance_sheet_section(
                    account_type,
                    statement_key,
                ),
                "account_code": row["account_code"],
                "account_name": row["account_name"],
                "account_type": account_type,
                "amount": _amount_text(amount if is_asset else -amount),
            })
        result = sum(
            -_amount(row["closing_balance"])
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

    @staticmethod
    def _french_balance_sheet_side(account_code, account_type, balance):
        """Apply French Bilan prefix D/C rules to one closing balance."""
        code = str(account_code or "").strip()
        account_type = account_type or ""

        # Odoo's French Bilan nets these prefixes on their statutory side
        # instead of reclassifying an exceptional opposite-sign balance.
        # A debit supplier balance therefore reduces supplier debt on the
        # Passif, while a credit suspense balance reduces current assets.
        if code.startswith(FRENCH_BALANCE_SUPPLIER_PREFIXES):
            return "bilan_passif"
        if code.startswith(FRENCH_BALANCE_SUSPENSE_ASSET_PREFIXES):
            return "bilan_actif"

        # These prefixes are fixed to the Actif even with a credit balance:
        # classes 2/3 include depreciation provisions, while 49/59 are the
        # corresponding impairment accounts for receivables and treasury.
        if code.startswith(("109", "2", "3", "49", "59")):
            return "bilan_actif"
        if code.startswith("1"):
            return "bilan_passif"
        if code.startswith(("4", "5")):
            if balance > 0:
                return "bilan_actif"
            if balance < 0:
                return "bilan_passif"

        # Preserve a deterministic fallback for custom/non-PCG accounts while
        # retaining the same debit/credit inversion semantics.
        if account_type.startswith("asset"):
            return "bilan_actif" if balance >= 0 else "bilan_passif"
        if account_type.startswith("liability"):
            return "bilan_passif" if balance <= 0 else "bilan_actif"
        if account_type.startswith("equity"):
            return "bilan_passif"
        return False

    @staticmethod
    def _balance_sheet_section(account_type, statement_key):
        if statement_key == "bilan_actif" and account_type in (
            "asset_fixed",
            "asset_non_current",
        ):
            return "Immobilisations"
        if statement_key == "bilan_actif":
            return "Actif circulant"
        if account_type.startswith("equity"):
            return "Capitaux propres"
        return "Dettes et passifs"
