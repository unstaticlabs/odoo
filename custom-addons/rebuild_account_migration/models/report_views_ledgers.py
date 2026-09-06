"""Ledger-family presentation lines: trial balance, general ledger, journal, partner ledger, open items and aged balances."""

from odoo import fields, models, tools

from .report_views import (
    PERIOD_CASE_SQL,
    _base_journal_item_domain,
    _journal_items_action,
    _single_journal_item_action,
)


class RebuildAccountTrialBalanceLine(models.Model):
    _name = "rebuild.account.trial.balance.line"
    _description = "USL Trial Balance Line"
    _auto = False
    _order = "company_id, period_key, account_code"

    company_id = fields.Many2one("res.company", readonly=True)
    source_company_id = fields.Integer(readonly=True)
    company_currency_id = fields.Many2one("res.currency", readonly=True)
    period_key = fields.Char(readonly=True)
    date_from = fields.Date(readonly=True)
    date_to = fields.Date(readonly=True)
    account_id = fields.Many2one("account.account", readonly=True)
    account_code = fields.Char(readonly=True)
    account_name = fields.Char(readonly=True)
    account_type = fields.Char(readonly=True)
    move_count = fields.Integer(readonly=True)
    move_line_count = fields.Integer(readonly=True)
    debit = fields.Monetary(currency_field="company_currency_id", readonly=True)
    credit = fields.Monetary(currency_field="company_currency_id", readonly=True)
    closing_balance = fields.Monetary(currency_field="company_currency_id", readonly=True)

    def action_open_journal_items(self):
        self.ensure_one()
        return _journal_items_action(
            self,
            [
                *_base_journal_item_domain(self),
                ("account_id", "=", self.account_id.id),
            ],
            name=f"Journal Items - {self.account_code}",
        )

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT min(line.id) AS id,
                       line.company_id,
                       company.id AS source_company_id,
                       company.currency_id AS company_currency_id,
                       {PERIOD_CASE_SQL} AS period_key,
                       min(move.date) AS date_from,
                       max(move.date) AS date_to,
                       account.id AS account_id,
                       COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text) AS account_code,
                       COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text) AS account_name,
                       account.account_type AS account_type,
                       count(DISTINCT move.id) AS move_count,
                       count(line.id) AS move_line_count,
                       round(sum(line.debit)::numeric, 2) AS debit,
                       round(sum(line.credit)::numeric, 2) AS credit,
                       round(sum(line.balance)::numeric, 2) AS closing_balance
                  FROM account_move_line line
                  JOIN account_move move ON move.id = line.move_id
                  JOIN res_company company ON company.id = line.company_id
                  JOIN account_account account ON account.id = line.account_id
                 WHERE move.state = 'posted'
                 GROUP BY line.company_id,
                          company.id,
                          company.currency_id,
                          {PERIOD_CASE_SQL},
                          account.id,
                          COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text),
                          COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text),
                          account.account_type
            )
            """,
        )


class RebuildAccountGeneralLedgerLine(models.Model):
    _name = "rebuild.account.general.ledger.line"
    _description = "USL General Ledger Line"
    _auto = False
    _order = "company_id, account_code, date, move_name, source_line_id"

    company_id = fields.Many2one("res.company", readonly=True)
    source_company_id = fields.Integer(readonly=True)
    company_currency_id = fields.Many2one("res.currency", readonly=True)
    period_key = fields.Char(readonly=True)
    date = fields.Date(readonly=True)
    journal_id = fields.Many2one("account.journal", readonly=True)
    journal_code = fields.Char(readonly=True)
    move_id = fields.Many2one("account.move", readonly=True)
    move_line_id = fields.Many2one("account.move.line", readonly=True)
    source_move_id = fields.Integer(readonly=True)
    source_line_id = fields.Integer(readonly=True)
    source_move_type = fields.Char(readonly=True)
    move_name = fields.Char(readonly=True)
    partner_id = fields.Many2one("res.partner", readonly=True)
    account_id = fields.Many2one("account.account", readonly=True)
    account_code = fields.Char(readonly=True)
    account_name = fields.Char(readonly=True)
    label = fields.Char(readonly=True)
    debit = fields.Monetary(currency_field="company_currency_id", readonly=True)
    credit = fields.Monetary(currency_field="company_currency_id", readonly=True)
    balance = fields.Monetary(currency_field="company_currency_id", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    amount_currency = fields.Monetary(currency_field="currency_id", readonly=True)
    full_reconcile_id = fields.Many2one("account.full.reconcile", readonly=True)

    def action_open_journal_items(self):
        return _single_journal_item_action(self)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT line.id AS id,
                       line.company_id,
                       company.id AS source_company_id,
                       company.currency_id AS company_currency_id,
                       {PERIOD_CASE_SQL} AS period_key,
                       move.date,
                       journal.id AS journal_id,
                       journal.code AS journal_code,
                       move.id AS move_id,
                       line.id AS move_line_id,
                       move.id AS source_move_id,
                       line.id AS source_line_id,
                       move.move_type AS source_move_type,
                       move.name AS move_name,
                       line.partner_id,
                       account.id AS account_id,
                       COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text) AS account_code,
                       COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text) AS account_name,
                       line.name AS label,
                       line.debit,
                       line.credit,
                       line.balance,
                       line.currency_id,
                       line.amount_currency,
                       line.full_reconcile_id
                  FROM account_move_line line
                  JOIN account_move move ON move.id = line.move_id
                  JOIN res_company company ON company.id = line.company_id
                  JOIN account_account account ON account.id = line.account_id
                  JOIN account_journal journal ON journal.id = move.journal_id
                 WHERE move.state = 'posted'
            )
            """,
        )


