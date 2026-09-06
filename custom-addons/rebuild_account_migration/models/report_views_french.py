"""French statement and tax package presentation lines of the report workbench."""

from odoo import fields, models, tools
from odoo.exceptions import UserError

from .report_views import (
    FIRST_FISCAL_YEAR_PERIOD_KEY,
    PERIOD_CASE_SQL,
    _base_journal_item_domain,
    _journal_items_action,
    _prefix_domain,
)


class RebuildAccountFrenchStatementLine(models.Model):
    _name = "rebuild.account.french.statement.line"
    _description = "USL French Annual Statement Line"
    _auto = False
    _order = "company_id, period_key, statement_key, line_sequence, line_code"

    company_id = fields.Many2one("res.company", readonly=True)
    source_company_id = fields.Integer(readonly=True)
    company_currency_id = fields.Many2one("res.currency", readonly=True)
    period_key = fields.Char(readonly=True)
    statement_key = fields.Char(readonly=True)
    statement_name = fields.Char(readonly=True)
    line_sequence = fields.Integer(readonly=True)
    line_code = fields.Char(readonly=True)
    line_name = fields.Char(readonly=True)
    source_formula = fields.Char(readonly=True)
    drilldown_account_prefixes = fields.Char(readonly=True)
    move_line_count = fields.Integer(readonly=True)
    gross_amount = fields.Monetary(currency_field="company_currency_id", readonly=True)
    depreciation_amount = fields.Monetary(currency_field="company_currency_id", readonly=True)
    net_amount = fields.Monetary(currency_field="company_currency_id", readonly=True)
    amount = fields.Monetary(currency_field="company_currency_id", readonly=True)

    def action_open_journal_items(self):
        self.ensure_one()
        domain = _base_journal_item_domain(self)
        accounts = self._drilldown_accounts()
        if accounts:
            domain = [
                *domain,
                ("account_id", "in", accounts.ids),
            ]
        return _journal_items_action(
            self,
            domain,
            name=f"{self.statement_name} - {self.line_name}",
        )

    def _drilldown_accounts(self):
        self.ensure_one()
        TrialBalance = self.env["rebuild.account.trial.balance.line"]
        base_domain = (
            fields.Domain("company_id", "=", self.company_id.id)
            & fields.Domain("period_key", "=", self.period_key)
        )
        line_code = self.line_code

        if line_code in ("ACTIF_IMMO_CORP",):
            trial_balance_domain = base_domain & _prefix_domain("account_code", ["21", "28"])
        elif line_code in ("ACTIF_AUTRES_CREANCES",):
            trial_balance_domain = (
                base_domain
                & _prefix_domain("account_code", ["4"])
                & fields.Domain("account_type", "in", ["asset_current", "asset_receivable"])
                & fields.Domain("closing_balance", ">", 0)
            )
        elif line_code in ("ACTIF_DISPONIBILITES",):
            trial_balance_domain = (
                base_domain
                & _prefix_domain("account_code", ["5"])
                & fields.Domain("account_type", "=", "asset_cash")
            )
        elif line_code in ("ACTIF_TOTAL",):
            trial_balance_domain = base_domain & (
                _prefix_domain("account_code", ["21", "28"])
                | (
                    _prefix_domain("account_code", ["4"])
                    & fields.Domain("account_type", "in", ["asset_current", "asset_receivable"])
                    & fields.Domain("closing_balance", ">", 0)
                )
                | (
                    _prefix_domain("account_code", ["5"])
                    & fields.Domain("account_type", "=", "asset_cash")
                )
            )
        elif line_code in ("PASSIF_COMPTE_COURANT_ASSOCIE",):
            trial_balance_domain = (
                base_domain
                & _prefix_domain("account_code", ["455"])
                & fields.Domain("closing_balance", "<", 0)
            )
        elif line_code in ("PASSIF_DETTES_FISCALES_SOCIALES",):
            trial_balance_domain = (
                base_domain
                & _prefix_domain("account_code", ["42", "43", "44"])
                & fields.Domain("closing_balance", "<", 0)
            )
        elif line_code in ("PASSIF_TOTAL_DETTES",):
            trial_balance_domain = (
                base_domain
                & _prefix_domain("account_code", ["455", "42", "43", "44"])
                & fields.Domain("closing_balance", "<", 0)
            )
        elif line_code in ("PASSIF_TOTAL",):
            trial_balance_domain = base_domain & (
                _prefix_domain("account_code", ["101", "6", "7"])
                | (
                    _prefix_domain("account_code", ["455", "42", "43", "44"])
                    & fields.Domain("closing_balance", "<", 0)
                )
            )
        else:
            prefixes = [
                prefix.strip()
                for prefix in (self.drilldown_account_prefixes or "").split(",")
                if prefix.strip()
            ]
            if not prefixes:
                return self.env["account.account"]
            trial_balance_domain = base_domain & _prefix_domain("account_code", prefixes)

        return TrialBalance.search(trial_balance_domain).mapped("account_id")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                WITH balances AS (
                    SELECT line.company_id,
                           company.id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           {PERIOD_CASE_SQL} AS period_key,
                           COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text) AS account_code,
                           account.account_type,
                           count(line.id) AS move_line_count,
                           round(sum(line.balance)::numeric, 2) AS balance
                      FROM account_move_line line
                      JOIN account_move move ON move.id = line.move_id
                      JOIN res_company company ON company.id = line.company_id
                      JOIN account_account account ON account.id = line.account_id
                 WHERE move.state = 'posted'
                     GROUP BY line.company_id,
                              company.id,
                              company.currency_id,
                              {PERIOD_CASE_SQL},
                              COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text),
                              account.account_type
                ),
                line_sources AS (
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'bilan_actif' AS statement_key,
                           'Bilan Actif' AS statement_name,
                           10 AS line_sequence,
                           'ACTIF_IMMO_CORP' AS line_code,
                           'Immobilisations corporelles' AS line_name,
                           'Comptes 21 diminués des amortissements 28' AS source_formula,
                           '21,28' AS drilldown_account_prefixes,
                           move_line_count,
                           CASE WHEN account_code LIKE '21%' THEN balance ELSE 0 END AS gross_component,
                           CASE WHEN account_code LIKE '28%' THEN -balance ELSE 0 END AS depreciation_component,
                           balance AS net_component,
                           balance AS amount_component
                      FROM balances
                     WHERE account_code LIKE '21%' OR account_code LIKE '28%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'bilan_actif', 'Bilan Actif', 20,
                           'ACTIF_AUTRES_CREANCES', 'Autres créances',
                           'Soldes débiteurs de classe 4 hors trésorerie', '4',
                           move_line_count, balance, 0, balance, balance
                      FROM balances
                     WHERE account_code LIKE '4%'
                       AND account_type IN ('asset_current', 'asset_receivable')
                       AND balance > 0
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'bilan_actif', 'Bilan Actif', 30,
                           'ACTIF_DISPONIBILITES', 'Disponibilités',
                           'Comptes de trésorerie 5 classés asset_cash', '5',
                           move_line_count, balance, 0, balance, balance
                      FROM balances
                     WHERE account_code LIKE '5%'
                       AND account_type = 'asset_cash'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'bilan_actif', 'Bilan Actif', 90,
                           'ACTIF_TOTAL', 'Total actif',
                           'Immobilisations nettes + autres créances + disponibilités', '21,28,4,5',
                           move_line_count,
                           CASE WHEN account_code LIKE '28%' THEN 0 ELSE balance END,
                           CASE WHEN account_code LIKE '28%' THEN -balance ELSE 0 END,
                           balance,
                           balance
                      FROM balances
                     WHERE account_code LIKE '21%'
                        OR account_code LIKE '28%'
                        OR (account_code LIKE '4%' AND account_type IN ('asset_current', 'asset_receivable') AND balance > 0)
                        OR (account_code LIKE '5%' AND account_type = 'asset_cash')
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'bilan_passif', 'Bilan Passif', 10,
                           'PASSIF_CAPITAL', 'Capital social',
                           'Comptes 101', '101',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '101%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'bilan_passif', 'Bilan Passif', 20,
                           'PASSIF_RESULTAT', 'Résultat de l’exercice',
                           'Résultat net des comptes 6 et 7', '6,7',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '6%' OR account_code LIKE '7%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'bilan_passif', 'Bilan Passif', 30,
                           'PASSIF_CAPITAUX_PROPRES', 'Capitaux propres',
                           'Capital social + résultat de l’exercice', '101,6,7',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '101%' OR account_code LIKE '6%' OR account_code LIKE '7%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'bilan_passif', 'Bilan Passif', 40,
                           'PASSIF_DETTES_FINANCIERES', 'Emprunts et dettes financières diverses',
                           'Comptes 16/17 et comptes courants d’associés 455', '16,17,455',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE (
                           account_code LIKE '16%'
                        OR account_code LIKE '17%'
                        OR account_code LIKE '455%'
                     )
                       AND balance < 0
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'bilan_passif', 'Bilan Passif', 50,
                           'PASSIF_DETTES_FISCALES_SOCIALES', 'Dettes fiscales et sociales',
                           'Soldes créditeurs des comptes 42, 43 et 44', '42,43,44',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE (account_code LIKE '42%' OR account_code LIKE '43%' OR account_code LIKE '44%')
                       AND balance < 0
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'bilan_passif', 'Bilan Passif', 80,
                           'PASSIF_TOTAL_DETTES', 'Total des dettes',
                           'Dettes financières, fournisseurs, fiscales et sociales', '16,17,455,40,42,43,44',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE ((account_code LIKE '16%'
                             OR account_code LIKE '17%'
                             OR account_code LIKE '455%'
                             OR account_code LIKE '40%'
                             OR account_code LIKE '42%'
                             OR account_code LIKE '43%'
                             OR account_code LIKE '44%')
                            AND balance < 0)
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'bilan_passif', 'Bilan Passif', 90,
                           'PASSIF_TOTAL', 'Total passif',
                           'Capitaux propres + dettes', '101,6,7,16,17,455,40,42,43,44',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '101%'
                        OR account_code LIKE '6%'
                        OR account_code LIKE '7%'
                        OR ((account_code LIKE '16%'
                             OR account_code LIKE '17%'
                             OR account_code LIKE '455%'
                             OR account_code LIKE '40%'
                             OR account_code LIKE '42%'
                             OR account_code LIKE '43%'
                             OR account_code LIKE '44%')
                            AND balance < 0)
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 10,
                           'CR_VENTES_PRODUITS', 'Production vendue — biens',
                           'Comptes 701', '701',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '701%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 20,
                           'CR_SERVICES', 'Prestations de services',
                           'Comptes 706', '706',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '706%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 30,
                           'CR_CHIFFRE_AFFAIRES', 'Chiffre d’affaires net',
                           'Comptes 70', '70',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '70%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 40,
                           'CR_TOTAL_PRODUITS_EXPLOITATION', 'Total des produits d’exploitation',
                           'Comptes 70/71/72/74/75 hors 755', '70,71,72,74,75',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '70%'
                        OR account_code LIKE '71%'
                        OR account_code LIKE '72%'
                        OR account_code LIKE '74%'
                        OR (
                            account_code LIKE '75%'
                            AND account_code NOT LIKE '755%'
                        )
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 35,
                           'CR_AUTRES_PRODUITS_EXPLOITATION', 'Autres produits d’exploitation',
                           'Comptes 75 hors opérations en commun 755', '75',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '75%'
                       AND account_code NOT LIKE '755%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 50,
                           'CR_ACHATS_MARCHANDISES', 'Achats de marchandises',
                           'Comptes 607', '607',
                           move_line_count, 0, 0, balance, balance
                      FROM balances
                     WHERE account_code LIKE '607%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 60,
                           'CR_CHARGES_EXTERNES', 'Autres achats et charges externes',
                           'Comptes 606, 61 et 62', '606,61,62',
                           move_line_count, 0, 0, balance, balance
                      FROM balances
                     WHERE account_code LIKE '606%' OR account_code LIKE '61%' OR account_code LIKE '62%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 70,
                           'CR_IMPOTS_TAXES', 'Impôts, taxes et versements assimilés',
                           'Comptes 631 et 633', '631,633',
                           move_line_count, 0, 0, balance, balance
                      FROM balances
                     WHERE account_code LIKE '631%' OR account_code LIKE '633%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 80,
                           'CR_SALAIRES', 'Salaires et traitements',
                           'Comptes 641', '641',
                           move_line_count, 0, 0, balance, balance
                      FROM balances
                     WHERE account_code LIKE '641%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 90,
                           'CR_CHARGES_SOCIALES', 'Charges sociales',
                           'Comptes 645', '645',
                           move_line_count, 0, 0, balance, balance
                      FROM balances
                     WHERE account_code LIKE '645%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 100,
                           'CR_DOTATIONS_AMORTISSEMENTS', 'Dotations aux amortissements',
                           'Comptes 681', '681',
                           move_line_count, 0, 0, balance, balance
                      FROM balances
                     WHERE account_code LIKE '681%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 110,
                           'CR_AUTRES_CHARGES_EXPLOITATION', 'Autres charges d’exploitation',
                           'Comptes 658', '658',
                           move_line_count, 0, 0, balance, balance
                      FROM balances
                     WHERE account_code LIKE '658%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 120,
                           'CR_TOTAL_CHARGES_EXPLOITATION', 'Total des charges d’exploitation',
                           'Comptes 60, 61, 62, 63, 64, 658 et 681', '60,61,62,63,64,658,681',
                           move_line_count, 0, 0, balance, balance
                      FROM balances
                     WHERE account_code LIKE '60%'
                        OR account_code LIKE '61%'
                        OR account_code LIKE '62%'
                        OR account_code LIKE '63%'
                        OR account_code LIKE '64%'
                        OR account_code LIKE '658%'
                        OR account_code LIKE '681%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 130,
                           'CR_RESULTAT_EXPLOITATION', 'Résultat d’exploitation',
                           'Produits d’exploitation moins charges d’exploitation', '70,758,60,61,62,63,64,658,681',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '70%'
                        OR account_code LIKE '758%'
                        OR account_code LIKE '60%'
                        OR account_code LIKE '61%'
                        OR account_code LIKE '62%'
                        OR account_code LIKE '63%'
                        OR account_code LIKE '64%'
                        OR account_code LIKE '658%'
                        OR account_code LIKE '681%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 140,
                           'CR_PRODUITS_FINANCIERS', 'Produits financiers',
                           'Comptes 76', '76',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '76%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 150,
                           'CR_CHARGES_FINANCIERES', 'Charges financières',
                           'Comptes 66', '66',
                           move_line_count, 0, 0, balance, balance
                      FROM balances
                     WHERE account_code LIKE '66%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 160,
                           'CR_RESULTAT_FINANCIER', 'Résultat financier',
                           'Produits financiers moins charges financières', '76,66',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '76%' OR account_code LIKE '66%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 170,
                           'CR_RESULTAT_COURANT_AVANT_IMPOT', 'Résultat courant avant impôts',
                           'Résultat d’exploitation + résultat financier', '70,758,60,61,62,63,64,658,681,76,66',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '70%'
                        OR account_code LIKE '758%'
                        OR account_code LIKE '60%'
                        OR account_code LIKE '61%'
                        OR account_code LIKE '62%'
                        OR account_code LIKE '63%'
                        OR account_code LIKE '64%'
                        OR account_code LIKE '658%'
                        OR account_code LIKE '681%'
                        OR account_code LIKE '76%'
                        OR account_code LIKE '66%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 180,
                           'CR_IMPOTS_BENEFICES', 'Impôts sur les bénéfices',
                           'Comptes 695', '695',
                           move_line_count, 0, 0, balance, balance
                      FROM balances
                     WHERE account_code LIKE '695%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 190,
                           'CR_TOTAL_PRODUITS', 'Total des produits',
                           'Total des comptes de classe 7', '7',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '7%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 195,
                           'CR_TOTAL_CHARGES', 'Total des charges',
                           'Total des comptes de classe 6', '6',
                           move_line_count, 0, 0, balance, balance
                      FROM balances
                     WHERE account_code LIKE '6%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'compte_resultat', 'Compte de résultat', 200,
                           'CR_RESULTAT_NET', 'Résultat net de l’exercice',
                           'Total des produits - total des charges', '6,7',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '6%' OR account_code LIKE '7%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'sig_caf', 'SIG et CAF', 10,
                           'SIG_VALEUR_AJOUTEE', 'Valeur ajoutée',
                           'Chiffre d’affaires et autres produits, moins achats et charges externes', '70,758,607,606,61,62,658',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '70%'
                        OR account_code LIKE '758%'
                        OR account_code LIKE '607%'
                        OR account_code LIKE '606%'
                        OR account_code LIKE '61%'
                        OR account_code LIKE '62%'
                        OR account_code LIKE '658%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'sig_caf', 'SIG et CAF', 20,
                           'SIG_EBE', 'Excédent brut d’exploitation',
                           'Valeur ajoutée moins impôts et charges de personnel', '70,758,607,606,61,62,658,631,633,641,645',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '70%'
                        OR account_code LIKE '758%'
                        OR account_code LIKE '607%'
                        OR account_code LIKE '606%'
                        OR account_code LIKE '61%'
                        OR account_code LIKE '62%'
                        OR account_code LIKE '658%'
                        OR account_code LIKE '631%'
                        OR account_code LIKE '633%'
                        OR account_code LIKE '641%'
                        OR account_code LIKE '645%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'sig_caf', 'SIG et CAF', 30,
                           'SIG_RESULTAT_EXPLOITATION', 'Résultat d’exploitation',
                           'Excédent brut d’exploitation moins dotations aux amortissements', '70,758,60,61,62,63,64,658,681',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '70%'
                        OR account_code LIKE '758%'
                        OR account_code LIKE '60%'
                        OR account_code LIKE '61%'
                        OR account_code LIKE '62%'
                        OR account_code LIKE '63%'
                        OR account_code LIKE '64%'
                        OR account_code LIKE '658%'
                        OR account_code LIKE '681%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'sig_caf', 'SIG et CAF', 40,
                           'SIG_RESULTAT_COURANT_AVANT_IMPOT', 'Résultat courant avant impôts',
                           'Résultat d’exploitation + résultat financier', '70,758,60,61,62,63,64,658,681,76,66',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '70%'
                        OR account_code LIKE '758%'
                        OR account_code LIKE '60%'
                        OR account_code LIKE '61%'
                        OR account_code LIKE '62%'
                        OR account_code LIKE '63%'
                        OR account_code LIKE '64%'
                        OR account_code LIKE '658%'
                        OR account_code LIKE '681%'
                        OR account_code LIKE '76%'
                        OR account_code LIKE '66%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'sig_caf', 'SIG et CAF', 50,
                           'SIG_RESULTAT_NET', 'Résultat net comptable',
                           'Solde des comptes 6 et 7', '6,7',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '6%' OR account_code LIKE '7%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'sig_caf', 'SIG et CAF', 60,
                           'SIG_CAPACITE_AUTOFINANCEMENT', 'Capacité d’autofinancement',
                           'Résultat net comptable + dotations aux amortissements', '6,7,681',
                           move_line_count, 0, 0, -balance, -balance
                      FROM balances
                     WHERE account_code LIKE '6%' OR account_code LIKE '7%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'sig_caf', 'SIG et CAF', 60,
                           'SIG_CAPACITE_AUTOFINANCEMENT', 'Capacité d’autofinancement',
                           'Résultat net comptable + dotations aux amortissements', '6,7,681',
                           move_line_count, 0, 0, balance, balance
                      FROM balances
                     WHERE account_code LIKE '681%'
                )
                SELECT row_number() OVER (
                           ORDER BY company_id, period_key, statement_key, line_sequence, line_code
                       )::integer AS id,
                       company_id,
                       source_company_id,
                       company_currency_id,
                       period_key,
                       statement_key,
                       statement_name,
                       line_sequence,
                       line_code,
                       line_name,
                       source_formula,
                       drilldown_account_prefixes,
                       sum(move_line_count)::integer AS move_line_count,
                       round(sum(gross_component)::numeric, 2) AS gross_amount,
                       round(sum(depreciation_component)::numeric, 2) AS depreciation_amount,
                       round(sum(net_component)::numeric, 2) AS net_amount,
                       round(sum(amount_component)::numeric, 2) AS amount
                  FROM line_sources
                 GROUP BY company_id,
                          source_company_id,
                          company_currency_id,
                          period_key,
                          statement_key,
                          statement_name,
                          line_sequence,
                          line_code,
                          line_name,
                          source_formula,
                          drilldown_account_prefixes
            )
            """,
        )


class RebuildAccountFrenchTaxPackageLine(models.Model):
    _name = "rebuild.account.french.tax.package.line"
    _description = "USL French Tax Package Mapping Line"
    _auto = False
    _order = "company_id, period_key, form_code, line_sequence, field_code"

    company_id = fields.Many2one("res.company", readonly=True)
    source_company_id = fields.Integer(readonly=True)
    company_currency_id = fields.Many2one("res.currency", readonly=True)
    period_key = fields.Char(readonly=True)
    form_code = fields.Char(readonly=True)
    form_name = fields.Char(readonly=True)
    line_sequence = fields.Integer(readonly=True)
    field_code = fields.Char(readonly=True)
    field_label = fields.Char(readonly=True)
    source_kind = fields.Char(readonly=True)
    source_formula = fields.Char(readonly=True)
    source_report_line_code = fields.Char(readonly=True)
    drilldown_account_prefixes = fields.Char(readonly=True)
    move_line_count = fields.Integer(readonly=True)
    quantity = fields.Integer(readonly=True)
    amount = fields.Monetary(currency_field="company_currency_id", readonly=True)
    rounded_amount = fields.Monetary(currency_field="company_currency_id", readonly=True)
    benchmark_amount = fields.Monetary(currency_field="company_currency_id", readonly=True)
    ledger_amount = fields.Monetary(currency_field="company_currency_id", readonly=True)
    difference_amount = fields.Monetary(currency_field="company_currency_id", readonly=True)
    difference_classification = fields.Char(readonly=True)
    value_text = fields.Char(readonly=True)
    review_status = fields.Selection(
        [
            ("ledger_derived", "Ledger Derived"),
            ("accountant_review_required", "Accountant Review Required"),
            ("external_value_required", "External Value Required"),
            ("benchmark_difference_review", "Benchmark Difference Review"),
        ],
        readonly=True,
    )

    def action_open_journal_items(self):
        self.ensure_one()
        if self.source_report_line_code:
            statement_line = self.env["rebuild.account.french.statement.line"].search([
                ("company_id", "=", self.company_id.id),
                ("period_key", "=", self.period_key),
                ("line_code", "=", self.source_report_line_code),
            ], limit=1)
            if statement_line:
                return statement_line.action_open_journal_items()
        prefixes = [
            prefix.strip()
            for prefix in (self.drilldown_account_prefixes or "").split(",")
            if prefix.strip()
        ]
        if not prefixes:
            raise UserError("This tax-package line has no direct journal-item drill-down; review the source formula and any required external value.")
        TrialBalance = self.env["rebuild.account.trial.balance.line"]
        accounts = TrialBalance.search(
            fields.Domain("company_id", "=", self.company_id.id)
            & fields.Domain("period_key", "=", self.period_key)
            & _prefix_domain("account_code", prefixes),
        ).mapped("account_id")
        if not accounts:
            raise UserError("No accounts currently match this tax-package line's drill-down prefixes.")
        return _journal_items_action(
            self,
            [
                *_base_journal_item_domain(self),
                ("account_id", "in", accounts.ids),
            ],
            name=f"{self.form_code} - {self.field_code}",
        )

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                WITH fs AS (
                    SELECT company_id,
                           source_company_id,
                           company_currency_id,
                           period_key,
                           line_code,
                           move_line_count,
                           gross_amount,
                           depreciation_amount,
                           net_amount,
                           amount
                      FROM rebuild_account_french_statement_line
                ),
                vat AS (
                    SELECT company_id,
                           source_company_id,
                           company_currency_id,
                           period_key,
                           account_code,
                           move_line_count,
                           debit,
                           credit,
                           balance
                      FROM rebuild_account_tax_report_line
                     WHERE report_section = 'VAT accounts'
                ),
                vat_ca12_clearing AS (
                    SELECT line.company_id,
                           company.id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           {PERIOD_CASE_SQL} AS period_key,
                           COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text) AS account_code,
                           count(line.id)::integer AS move_line_count,
                           round(sum(line.debit)::numeric, 2) AS debit,
                           round(sum(line.credit)::numeric, 2) AS credit,
                           round(sum(line.balance)::numeric, 2) AS balance,
                           string_agg(DISTINCT move.name, ', ' ORDER BY move.name) AS move_names
                      FROM account_move_line line
                      JOIN account_move move ON move.id = line.move_id
                      JOIN res_company company ON company.id = line.company_id
                      JOIN account_account account ON account.id = line.account_id
                     WHERE move.state = 'posted'
                       AND lower(COALESCE(line.name, '')) = 'ca12'
                       AND COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text) LIKE '445%'
                     GROUP BY line.company_id,
                              company.id,
                              company.currency_id,
                              period_key,
                              account_code
                ),
                assets AS (
                    SELECT asset.company_id,
                           company.id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           '{FIRST_FISCAL_YEAR_PERIOD_KEY}'::text AS period_key,
                           count(DISTINCT asset.id)::integer AS asset_count,
                           round(sum(asset.purchase_value)::numeric, 2) AS original_value,
                           round(sum(COALESCE(depreciation.amount, 0))::numeric, 2) AS accumulated_depreciation,
                           round(sum(asset.purchase_value - COALESCE(depreciation.amount, 0))::numeric, 2) AS net_value
                      FROM account_asset asset
                      JOIN res_company company ON company.id = asset.company_id
                 LEFT JOIN LATERAL (
                           SELECT sum(line.amount) AS amount
                             FROM account_asset_line line
                        LEFT JOIN account_move move ON move.id = line.move_id
                            WHERE line.asset_id = asset.id
                              AND line.type = 'depreciate'
                              AND line.line_date <= DATE '2025-09-30'
                              AND (line.init_entry OR move.state = 'posted')
                           ) depreciation ON TRUE
                     GROUP BY asset.company_id, company.id, company.currency_id
                ),
                schedule AS (
                    SELECT asset.company_id,
                           company.id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           '{FIRST_FISCAL_YEAR_PERIOD_KEY}'::text AS period_key,
                           count(schedule.id)::integer AS schedule_line_count,
                           round(sum(schedule.amount)::numeric, 2) AS depreciation_schedule_total
                      FROM account_asset_line schedule
                      JOIN account_asset asset ON asset.id = schedule.asset_id
                      JOIN res_company company ON company.id = asset.company_id
                     WHERE schedule.type = 'depreciate'
                     GROUP BY asset.company_id, company.id, company.currency_id
                ),
                external_values AS (
                    SELECT value.company_id,
                           value.period_key,
                           value.form_code,
                           value.field_code,
                           round(sum(value.amount)::numeric, 2) AS amount
                      FROM rebuild_account_external_report_value value
                     WHERE value.active IS TRUE
                       AND value.value_kind IN (
                           'benchmark_acceptance_anchor',
                           'source_external_value',
                           'accountant_supplied',
                           'manual_adjustment',
                           'carryover'
                       )
                       AND value.review_status != 'superseded'
                     GROUP BY value.company_id,
                              value.period_key,
                              value.form_code,
                              value.field_code
                ),
                lines AS (
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2065-SD' AS form_code,
                           'Impôt sur les sociétés' AS form_name,
                           10 AS line_sequence,
                           '2065_RESULTAT_FISCAL_AVANT_DEFICITS_REVIEW' AS field_code,
                           'Bénéfice imposable avant déficits - montant de revue' AS field_label,
                           'annual_statement' AS source_kind,
                           'Compte de résultat: résultat courant avant impôts; réintégrations/déductions fiscales non automatisées' AS source_formula,
                           line_code AS source_report_line_code,
                           '70,758,60,61,62,63,64,658,681,76,66' AS drilldown_account_prefixes,
                           move_line_count,
                           0 AS quantity,
                           amount,
                           round(amount, 0) AS rounded_amount,
                           NULL::text AS value_text,
                           'accountant_review_required' AS review_status
                      FROM fs
                     WHERE line_code = 'CR_RESULTAT_COURANT_AVANT_IMPOT'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2065-SD', 'Impôt sur les sociétés', 20,
                           '2065_BASE_TAUX_REDUIT_15_REVIEW',
                           'Base potentielle au taux réduit de 15% - à valider',
                           'annual_statement',
                           'Montant provisoire égal au résultat fiscal de revue; plafond, capital libéré et détention PME à confirmer',
                           line_code,
                           '70,758,60,61,62,63,64,658,681,76,66',
                           move_line_count,
                           0,
                           amount,
                           round(amount, 0),
                           NULL::text,
                           'accountant_review_required'
                      FROM fs
                     WHERE line_code = 'CR_RESULTAT_COURANT_AVANT_IMPOT'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2065-SD', 'Impôt sur les sociétés', 30,
                           '2065_BASE_TAUX_NORMAL_REVIEW',
                           'Base potentielle au taux normal - à valider',
                           'manual_required',
                           'À calculer après validation de la base taux réduit, des réintégrations, déductions et déficits',
                           NULL::text,
                           NULL::text,
                           0,
                           0,
                           0::numeric,
                           0::numeric,
                           'External tax computation required',
                           'external_value_required'
                      FROM fs
                     WHERE line_code = 'CR_RESULTAT_COURANT_AVANT_IMPOT'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2065-SD', 'Impôt sur les sociétés', 40,
                           '2065_CHARGE_IS_COMPTABILISEE',
                           'Impôt sur les bénéfices comptabilisé',
                           'annual_statement',
                           'Compte 695 dans le compte de résultat',
                           line_code,
                           '695',
                           move_line_count,
                           0,
                           amount,
                           round(amount, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM fs
                     WHERE line_code = 'CR_IMPOTS_BENEFICES'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-A-SD', 'Bilan simplifié', 10,
                           '2033_A_IMMOBILISATIONS_CORP_BRUT',
                           'Immobilisations corporelles - brut',
                           'annual_statement',
                           'Bilan Actif: comptes 21',
                           line_code,
                           '21',
                           move_line_count,
                           0,
                           gross_amount,
                           round(gross_amount, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM fs
                     WHERE line_code = 'ACTIF_IMMO_CORP'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-A-SD', 'Bilan simplifié', 20,
                           '2033_A_IMMOBILISATIONS_CORP_AMORT_PROV',
                           'Immobilisations corporelles - amortissements/provisions',
                           'annual_statement',
                           'Bilan Actif: comptes 28',
                           line_code,
                           '28',
                           move_line_count,
                           0,
                           depreciation_amount,
                           round(depreciation_amount, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM fs
                     WHERE line_code = 'ACTIF_IMMO_CORP'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-A-SD', 'Bilan simplifié', 30,
                           '2033_A_IMMOBILISATIONS_CORP_NET',
                           'Immobilisations corporelles - net',
                           'annual_statement',
                           'Bilan Actif: comptes 21 diminués des comptes 28',
                           line_code,
                           '21,28',
                           move_line_count,
                           0,
                           amount,
                           round(amount, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM fs
                     WHERE line_code = 'ACTIF_IMMO_CORP'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-A-SD', 'Bilan simplifié', 40,
                           '2033_A_TOTAL_ACTIF_BRUT',
                           'Total actif - brut',
                           'annual_statement',
                           'Bilan Actif: total brut',
                           line_code,
                           '21,28,4,5',
                           move_line_count,
                           0,
                           gross_amount,
                           round(gross_amount, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM fs
                     WHERE line_code = 'ACTIF_TOTAL'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-A-SD', 'Bilan simplifié', 50,
                           '2033_A_TOTAL_ACTIF_AMORT_PROV',
                           'Total actif - amortissements/provisions',
                           'annual_statement',
                           'Bilan Actif: total amortissements/provisions',
                           line_code,
                           '28',
                           move_line_count,
                           0,
                           depreciation_amount,
                           round(depreciation_amount, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM fs
                     WHERE line_code = 'ACTIF_TOTAL'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-A-SD', 'Bilan simplifié', 60,
                           '2033_A_TOTAL_ACTIF_NET',
                           'Total actif - net',
                           'annual_statement',
                           'Bilan Actif: total net',
                           line_code,
                           '21,28,4,5',
                           move_line_count,
                           0,
                           amount,
                           round(amount, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM fs
                     WHERE line_code = 'ACTIF_TOTAL'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-A-SD', 'Bilan simplifié', 70,
                           '2033_A_CAPITAL_SOCIAL',
                           'Capital social',
                           'annual_statement',
                           'Bilan Passif: compte 101',
                           line_code,
                           '101',
                           move_line_count,
                           0,
                           amount,
                           round(amount, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM fs
                     WHERE line_code = 'PASSIF_CAPITAL'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-A-SD', 'Bilan simplifié', 80,
                           '2033_A_RESULTAT_EXERCICE',
                           'Résultat de l’exercice',
                           'annual_statement',
                           'Bilan Passif: résultat de l’exercice',
                           line_code,
                           '6,7',
                           move_line_count,
                           0,
                           amount,
                           round(amount, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM fs
                     WHERE line_code = 'PASSIF_RESULTAT'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-A-SD', 'Bilan simplifié', 90,
                           '2033_A_TOTAL_PASSIF',
                           'Total passif',
                           'annual_statement',
                           'Bilan Passif: total passif',
                           line_code,
                           '101,6,7,455,42,43,44',
                           move_line_count,
                           0,
                           amount,
                           round(amount, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM fs
                     WHERE line_code = 'PASSIF_TOTAL'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-B-SD', 'Compte de résultat simplifié', 10,
                           '2033_B_CHIFFRE_AFFAIRES_NET',
                           'Chiffre d’affaires net',
                           'annual_statement',
                           'Compte de résultat: comptes 70',
                           line_code,
                           '70',
                           move_line_count,
                           0,
                           amount,
                           round(amount, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM fs
                     WHERE line_code = 'CR_CHIFFRE_AFFAIRES'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-B-SD', 'Compte de résultat simplifié', 20,
                           '2033_B_RESULTAT_COURANT_AVANT_IMPOT',
                           'Résultat courant avant impôt',
                           'annual_statement',
                           'Compte de résultat: résultat courant avant impôt',
                           line_code,
                           '70,758,60,61,62,63,64,658,681,76,66',
                           move_line_count,
                           0,
                           amount,
                           round(amount, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM fs
                     WHERE line_code = 'CR_RESULTAT_COURANT_AVANT_IMPOT'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-B-SD', 'Compte de résultat simplifié', 30,
                           '2033_B_IMPOTS_BENEFICES',
                           'Impôts sur les bénéfices',
                           'annual_statement',
                           'Compte de résultat: compte 695',
                           line_code,
                           '695',
                           move_line_count,
                           0,
                           amount,
                           round(amount, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM fs
                     WHERE line_code = 'CR_IMPOTS_BENEFICES'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-B-SD', 'Compte de résultat simplifié', 40,
                           '2033_B_RESULTAT_NET_COMPTABLE',
                           'Résultat net comptable',
                           'annual_statement',
                           'Compte de résultat: solde des comptes 6 et 7',
                           line_code,
                           '6,7',
                           move_line_count,
                           0,
                           amount,
                           round(amount, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM fs
                     WHERE line_code = 'CR_RESULTAT_NET'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-C-SD', 'Immobilisations et amortissements', 10,
                           '2033_C_NOMBRE_IMMOBILISATIONS_SOURCE',
                           'Nombre d’immobilisations source représentées',
                           'fixed_asset_register',
                           'Registre des immobilisations importé',
                           NULL::text,
                           NULL::text,
                           0,
                           asset_count,
                           0::numeric,
                           0::numeric,
                           asset_count::text,
                           'ledger_derived'
                      FROM assets
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-C-SD', 'Immobilisations et amortissements', 20,
                           '2033_C_IMMOBILISATIONS_CORP_BRUT',
                           'Immobilisations corporelles - valeur brute',
                           'fixed_asset_register',
                           'Somme des valeurs d’acquisition importées',
                           NULL::text,
                           '21',
                           0,
                           0,
                           original_value,
                           round(original_value, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM assets
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-C-SD', 'Immobilisations et amortissements', 30,
                           '2033_C_AMORTISSEMENTS_TOTAL',
                           'Amortissements cumulés',
                           'fixed_asset_register',
                           'Somme des amortissements importés à la clôture',
                           NULL::text,
                           '28',
                           0,
                           0,
                           accumulated_depreciation,
                           round(accumulated_depreciation, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM assets
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-C-SD', 'Immobilisations et amortissements', 40,
                           '2033_C_NET_COMPTABLE',
                           'Valeur nette comptable importée à la clôture',
                           'fixed_asset_register',
                           'Valeur brute moins amortissements importés',
                           NULL::text,
                           '21,28',
                           0,
                           0,
                           net_value,
                           round(net_value, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM assets
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-C-SD', 'Immobilisations et amortissements', 50,
                           '2033_C_DOTATIONS_EXERCICE',
                           'Dotations aux amortissements de l’exercice',
                           'annual_statement',
                           'Compte de résultat: compte 681',
                           line_code,
                           '681',
                           move_line_count,
                           0,
                           amount,
                           round(amount, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM fs
                     WHERE line_code = 'CR_DOTATIONS_AMORTISSEMENTS'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-C-SD', 'Immobilisations et amortissements', 60,
                           '2033_C_LIGNES_PLAN_AMORTISSEMENT_SOURCE',
                           'Lignes du plan d’amortissement source conservées',
                           'depreciation_schedule',
                           'Nombre de lignes source de calendrier d’amortissement importées comme preuve',
                           NULL::text,
                           NULL::text,
                           0,
                           schedule_line_count,
                           0::numeric,
                           0::numeric,
                           schedule_line_count::text,
                           'ledger_derived'
                      FROM schedule
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-D-SD', 'TVA et taxes', 10,
                           '2033_D_TVA_COLLECTEE_445700',
                           'TVA collectée / due sur comptes 4457',
                           'vat_accounts',
                           'Crédits des comptes 4457 dans le grand livre importé',
                           NULL::text,
                           '4457',
                           move_line_count,
                           0,
                           credit,
                           round(credit, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM vat
                     WHERE account_code LIKE '4457%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-D-SD', 'TVA et taxes', 20,
                           '2033_D_TVA_DEDUCTIBLE_IMMOBILISATIONS_445620',
                           'TVA déductible sur immobilisations',
                           'vat_accounts',
                           'Débits des comptes 44562 dans le grand livre importé',
                           NULL::text,
                           '44562',
                           move_line_count,
                           0,
                           debit,
                           round(debit, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM vat
                     WHERE account_code LIKE '44562%'
                    UNION ALL
                    SELECT vat.company_id, vat.source_company_id, vat.company_currency_id, vat.period_key,
                           '2033-D-SD', 'TVA et taxes', 30,
                           '2033_D_TVA_DEDUCTIBLE_BIENS_SERVICES_445660',
                           'TVA déductible sur autres biens et services',
                           'vat_accounts',
                           CASE
                               WHEN ca12.move_line_count IS NOT NULL
                               THEN 'Crédit du compte 445660 sur l’écriture CA12 source; le total débiteur du grand livre reste contrôlable dans le rapport TVA'
                               ELSE 'Débits du compte 445660 dans le grand livre importé; écriture CA12 source non trouvée'
                           END,
                           NULL::text,
                           '445660',
                           COALESCE(ca12.move_line_count, vat.move_line_count),
                           0,
                           COALESCE(NULLIF(ca12.credit, 0), vat.debit),
                           round(COALESCE(NULLIF(ca12.credit, 0), vat.debit), 0),
                           CASE
                               WHEN ca12.move_line_count IS NOT NULL
                               THEN concat('Débit total 445660: ', vat.debit::text, '; crédit CA12: ', ca12.credit::text, '; écriture(s): ', COALESCE(ca12.move_names, ''))
                               ELSE NULL::text
                           END,
                           CASE WHEN ca12.move_line_count IS NOT NULL THEN 'ledger_derived' ELSE 'benchmark_difference_review' END
                      FROM vat
                      LEFT JOIN vat_ca12_clearing ca12
                        ON ca12.company_id = vat.company_id
                       AND ca12.period_key = vat.period_key
                       AND ca12.account_code = vat.account_code
                     WHERE vat.account_code = '445660'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '2033-D-SD', 'TVA et taxes', 40,
                           '2033_D_CREDIT_TVA_A_REPORTER_445670',
                           'Crédit de TVA à reporter',
                           'vat_accounts',
                           'Solde débiteur du compte 445670',
                           NULL::text,
                           '445670',
                           move_line_count,
                           0,
                           balance,
                           round(balance, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM vat
                     WHERE account_code = '445670'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '3517-S-SD', 'TVA CA12/CA12E', 10,
                           '3517S_TVA_COLLECTEE_445700',
                           'CA12 - TVA collectée issue du grand livre',
                           'vat_accounts',
                           'Crédits des comptes 4457',
                           NULL::text,
                           '4457',
                           move_line_count,
                           0,
                           credit,
                           round(credit, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM vat
                     WHERE account_code LIKE '4457%'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '3517-S-SD', 'TVA CA12/CA12E', 20,
                           '3517S_TVA_DEDUCTIBLE_IMMOBILISATIONS_445620',
                           'CA12 - TVA déductible sur immobilisations',
                           'vat_accounts',
                           'Débits des comptes 44562',
                           NULL::text,
                           '44562',
                           move_line_count,
                           0,
                           debit,
                           round(debit, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM vat
                     WHERE account_code LIKE '44562%'
                    UNION ALL
                    SELECT vat.company_id, vat.source_company_id, vat.company_currency_id, vat.period_key,
                           '3517-S-SD', 'TVA CA12/CA12E', 30,
                           '3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660',
                           'CA12 - TVA déductible sur biens et services issue du grand livre',
                           'vat_accounts',
                           CASE
                               WHEN ca12.move_line_count IS NOT NULL
                               THEN 'Crédit du compte 445660 sur l’écriture CA12 source; le total débiteur du grand livre reste contrôlable dans le rapport TVA'
                               ELSE 'Débits du compte 445660; écriture CA12 source non trouvée'
                           END,
                           NULL::text,
                           '445660',
                           COALESCE(ca12.move_line_count, vat.move_line_count),
                           0,
                           COALESCE(NULLIF(ca12.credit, 0), vat.debit),
                           round(COALESCE(NULLIF(ca12.credit, 0), vat.debit), 0),
                           CASE
                               WHEN ca12.move_line_count IS NOT NULL
                               THEN concat('Débit total 445660: ', vat.debit::text, '; crédit CA12: ', ca12.credit::text, '; écriture(s): ', COALESCE(ca12.move_names, ''))
                               ELSE NULL::text
                           END,
                           CASE WHEN ca12.move_line_count IS NOT NULL THEN 'ledger_derived' ELSE 'benchmark_difference_review' END
                      FROM vat
                      LEFT JOIN vat_ca12_clearing ca12
                        ON ca12.company_id = vat.company_id
                       AND ca12.period_key = vat.period_key
                       AND ca12.account_code = vat.account_code
                     WHERE vat.account_code = '445660'
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           '3517-S-SD', 'TVA CA12/CA12E', 40,
                           '3517S_CREDIT_TVA_A_REPORTER_445670',
                           'CA12 - Crédit de TVA à reporter',
                           'vat_accounts',
                           'Solde du compte 445670',
                           NULL::text,
                           '445670',
                           move_line_count,
                           0,
                           balance,
                           round(balance, 0),
                           NULL::text,
                           'ledger_derived'
                      FROM vat
                     WHERE account_code = '445670'
                )
                SELECT row_number() OVER (
                           ORDER BY lines.company_id, lines.period_key, lines.form_code, lines.line_sequence, lines.field_code
                       )::integer AS id,
                       lines.company_id,
                       lines.source_company_id,
                       lines.company_currency_id,
                       lines.period_key,
                       lines.form_code,
                       lines.form_name,
                       lines.line_sequence,
                       lines.field_code,
                       lines.field_label,
                       lines.source_kind,
                       lines.source_formula,
                       lines.source_report_line_code,
                       lines.drilldown_account_prefixes,
                       lines.move_line_count,
                       lines.quantity,
                       round(lines.amount::numeric, 2) AS amount,
                       round(lines.rounded_amount::numeric, 2) AS rounded_amount,
                       external_value.amount AS benchmark_amount,
                       CASE WHEN external_value.amount IS NOT NULL THEN round(lines.amount::numeric, 2) ELSE NULL::numeric END AS ledger_amount,
                       CASE WHEN external_value.amount IS NOT NULL THEN round(lines.amount::numeric - external_value.amount, 2) ELSE NULL::numeric END AS difference_amount,
                       CASE
                           WHEN external_value.amount IS NOT NULL
                            AND round(lines.amount::numeric - external_value.amount, 2) != 0
                           THEN 'EXTERNAL_VALUE_DIFFERENCE'
                           ELSE NULL::text
                       END AS difference_classification,
                       lines.value_text,
                       lines.review_status
                  FROM lines
                  LEFT JOIN external_values external_value
                    ON external_value.company_id = lines.company_id
                   AND external_value.period_key = lines.period_key
                   AND external_value.form_code = lines.form_code
                   AND external_value.field_code = lines.field_code
            )
            """,
        )
