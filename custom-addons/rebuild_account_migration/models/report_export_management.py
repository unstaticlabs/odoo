"""Bank reconciliation, currency, management summary and analytic row sources."""

from decimal import Decimal

from odoo import models

from .report_export_wizard import (
    ACCOUNT_CODE_SQL,
    ACCOUNT_NAME_SQL,
    _amount,
    _amount_text,
)


class RebuildAccountReportExportWizard(models.TransientModel):
    _inherit = "rebuild.account.report.export.wizard"

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