class RebuildAccountJournalReportLine(models.Model):
    _name = "rebuild.account.journal.report.line"
    _description = "USL Journal Report Line"
    _auto = False
    _order = "company_id, period_key, journal_code"

    company_id = fields.Many2one("res.company", readonly=True)
    source_company_id = fields.Integer(readonly=True)
    company_currency_id = fields.Many2one("res.currency", readonly=True)
    period_key = fields.Char(readonly=True)
    date_from = fields.Date(readonly=True)
    date_to = fields.Date(readonly=True)
    journal_id = fields.Many2one("account.journal", readonly=True)
    journal_code = fields.Char(readonly=True)
    journal_name = fields.Char(readonly=True)
    journal_type = fields.Char(readonly=True)
    move_count = fields.Integer(readonly=True)
    move_line_count = fields.Integer(readonly=True)
    debit = fields.Monetary(currency_field="company_currency_id", readonly=True)
    credit = fields.Monetary(currency_field="company_currency_id", readonly=True)
    balance = fields.Monetary(currency_field="company_currency_id", readonly=True)

    def action_open_journal_items(self):
        self.ensure_one()
        return _journal_items_action(
            self,
            [
                *_base_journal_item_domain(self),
                ("journal_id", "=", self.journal_id.id),
            ],
            name=f"Journal Items - {self.journal_code}",
        )

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT min(line.id) AS id,
                       line.company_id,
                       company.id AS source_company_id,
                       company.currency_id AS company_currency_id,
                       {PERIOD_CASE_SQL} AS period_key,
                       min(move.date) AS date_from,
                       max(move.date) AS date_to,
                       journal.id AS journal_id,
                       journal.code AS journal_code,
                       COALESCE(journal.name->>'fr_FR', journal.name->>'en_US', journal.name::text) AS journal_name,
                       journal.type AS journal_type,
                       count(DISTINCT move.id) AS move_count,
                       count(line.id) AS move_line_count,
                       round(sum(line.debit)::numeric, 2) AS debit,
                       round(sum(line.credit)::numeric, 2) AS credit,
                       round(sum(line.balance)::numeric, 2) AS balance
                  FROM account_move_line line
                  JOIN account_move move ON move.id = line.move_id
                  JOIN res_company company ON company.id = line.company_id
                  JOIN account_journal journal ON journal.id = move.journal_id
                 WHERE move.state = 'posted'
                 GROUP BY line.company_id,
                          company.id,
                          company.currency_id,
                          {PERIOD_CASE_SQL},
                          journal.id,
                          journal.code,
                          COALESCE(journal.name->>'fr_FR', journal.name->>'en_US', journal.name::text),
                          journal.type
            )
            """,
        )


class RebuildAccountPartnerLedgerLine(models.Model):
    _name = "rebuild.account.partner.ledger.line"
    _description = "USL Partner Ledger Line"
    _auto = False
    _order = "company_id, partner_id, date, move_name, source_line_id"

    company_id = fields.Many2one("res.company", readonly=True)
    source_company_id = fields.Integer(readonly=True)
    company_currency_id = fields.Many2one("res.currency", readonly=True)
    period_key = fields.Char(readonly=True)
    date = fields.Date(readonly=True)
    date_maturity = fields.Date(readonly=True)
    journal_id = fields.Many2one("account.journal", readonly=True)
    journal_code = fields.Char(readonly=True)
    move_id = fields.Many2one("account.move", readonly=True)
    move_line_id = fields.Many2one("account.move.line", readonly=True)
    source_move_id = fields.Integer(readonly=True)
    source_line_id = fields.Integer(readonly=True)
    move_name = fields.Char(readonly=True)
    partner_id = fields.Many2one("res.partner", readonly=True)
    account_id = fields.Many2one("account.account", readonly=True)
    account_code = fields.Char(readonly=True)
    account_name = fields.Char(readonly=True)
    account_type = fields.Char(readonly=True)
    label = fields.Char(readonly=True)
    debit = fields.Monetary(currency_field="company_currency_id", readonly=True)
    credit = fields.Monetary(currency_field="company_currency_id", readonly=True)
    balance = fields.Monetary(currency_field="company_currency_id", readonly=True)
    amount_residual = fields.Monetary(currency_field="company_currency_id", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    amount_currency = fields.Monetary(currency_field="currency_id", readonly=True)
    amount_residual_currency = fields.Monetary(currency_field="currency_id", readonly=True)
    reconciled = fields.Boolean(readonly=True)
    matching_number = fields.Char(readonly=True)
    full_reconcile_id = fields.Many2one("account.full.reconcile", readonly=True)

    def action_open_journal_items(self):
        return _single_journal_item_action(self)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT line.id AS id,
                       line.company_id,
                       company.id AS source_company_id,
                       company.currency_id AS company_currency_id,
                       {PERIOD_CASE_SQL} AS period_key,
                       move.date,
                       line.date_maturity,
                       journal.id AS journal_id,
                       journal.code AS journal_code,
                       move.id AS move_id,
                       line.id AS move_line_id,
                       move.id AS source_move_id,
                       line.id AS source_line_id,
                       move.name AS move_name,
                       line.partner_id,
                       account.id AS account_id,
                       COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text) AS account_code,
                       COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text) AS account_name,
                       account.account_type,
                       line.name AS label,
                       line.debit,
                       line.credit,
                       line.balance,
                       line.amount_residual,
                       line.currency_id,
                       line.amount_currency,
                       line.amount_residual_currency,
                       line.reconciled,
                       line.matching_number,
                       line.full_reconcile_id
                  FROM account_move_line line
                  JOIN account_move move ON move.id = line.move_id
                  JOIN res_company company ON company.id = line.company_id
                  JOIN account_account account ON account.id = line.account_id
                  JOIN account_journal journal ON journal.id = move.journal_id
                 WHERE move.state = 'posted'
                   AND line.partner_id IS NOT NULL
            )
            """,
        )


class RebuildAccountOpenItemLine(models.Model):
    _name = "rebuild.account.open.item.line"
    _description = "USL Open Item Line"
    _auto = False
    _order = "company_id, date_maturity, partner_id, move_name, source_line_id"

    company_id = fields.Many2one("res.company", readonly=True)
    source_company_id = fields.Integer(readonly=True)
    company_currency_id = fields.Many2one("res.currency", readonly=True)
    period_key = fields.Char(readonly=True)
    date = fields.Date(readonly=True)
    date_maturity = fields.Date(readonly=True)
    journal_id = fields.Many2one("account.journal", readonly=True)
    journal_code = fields.Char(readonly=True)
    move_id = fields.Many2one("account.move", readonly=True)
    move_line_id = fields.Many2one("account.move.line", readonly=True)
    source_move_id = fields.Integer(readonly=True)
    source_line_id = fields.Integer(readonly=True)
    move_name = fields.Char(readonly=True)
    partner_id = fields.Many2one("res.partner", readonly=True)
    account_id = fields.Many2one("account.account", readonly=True)
    account_code = fields.Char(readonly=True)
    account_name = fields.Char(readonly=True)
    account_type = fields.Char(readonly=True)
    label = fields.Char(readonly=True)
    amount_residual = fields.Monetary(currency_field="company_currency_id", readonly=True)
    presented_residual = fields.Monetary(currency_field="company_currency_id", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    amount_residual_currency = fields.Monetary(currency_field="currency_id", readonly=True)
    reconciled = fields.Boolean(readonly=True)
    matching_number = fields.Char(readonly=True)

    def action_open_journal_items(self):
        return _single_journal_item_action(self)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT line.id AS id,
                       line.company_id,
                       company.id AS source_company_id,
                       company.currency_id AS company_currency_id,
                       {PERIOD_CASE_SQL} AS period_key,
                       move.date,
                       line.date_maturity,
                       journal.id AS journal_id,
                       journal.code AS journal_code,
                       move.id AS move_id,
                       line.id AS move_line_id,
                       move.id AS source_move_id,
                       line.id AS source_line_id,
                       move.name AS move_name,
                       line.partner_id,
                       account.id AS account_id,
                       COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text) AS account_code,
                       COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text) AS account_name,
                       account.account_type,
                       line.name AS label,
                       line.amount_residual,
                       CASE
                           WHEN account.account_type = 'liability_payable' THEN -line.amount_residual
                           ELSE line.amount_residual
                       END AS presented_residual,
                       line.currency_id,
                       line.amount_residual_currency,
                       line.reconciled,
                       line.matching_number
                  FROM account_move_line line
                  JOIN account_move move ON move.id = line.move_id
                  JOIN res_company company ON company.id = line.company_id
                  JOIN account_account account ON account.id = line.account_id
                  JOIN account_journal journal ON journal.id = move.journal_id
                 WHERE move.state = 'posted'
                   AND account.account_type IN ('asset_receivable', 'liability_payable')
                   AND (line.reconciled IS NOT TRUE OR abs(line.amount_residual) > 0.004)
            )
            """,
        )


class RebuildAccountAgedPartnerBalanceLine(models.Model):
    _name = "rebuild.account.aged.partner.balance.line"
    _description = "USL Aged Partner Balance Line"
    _auto = False
    _order = "company_id, account_type, partner_id"

    company_id = fields.Many2one("res.company", readonly=True)
    source_company_id = fields.Integer(readonly=True)
    company_currency_id = fields.Many2one("res.currency", readonly=True)
    period_key = fields.Char(readonly=True)
    as_of_date = fields.Date(readonly=True)
    partner_id = fields.Many2one("res.partner", readonly=True)
    account_type = fields.Char(readonly=True)
    open_item_count = fields.Integer(readonly=True)
    not_due = fields.Monetary(currency_field="company_currency_id", readonly=True)
    bucket_1_30 = fields.Monetary(currency_field="company_currency_id", readonly=True)
    bucket_31_60 = fields.Monetary(currency_field="company_currency_id", readonly=True)
    bucket_61_90 = fields.Monetary(currency_field="company_currency_id", readonly=True)
    bucket_over_90 = fields.Monetary(currency_field="company_currency_id", readonly=True)
    total = fields.Monetary(currency_field="company_currency_id", readonly=True)

    def action_open_journal_items(self):
        self.ensure_one()
        domain = [
            *_base_journal_item_domain(self),
            ("account_id.account_type", "=", self.account_type),
            "|",
            ("reconciled", "=", False),
            ("amount_residual", "!=", 0),
        ]
        if self.partner_id:
            domain.append(("partner_id", "=", self.partner_id.id))
        else:
            domain.append(("partner_id", "=", False))
        return _journal_items_action(
            self,
            domain,
            name="Aged Balance Journal Items",
        )

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                WITH open_lines AS (
                    SELECT line.id,
                           line.company_id,
                           company.id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           {PERIOD_CASE_SQL} AS period_key,
                           CASE
                               WHEN company.id = 1
                                AND move.date BETWEEN DATE '2024-01-10' AND DATE '2025-09-30'
                               THEN DATE '2025-09-30'
                               ELSE CURRENT_DATE
                           END AS as_of_date,
                           line.partner_id,
                           account.account_type,
                           CASE
                               WHEN account.account_type = 'liability_payable' THEN -line.amount_residual
                               ELSE line.amount_residual
                           END AS presented_residual,
                           CASE
                               WHEN company.id = 1
                                AND move.date BETWEEN DATE '2024-01-10' AND DATE '2025-09-30'
                               THEN DATE '2025-09-30'
                               ELSE CURRENT_DATE
                           END - COALESCE(line.date_maturity, move.date) AS age_days
                      FROM account_move_line line
                      JOIN account_move move ON move.id = line.move_id
                      JOIN res_company company ON company.id = line.company_id
                      JOIN account_account account ON account.id = line.account_id
                 WHERE move.state = 'posted'
                       AND account.account_type IN ('asset_receivable', 'liability_payable')
                       AND (line.reconciled IS NOT TRUE OR abs(line.amount_residual) > 0.004)
                )
                SELECT min(id) AS id,
                       company_id,
                       source_company_id,
                       company_currency_id,
                       period_key,
                       as_of_date,
                       partner_id,
                       account_type,
                       count(id) AS open_item_count,
                       round(sum(CASE WHEN age_days <= 0 THEN presented_residual ELSE 0 END)::numeric, 2) AS not_due,
                       round(sum(CASE WHEN age_days BETWEEN 1 AND 30 THEN presented_residual ELSE 0 END)::numeric, 2) AS bucket_1_30,
                       round(sum(CASE WHEN age_days BETWEEN 31 AND 60 THEN presented_residual ELSE 0 END)::numeric, 2) AS bucket_31_60,
                       round(sum(CASE WHEN age_days BETWEEN 61 AND 90 THEN presented_residual ELSE 0 END)::numeric, 2) AS bucket_61_90,
                       round(sum(CASE WHEN age_days > 90 THEN presented_residual ELSE 0 END)::numeric, 2) AS bucket_over_90,
                       round(sum(presented_residual)::numeric, 2) AS total
                  FROM open_lines
                 GROUP BY company_id,
                          source_company_id,
                          company_currency_id,
                          period_key,
                          as_of_date,
                          partner_id,
                          account_type
            )
            """,
        )
