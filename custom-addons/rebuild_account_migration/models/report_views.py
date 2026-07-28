from odoo import api, fields, models, tools
from odoo.exceptions import UserError
from odoo.tools import date_utils


BENCHMARK_PERIOD_KEY = "USL benchmark 2024-01-10 to 2025-09-30"
CURRENT_PERIOD_KEY = "USL current from 2025-10-01"
USL_MEDIA_PERIOD_KEY = "USL Media full posted replay"

PERIOD_CASE_SQL = """
    CASE
        WHEN company.rebuild_source_id = 1
         AND move.date BETWEEN DATE '2024-01-10' AND DATE '2025-09-30'
        THEN 'USL benchmark 2024-01-10 to 2025-09-30'
        WHEN company.rebuild_source_id = 1
         AND move.date >= DATE '2025-10-01'
        THEN 'USL current from 2025-10-01'
        WHEN company.rebuild_source_id = 8
        THEN 'USL Media full posted replay'
        ELSE 'Other imported posted replay'
    END
"""

ANALYTIC_PERIOD_CASE_SQL = """
    CASE
        WHEN company.rebuild_source_id = 1
         AND analytic.date BETWEEN DATE '2024-01-10' AND DATE '2025-09-30'
        THEN 'USL benchmark 2024-01-10 to 2025-09-30'
        WHEN company.rebuild_source_id = 1
         AND analytic.date >= DATE '2025-10-01'
        THEN 'USL current from 2025-10-01'
        WHEN company.rebuild_source_id = 8
        THEN 'USL Media full posted replay'
        ELSE 'Other imported posted replay'
    END
"""


def _period_domain(record):
    if record.period_key == BENCHMARK_PERIOD_KEY:
        return [
            ("move_id.date", ">=", "2024-01-10"),
            ("move_id.date", "<=", "2025-09-30"),
        ]
    if record.period_key == CURRENT_PERIOD_KEY:
        return [("move_id.date", ">=", "2025-10-01")]
    if record.period_key == USL_MEDIA_PERIOD_KEY:
        return []
    return []


def _base_journal_item_domain(record):
    return [
        ("company_id", "=", record.company_id.id),
        ("rebuild_source_model", "=", "account.move.line"),
        ("move_id.rebuild_source_model", "=", "account.move"),
        ("move_id.state", "=", "posted"),
        *_period_domain(record),
    ]


def _analytic_line_period_domain(record):
    if record.period_key == BENCHMARK_PERIOD_KEY:
        return [
            ("date", ">=", "2024-01-10"),
            ("date", "<=", "2025-09-30"),
        ]
    if record.period_key == CURRENT_PERIOD_KEY:
        return [("date", ">=", "2025-10-01")]
    if record.period_key == USL_MEDIA_PERIOD_KEY:
        return []
    return []


def _analytic_lines_action(record, domain, name=None):
    record.ensure_one()
    return {
        "type": "ir.actions.act_window",
        "name": name or "Contributing Analytic Lines",
        "res_model": "account.analytic.line",
        "view_mode": "list,form,pivot",
        "views": [(False, "list"), (False, "form"), (False, "pivot")],
        "domain": domain,
        "context": {
            "create": False,
            "delete": False,
        },
    }


def _journal_items_action(record, domain, name=None):
    record.ensure_one()
    return {
        "type": "ir.actions.act_window",
        "name": name or "Contributing Journal Items",
        "res_model": "account.move.line",
        "view_mode": "list,form,pivot",
        "views": [(False, "list"), (False, "form"), (False, "pivot")],
        "domain": domain,
        "context": {
            "create": False,
            "delete": False,
        },
    }


def _single_journal_item_action(record):
    record.ensure_one()
    return _journal_items_action(
        record,
        [("id", "=", record.move_line_id.id)],
        name="Imported Journal Item",
    )


def _prefix_domain(field_name, prefixes):
    return fields.Domain.OR(
        fields.Domain(field_name, "=like", f"{prefix}%")
        for prefix in prefixes
    )


class RebuildAccountTrialBalanceLine(models.Model):
    _name = "rebuild.account.trial.balance.line"
    _description = "USL Imported Trial Balance Line"
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
                       company.rebuild_source_id AS source_company_id,
                       company.currency_id AS company_currency_id,
                       {PERIOD_CASE_SQL} AS period_key,
                       min(move.date) AS date_from,
                       max(move.date) AS date_to,
                       account.id AS account_id,
                       COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text) AS account_code,
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
                 WHERE line.rebuild_source_model = 'account.move.line'
                   AND move.rebuild_source_model = 'account.move'
                   AND move.state = 'posted'
                 GROUP BY line.company_id,
                          company.rebuild_source_id,
                          company.currency_id,
                          {PERIOD_CASE_SQL},
                          account.id,
                          COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text),
                          COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text),
                          account.account_type
            )
            """,
        )


class RebuildAccountGeneralLedgerLine(models.Model):
    _name = "rebuild.account.general.ledger.line"
    _description = "USL Imported General Ledger Line"
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
                       company.rebuild_source_id AS source_company_id,
                       company.currency_id AS company_currency_id,
                       {PERIOD_CASE_SQL} AS period_key,
                       move.date,
                       journal.id AS journal_id,
                       journal.code AS journal_code,
                       move.id AS move_id,
                       line.id AS move_line_id,
                       move.rebuild_source_id AS source_move_id,
                       line.rebuild_source_id AS source_line_id,
                       move.rebuild_source_move_type AS source_move_type,
                       move.name AS move_name,
                       line.partner_id,
                       account.id AS account_id,
                       COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text) AS account_code,
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
                 WHERE line.rebuild_source_model = 'account.move.line'
                   AND move.rebuild_source_model = 'account.move'
                   AND move.state = 'posted'
            )
            """,
        )


class RebuildAccountJournalReportLine(models.Model):
    _name = "rebuild.account.journal.report.line"
    _description = "USL Imported Journal Report Line"
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
                       company.rebuild_source_id AS source_company_id,
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
                 WHERE line.rebuild_source_model = 'account.move.line'
                   AND move.rebuild_source_model = 'account.move'
                   AND move.state = 'posted'
                 GROUP BY line.company_id,
                          company.rebuild_source_id,
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
    _description = "USL Imported Partner Ledger Line"
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
                       company.rebuild_source_id AS source_company_id,
                       company.currency_id AS company_currency_id,
                       {PERIOD_CASE_SQL} AS period_key,
                       move.date,
                       line.date_maturity,
                       journal.id AS journal_id,
                       journal.code AS journal_code,
                       move.id AS move_id,
                       line.id AS move_line_id,
                       move.rebuild_source_id AS source_move_id,
                       line.rebuild_source_id AS source_line_id,
                       move.name AS move_name,
                       line.partner_id,
                       account.id AS account_id,
                       COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text) AS account_code,
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
                 WHERE line.rebuild_source_model = 'account.move.line'
                   AND move.rebuild_source_model = 'account.move'
                   AND move.state = 'posted'
                   AND line.partner_id IS NOT NULL
            )
            """,
        )


class RebuildAccountOpenItemLine(models.Model):
    _name = "rebuild.account.open.item.line"
    _description = "USL Imported Open Item Line"
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
                       company.rebuild_source_id AS source_company_id,
                       company.currency_id AS company_currency_id,
                       {PERIOD_CASE_SQL} AS period_key,
                       move.date,
                       line.date_maturity,
                       journal.id AS journal_id,
                       journal.code AS journal_code,
                       move.id AS move_id,
                       line.id AS move_line_id,
                       move.rebuild_source_id AS source_move_id,
                       line.rebuild_source_id AS source_line_id,
                       move.name AS move_name,
                       line.partner_id,
                       account.id AS account_id,
                       COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text) AS account_code,
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
                 WHERE line.rebuild_source_model = 'account.move.line'
                   AND move.rebuild_source_model = 'account.move'
                   AND move.state = 'posted'
                   AND account.account_type IN ('asset_receivable', 'liability_payable')
                   AND (line.reconciled IS NOT TRUE OR abs(line.amount_residual) > 0.004)
            )
            """,
        )


class RebuildAccountAgedPartnerBalanceLine(models.Model):
    _name = "rebuild.account.aged.partner.balance.line"
    _description = "USL Imported Aged Partner Balance Line"
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
                           company.rebuild_source_id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           {PERIOD_CASE_SQL} AS period_key,
                           CASE
                               WHEN company.rebuild_source_id = 1
                                AND move.date BETWEEN DATE '2024-01-10' AND DATE '2025-09-30'
                               THEN DATE '2025-09-30'
                               ELSE DATE '2026-07-21'
                           END AS as_of_date,
                           line.partner_id,
                           account.account_type,
                           CASE
                               WHEN account.account_type = 'liability_payable' THEN -line.amount_residual
                               ELSE line.amount_residual
                           END AS presented_residual,
                           CASE
                               WHEN company.rebuild_source_id = 1
                                AND move.date BETWEEN DATE '2024-01-10' AND DATE '2025-09-30'
                               THEN DATE '2025-09-30'
                               ELSE DATE '2026-07-21'
                           END - COALESCE(line.date_maturity, move.date) AS age_days
                      FROM account_move_line line
                      JOIN account_move move ON move.id = line.move_id
                      JOIN res_company company ON company.id = line.company_id
                      JOIN account_account account ON account.id = line.account_id
                     WHERE line.rebuild_source_model = 'account.move.line'
                       AND move.rebuild_source_model = 'account.move'
                       AND move.state = 'posted'
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


class RebuildAccountFinancialStatementLine(models.Model):
    _name = "rebuild.account.financial.statement.line"
    _description = "USL Imported Financial Statement Line"
    _auto = False
    _order = "company_id, period_key, statement_key, section_sequence, account_code"

    company_id = fields.Many2one("res.company", readonly=True)
    source_company_id = fields.Integer(readonly=True)
    company_currency_id = fields.Many2one("res.currency", readonly=True)
    period_key = fields.Char(readonly=True)
    statement_key = fields.Char(readonly=True)
    statement_name = fields.Char(readonly=True)
    section_sequence = fields.Integer(readonly=True)
    section_name = fields.Char(readonly=True)
    account_id = fields.Many2one("account.account", readonly=True)
    account_code = fields.Char(readonly=True)
    account_name = fields.Char(readonly=True)
    account_type = fields.Char(readonly=True)
    move_line_count = fields.Integer(readonly=True)
    debit = fields.Monetary(currency_field="company_currency_id", readonly=True)
    credit = fields.Monetary(currency_field="company_currency_id", readonly=True)
    raw_balance = fields.Monetary(currency_field="company_currency_id", readonly=True)
    presentation_balance = fields.Monetary(currency_field="company_currency_id", readonly=True)
    statement_balance = fields.Monetary(currency_field="company_currency_id", readonly=True)

    def action_open_journal_items(self):
        self.ensure_one()
        domain = _base_journal_item_domain(self)
        if self.account_id:
            domain = [
                *domain,
                ("account_id", "=", self.account_id.id),
            ]
        elif self.account_type == "equity_current_year_result":
            domain = [
                *domain,
                (
                    "account_id.account_type",
                    "in",
                    ["income", "income_other", "expense", "expense_direct_cost", "expense_depreciation"],
                ),
            ]
        else:
            domain = [
                *domain,
                ("account_id.account_type", "=", self.account_type),
            ]
        return _journal_items_action(
            self,
            domain,
            name=f"{self.statement_name} Journal Items - {self.account_code}",
        )

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                WITH account_lines AS (
                    SELECT min(line.id) AS id,
                           line.company_id,
                           company.rebuild_source_id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           {PERIOD_CASE_SQL} AS period_key,
                           CASE
                               WHEN account.account_type IN ('income', 'income_other', 'expense', 'expense_direct_cost', 'expense_depreciation')
                               THEN 'profit_and_loss'
                               ELSE 'balance_sheet'
                           END AS statement_key,
                           CASE
                               WHEN account.account_type IN ('income', 'income_other', 'expense', 'expense_direct_cost', 'expense_depreciation')
                               THEN 'Profit and Loss'
                               ELSE 'Balance Sheet'
                           END AS statement_name,
                           CASE
                               WHEN account.account_type IN ('asset_fixed', 'asset_non_current') THEN 10
                               WHEN account.account_type LIKE 'asset%' THEN 20
                               WHEN account.account_type LIKE 'equity%' THEN 30
                               WHEN account.account_type LIKE 'liability%' THEN 40
                               WHEN account.account_type IN ('income', 'income_other') THEN 50
                               ELSE 60
                           END AS section_sequence,
                           CASE
                               WHEN account.account_type IN ('asset_fixed', 'asset_non_current') THEN 'Fixed assets'
                               WHEN account.account_type LIKE 'asset%' THEN 'Current assets'
                               WHEN account.account_type LIKE 'equity%' THEN 'Equity'
                               WHEN account.account_type LIKE 'liability%' THEN 'Liabilities'
                               WHEN account.account_type IN ('income', 'income_other') THEN 'Income'
                               ELSE 'Expenses'
                           END AS section_name,
                           account.id AS account_id,
                           COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text) AS account_code,
                           COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text) AS account_name,
                           account.account_type,
                           count(line.id) AS move_line_count,
                           round(sum(line.debit)::numeric, 2) AS debit,
                           round(sum(line.credit)::numeric, 2) AS credit,
                           round(sum(line.balance)::numeric, 2) AS raw_balance,
                           round(sum(
                               CASE
                                   WHEN account.account_type LIKE 'liability%%'
                                     OR account.account_type LIKE 'equity%%'
                                     OR account.account_type IN ('income', 'income_other')
                                   THEN -line.balance
                                   ELSE line.balance
                               END
                           )::numeric, 2) AS presentation_balance,
                           round(sum(
                               CASE
                                   WHEN account.account_type IN ('income', 'income_other') THEN -line.balance
                                   WHEN account.account_type IN ('expense', 'expense_direct_cost', 'expense_depreciation') THEN -line.balance
                                   ELSE line.balance
                               END
                           )::numeric, 2) AS statement_balance
                      FROM account_move_line line
                      JOIN account_move move ON move.id = line.move_id
                      JOIN res_company company ON company.id = line.company_id
                      JOIN account_account account ON account.id = line.account_id
                     WHERE line.rebuild_source_model = 'account.move.line'
                       AND move.rebuild_source_model = 'account.move'
                       AND move.state = 'posted'
                     GROUP BY line.company_id,
                              company.rebuild_source_id,
                              company.currency_id,
                              {PERIOD_CASE_SQL},
                              account.id,
                              COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text),
                              COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text),
                              account.account_type
                ),
                period_result AS (
                    SELECT -min(id) AS id,
                           company_id,
                           source_company_id,
                           company_currency_id,
                           period_key,
                           'balance_sheet' AS statement_key,
                           'Balance Sheet' AS statement_name,
                           35 AS section_sequence,
                           'Current-year result' AS section_name,
                           NULL::integer AS account_id,
                           'RESULT' AS account_code,
                           'Current-year result' AS account_name,
                           'equity_current_year_result' AS account_type,
                           sum(move_line_count)::integer AS move_line_count,
                           0::numeric AS debit,
                           0::numeric AS credit,
                           round(sum(raw_balance)::numeric, 2) AS raw_balance,
                           round(-sum(raw_balance)::numeric, 2) AS presentation_balance,
                           round(sum(raw_balance)::numeric, 2) AS statement_balance
                      FROM account_lines
                     WHERE statement_key = 'profit_and_loss'
                     GROUP BY company_id,
                              source_company_id,
                              company_currency_id,
                              period_key
                )
                SELECT * FROM account_lines
                UNION ALL
                SELECT * FROM period_result
            )
            """,
        )


class RebuildAccountTaxReportLine(models.Model):
    _name = "rebuild.account.tax.report.line"
    _description = "USL Imported VAT and Tax Report Line"
    _auto = False
    _order = "company_id, period_key, report_section, tax_tag_name, account_code"

    company_id = fields.Many2one("res.company", readonly=True)
    source_company_id = fields.Integer(readonly=True)
    company_currency_id = fields.Many2one("res.currency", readonly=True)
    period_key = fields.Char(readonly=True)
    report_section = fields.Char(readonly=True)
    tax_tag_id = fields.Many2one("account.account.tag", readonly=True)
    source_tax_tag_id = fields.Integer(readonly=True)
    tax_tag_name = fields.Char(readonly=True)
    account_id = fields.Many2one("account.account", readonly=True)
    account_code = fields.Char(readonly=True)
    account_name = fields.Char(readonly=True)
    move_line_count = fields.Integer(readonly=True)
    debit = fields.Monetary(currency_field="company_currency_id", readonly=True)
    credit = fields.Monetary(currency_field="company_currency_id", readonly=True)
    balance = fields.Monetary(currency_field="company_currency_id", readonly=True)
    tax_base_amount = fields.Monetary(currency_field="company_currency_id", readonly=True)

    def action_open_journal_items(self):
        self.ensure_one()
        domain = _base_journal_item_domain(self)
        if self.tax_tag_id:
            domain = [
                *domain,
                ("tax_tag_ids", "in", self.tax_tag_id.ids),
                ("account_id", "=", self.account_id.id),
            ]
        else:
            domain = [
                *domain,
                ("account_id", "=", self.account_id.id),
            ]
        return _journal_items_action(
            self,
            domain,
            name=f"Tax Journal Items - {self.account_code or self.tax_tag_name}",
        )

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                WITH tax_grid_lines AS (
                    SELECT line.company_id,
                           company.rebuild_source_id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           {PERIOD_CASE_SQL} AS period_key,
                           'Tax grid tags' AS report_section,
                           tag.id AS tax_tag_id,
                           tag.rebuild_source_id AS source_tax_tag_id,
                           COALESCE(tag.name->>'fr_FR', tag.name->>'en_US', tag.name::text) AS tax_tag_name,
                           account.id AS account_id,
                           COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text) AS account_code,
                           COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text) AS account_name,
                           count(line.id) AS move_line_count,
                           round(sum(line.debit)::numeric, 2) AS debit,
                           round(sum(line.credit)::numeric, 2) AS credit,
                           round(sum(line.balance)::numeric, 2) AS balance,
                           round(sum(line.tax_base_amount)::numeric, 2) AS tax_base_amount
                      FROM account_account_tag_account_move_line_rel rel
                      JOIN account_account_tag tag ON tag.id = rel.account_account_tag_id
                      JOIN account_move_line line ON line.id = rel.account_move_line_id
                      JOIN account_move move ON move.id = line.move_id
                      JOIN res_company company ON company.id = line.company_id
                      JOIN account_account account ON account.id = line.account_id
                     WHERE line.rebuild_source_model = 'account.move.line'
                       AND move.rebuild_source_model = 'account.move'
                       AND move.state = 'posted'
                     GROUP BY line.company_id,
                              company.rebuild_source_id,
                              company.currency_id,
                              {PERIOD_CASE_SQL},
                              tag.id,
                              tag.rebuild_source_id,
                              COALESCE(tag.name->>'fr_FR', tag.name->>'en_US', tag.name::text),
                              account.id,
                              COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text),
                              COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text)
                ),
                vat_account_lines AS (
                    SELECT line.company_id,
                           company.rebuild_source_id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           {PERIOD_CASE_SQL} AS period_key,
                           'VAT accounts' AS report_section,
                           NULL::integer AS tax_tag_id,
                           NULL::integer AS source_tax_tag_id,
                           NULL::text AS tax_tag_name,
                           account.id AS account_id,
                           COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text) AS account_code,
                           COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text) AS account_name,
                           count(line.id) AS move_line_count,
                           round(sum(line.debit)::numeric, 2) AS debit,
                           round(sum(line.credit)::numeric, 2) AS credit,
                           round(sum(line.balance)::numeric, 2) AS balance,
                           round(sum(line.tax_base_amount)::numeric, 2) AS tax_base_amount
                      FROM account_move_line line
                      JOIN account_move move ON move.id = line.move_id
                      JOIN res_company company ON company.id = line.company_id
                      JOIN account_account account ON account.id = line.account_id
                     WHERE line.rebuild_source_model = 'account.move.line'
                       AND move.rebuild_source_model = 'account.move'
                       AND move.state = 'posted'
                       AND COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text) LIKE '445%'
                     GROUP BY line.company_id,
                              company.rebuild_source_id,
                              company.currency_id,
                              {PERIOD_CASE_SQL},
                              account.id,
                              COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text),
                              COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text)
                ),
                combined AS (
                    SELECT * FROM tax_grid_lines
                    UNION ALL
                    SELECT * FROM vat_account_lines
                )
                SELECT row_number() OVER (
                           ORDER BY company_id, period_key, report_section, COALESCE(tax_tag_name, ''), account_code
                       )::integer AS id,
                       combined.*
                  FROM combined
            )
            """,
        )


class RebuildAccountEuTaxReportLine(models.Model):
    _name = "rebuild.account.eu.tax.report.line"
    _description = "USL Imported EC/OSS Tax Review Line"
    _auto = False
    _order = "company_id, period_key, report_type, country_code, partner_name, tax_name, account_code"

    company_id = fields.Many2one("res.company", readonly=True)
    source_company_id = fields.Integer(readonly=True)
    company_currency_id = fields.Many2one("res.currency", readonly=True)
    period_key = fields.Char(readonly=True)
    report_type = fields.Selection(
        [
            ("ec_sales_list", "EC Sales List"),
            ("oss_sales", "OSS Sales"),
            ("oss_imports", "OSS Imports"),
        ],
        readonly=True,
    )
    report_name = fields.Char(readonly=True)
    country_code = fields.Char(readonly=True)
    country_name = fields.Char(readonly=True)
    partner_id = fields.Many2one("res.partner", readonly=True)
    partner_name = fields.Char(readonly=True)
    vat_number = fields.Char(readonly=True)
    tax_id = fields.Many2one("account.tax", readonly=True)
    source_tax_id = fields.Integer(readonly=True)
    tax_name = fields.Char(readonly=True)
    tax_tag_id = fields.Many2one("account.account.tag", readonly=True)
    source_tax_tag_id = fields.Integer(readonly=True)
    tax_tag_name = fields.Char(readonly=True)
    journal_id = fields.Many2one("account.journal", readonly=True)
    journal_code = fields.Char(readonly=True)
    account_id = fields.Many2one("account.account", readonly=True)
    account_code = fields.Char(readonly=True)
    account_name = fields.Char(readonly=True)
    move_count = fields.Integer(readonly=True)
    move_line_count = fields.Integer(readonly=True)
    taxable_amount = fields.Monetary(currency_field="company_currency_id", readonly=True)
    tax_amount = fields.Monetary(currency_field="company_currency_id", readonly=True)
    balance = fields.Monetary(currency_field="company_currency_id", readonly=True)
    review_status = fields.Char(readonly=True)

    def action_open_journal_items(self):
        self.ensure_one()
        domain = _base_journal_item_domain(self)
        if self.partner_id:
            domain.append(("partner_id", "=", self.partner_id.id))
        if self.journal_id:
            domain.append(("journal_id", "=", self.journal_id.id))
        if self.account_id:
            domain.append(("account_id", "=", self.account_id.id))
        if self.tax_id:
            domain.append(("tax_ids", "in", [self.tax_id.id]))
        if self.tax_tag_id:
            domain.append(("tax_tag_ids", "in", [self.tax_tag_id.id]))
        return _journal_items_action(
            self,
            domain,
            name=f"{self.report_name} Journal Items - {self.partner_name or self.country_code or self.account_code}",
        )

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                WITH tax_rel_lines AS (
                    SELECT line.id AS move_line_id,
                           line.company_id,
                           company.rebuild_source_id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           {PERIOD_CASE_SQL} AS period_key,
                           line.move_id,
                           move.journal_id,
                           journal.code AS journal_code,
                           line.partner_id,
                           partner.name::text AS partner_name,
                           partner.vat::text AS vat_number,
                           COALESCE(
                               country.code::text,
                               CASE
                                   WHEN partner.vat::text ~* '^[A-Z][A-Z]'
                                   THEN upper(substring(partner.vat::text from 1 for 2))
                                   ELSE ''
                               END
                           ) AS country_code,
                           COALESCE(country.name->>'fr_FR', country.name->>'en_US', country.name::text) AS country_name,
                           account.id AS account_id,
                           COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text) AS account_code,
                           COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text) AS account_name,
                           account.account_type,
                           tax.id AS tax_id,
                           tax.rebuild_source_id AS source_tax_id,
                           COALESCE(tax.name->>'fr_FR', tax.name->>'en_US', tax.name::text) AS tax_name,
                           NULL::integer AS tax_tag_id,
                           NULL::integer AS source_tax_tag_id,
                           NULL::text AS tax_tag_name,
                           tax.type_tax_use,
                           line.debit,
                           line.credit,
                           line.balance,
                           line.tax_base_amount
                      FROM account_move_line_account_tax_rel tax_rel
                      JOIN account_tax tax ON tax.id = tax_rel.account_tax_id
                      JOIN account_move_line line ON line.id = tax_rel.account_move_line_id
                      JOIN account_move move ON move.id = line.move_id
                      JOIN account_journal journal ON journal.id = move.journal_id
                      JOIN res_company company ON company.id = line.company_id
                      JOIN account_account account ON account.id = line.account_id
                 LEFT JOIN res_partner partner ON partner.id = line.partner_id
                 LEFT JOIN res_country country ON country.id = partner.country_id
                     WHERE line.rebuild_source_model = 'account.move.line'
                       AND move.rebuild_source_model = 'account.move'
                       AND move.state = 'posted'
                ),
                tag_rel_lines AS (
                    SELECT line.id AS move_line_id,
                           line.company_id,
                           company.rebuild_source_id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           {PERIOD_CASE_SQL} AS period_key,
                           line.move_id,
                           move.journal_id,
                           journal.code AS journal_code,
                           line.partner_id,
                           partner.name::text AS partner_name,
                           partner.vat::text AS vat_number,
                           COALESCE(
                               country.code::text,
                               CASE
                                   WHEN partner.vat::text ~* '^[A-Z][A-Z]'
                                   THEN upper(substring(partner.vat::text from 1 for 2))
                                   ELSE ''
                               END
                           ) AS country_code,
                           COALESCE(country.name->>'fr_FR', country.name->>'en_US', country.name::text) AS country_name,
                           account.id AS account_id,
                           COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text) AS account_code,
                           COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text) AS account_name,
                           account.account_type,
                           NULL::integer AS tax_id,
                           NULL::integer AS source_tax_id,
                           NULL::text AS tax_name,
                           tag.id AS tax_tag_id,
                           tag.rebuild_source_id AS source_tax_tag_id,
                           COALESCE(tag.name->>'fr_FR', tag.name->>'en_US', tag.name::text) AS tax_tag_name,
                           NULL::text AS type_tax_use,
                           line.debit,
                           line.credit,
                           line.balance,
                           line.tax_base_amount
                      FROM account_account_tag_account_move_line_rel tag_rel
                      JOIN account_account_tag tag ON tag.id = tag_rel.account_account_tag_id
                      JOIN account_move_line line ON line.id = tag_rel.account_move_line_id
                      JOIN account_move move ON move.id = line.move_id
                      JOIN account_journal journal ON journal.id = move.journal_id
                      JOIN res_company company ON company.id = line.company_id
                      JOIN account_account account ON account.id = line.account_id
                 LEFT JOIN res_partner partner ON partner.id = line.partner_id
                 LEFT JOIN res_country country ON country.id = partner.country_id
                     WHERE line.rebuild_source_model = 'account.move.line'
                       AND move.rebuild_source_model = 'account.move'
                       AND move.state = 'posted'
                ),
                tagged AS (
                    SELECT 'ec_sales_list' AS report_type,
                           'EC Sales List' AS report_name,
                           'ledger_derived_review' AS review_status,
                           *
                      FROM tax_rel_lines
                     WHERE type_tax_use = 'sale'
                       AND account_type IN ('income', 'income_other')
                       AND (
                           lower(COALESCE(tax_name, '')) LIKE '%eu%'
                           OR lower(COALESCE(tax_name, '')) LIKE '%des%'
                       )
                    UNION ALL
                    SELECT CASE
                               WHEN COALESCE(type_tax_use, '') = 'purchase'
                                    OR account_type LIKE 'expense%'
                               THEN 'oss_imports'
                               ELSE 'oss_sales'
                           END AS report_type,
                           CASE
                               WHEN COALESCE(type_tax_use, '') = 'purchase'
                                    OR account_type LIKE 'expense%'
                               THEN 'OSS Imports'
                               ELSE 'OSS Sales'
                           END AS report_name,
                           'ledger_derived_review' AS review_status,
                           *
                      FROM tax_rel_lines
                     WHERE lower(COALESCE(tax_name, '')) LIKE '%oss%'
                    UNION ALL
                    SELECT CASE
                               WHEN COALESCE(source_tax_tag_id, 0) = 106
                                    OR account_type LIKE 'expense%'
                               THEN 'oss_imports'
                               ELSE 'oss_sales'
                           END AS report_type,
                           CASE
                               WHEN COALESCE(source_tax_tag_id, 0) = 106
                                    OR account_type LIKE 'expense%'
                               THEN 'OSS Imports'
                               ELSE 'OSS Sales'
                           END AS report_name,
                           'ledger_derived_review' AS review_status,
                           *
                      FROM tag_rel_lines
                     WHERE COALESCE(source_tax_tag_id, 0) IN (105, 106)
                        OR lower(COALESCE(tax_tag_name, '')) LIKE '%oss%'
                )
                SELECT row_number() OVER (
                           ORDER BY company_id, period_key, report_type, COALESCE(country_code, ''), COALESCE(partner_name, ''), COALESCE(tax_name, tax_tag_name, ''), journal_code, account_code
                       )::integer AS id,
                       company_id,
                       source_company_id,
                       company_currency_id,
                       period_key,
                       report_type,
                       report_name,
                       COALESCE(country_code, '') AS country_code,
                       COALESCE(country_name, '') AS country_name,
                       partner_id,
                       COALESCE(partner_name, '') AS partner_name,
                       COALESCE(vat_number, '') AS vat_number,
                       tax_id,
                       source_tax_id,
                       COALESCE(tax_name, '') AS tax_name,
                       tax_tag_id,
                       source_tax_tag_id,
                       COALESCE(tax_tag_name, '') AS tax_tag_name,
                       journal_id,
                       journal_code,
                       account_id,
                       account_code,
                       account_name,
                       count(DISTINCT move_id)::integer AS move_count,
                       count(DISTINCT move_line_id)::integer AS move_line_count,
                       round(sum(CASE
                           WHEN account_type IN ('income', 'income_other') THEN -balance
                           WHEN account_type LIKE 'expense%' THEN balance
                           ELSE COALESCE(tax_base_amount, 0)
                       END)::numeric, 2) AS taxable_amount,
                       round(sum(CASE
                           WHEN account_code LIKE '445%' THEN abs(balance)
                           ELSE 0
                       END)::numeric, 2) AS tax_amount,
                       round(sum(balance)::numeric, 2) AS balance,
                       review_status
                  FROM tagged
                 GROUP BY company_id,
                          source_company_id,
                          company_currency_id,
                          period_key,
                          report_type,
                          report_name,
                          country_code,
                          country_name,
                          partner_id,
                          partner_name,
                          vat_number,
                          tax_id,
                          source_tax_id,
                          tax_name,
                          tax_tag_id,
                          source_tax_tag_id,
                          tax_tag_name,
                          journal_id,
                          journal_code,
                          account_id,
                          account_code,
                          account_name,
                          review_status
            )
            """,
        )


class RebuildAccountBankReconciliationLine(models.Model):
    _name = "rebuild.account.bank.reconciliation.line"
    _description = "USL Imported Bank Reconciliation Line"
    _auto = False
    _order = "company_id, period_key, journal_code, date, source_statement_line_id"

    company_id = fields.Many2one("res.company", readonly=True)
    source_company_id = fields.Integer(readonly=True)
    company_currency_id = fields.Many2one("res.currency", readonly=True)
    period_key = fields.Char(readonly=True)
    date = fields.Date(readonly=True)
    journal_id = fields.Many2one("account.journal", readonly=True)
    journal_code = fields.Char(readonly=True)
    statement_line_id = fields.Many2one("account.bank.statement.line", readonly=True)
    source_statement_line_id = fields.Integer(readonly=True)
    move_id = fields.Many2one("account.move", readonly=True)
    move_name = fields.Char(readonly=True)
    partner_id = fields.Many2one("res.partner", readonly=True)
    partner_name = fields.Char(readonly=True)
    payment_ref = fields.Char(readonly=True)
    transaction_type = fields.Char(readonly=True)
    account_number = fields.Char(readonly=True)
    internal_index = fields.Char(readonly=True)
    amount = fields.Monetary(currency_field="company_currency_id", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    foreign_currency_id = fields.Many2one("res.currency", readonly=True)
    amount_currency = fields.Monetary(currency_field="currency_id", readonly=True)
    amount_residual = fields.Monetary(currency_field="company_currency_id", readonly=True)
    is_reconciled = fields.Boolean(readonly=True)
    reconciliation_status = fields.Char(readonly=True)
    move_line_count = fields.Integer(readonly=True)

    def action_open_journal_items(self):
        self.ensure_one()
        if self.move_id:
            domain = [("move_id", "=", self.move_id.id)]
        else:
            domain = [
                ("statement_line_id", "=", self.statement_line_id.id),
                ("company_id", "=", self.company_id.id),
            ]
        return _journal_items_action(
            self,
            domain,
            name=f"Bank Statement Journal Items - {self.payment_ref or self.move_name}",
        )

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT bsl.id AS id,
                       bsl.company_id,
                       company.rebuild_source_id AS source_company_id,
                       company.currency_id AS company_currency_id,
                       {PERIOD_CASE_SQL} AS period_key,
                       move.date,
                       journal.id AS journal_id,
                       journal.code AS journal_code,
                       bsl.id AS statement_line_id,
                       bsl.rebuild_source_id AS source_statement_line_id,
                       move.id AS move_id,
                       move.name AS move_name,
                       bsl.partner_id,
                       COALESCE(partner.name::text, bsl.partner_name::text, '') AS partner_name,
                       COALESCE(bsl.payment_ref::text, '') AS payment_ref,
                       COALESCE(bsl.transaction_type::text, '') AS transaction_type,
                       COALESCE(bsl.account_number::text, '') AS account_number,
                       COALESCE(bsl.internal_index::text, '') AS internal_index,
                       round(bsl.amount::numeric, 2) AS amount,
                       bsl.currency_id,
                       bsl.foreign_currency_id,
                       round(bsl.amount_currency::numeric, 2) AS amount_currency,
                       round(bsl.amount_residual::numeric, 2) AS amount_residual,
                       bsl.is_reconciled,
                       CASE
                           WHEN bsl.is_reconciled THEN 'Reconciled'
                           WHEN abs(bsl.amount_residual) > 0.004 THEN 'Open residual'
                           ELSE 'Not reconciled'
                       END AS reconciliation_status,
                       count(line.id)::integer AS move_line_count
                  FROM account_bank_statement_line bsl
                  JOIN account_move move ON move.id = bsl.move_id
                  JOIN res_company company ON company.id = bsl.company_id
                  JOIN account_journal journal ON journal.id = bsl.journal_id
                  LEFT JOIN res_partner partner ON partner.id = bsl.partner_id
                  LEFT JOIN account_move_line line ON line.move_id = move.id
                 WHERE bsl.rebuild_source_model = 'account.bank.statement.line'
                   AND move.rebuild_source_model = 'account.move'
                   AND move.state = 'posted'
                 GROUP BY bsl.id,
                          bsl.company_id,
                          company.rebuild_source_id,
                          company.currency_id,
                          {PERIOD_CASE_SQL},
                          move.date,
                          journal.id,
                          journal.code,
                          move.id,
                          move.name,
                          bsl.partner_id,
                          COALESCE(partner.name::text, bsl.partner_name::text, ''),
                          bsl.payment_ref,
                          bsl.transaction_type,
                          bsl.account_number,
                          bsl.internal_index,
                          bsl.amount,
                          bsl.currency_id,
                          bsl.foreign_currency_id,
                          bsl.amount_currency,
                          bsl.amount_residual,
                          bsl.is_reconciled
            )
            """,
        )


class RebuildAccountCurrencyReportLine(models.Model):
    _name = "rebuild.account.currency.report.line"
    _description = "USL Imported Currency Gain, Loss and Exposure Line"
    _auto = False
    _order = "company_id, period_key, report_section, currency_id, account_code, partner_id"

    company_id = fields.Many2one("res.company", readonly=True)
    source_company_id = fields.Integer(readonly=True)
    company_currency_id = fields.Many2one("res.currency", readonly=True)
    period_key = fields.Char(readonly=True)
    report_section = fields.Char(readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    account_id = fields.Many2one("account.account", readonly=True)
    account_code = fields.Char(readonly=True)
    account_name = fields.Char(readonly=True)
    account_type = fields.Char(readonly=True)
    partner_id = fields.Many2one("res.partner", readonly=True)
    move_line_count = fields.Integer(readonly=True)
    debit = fields.Monetary(currency_field="company_currency_id", readonly=True)
    credit = fields.Monetary(currency_field="company_currency_id", readonly=True)
    balance = fields.Monetary(currency_field="company_currency_id", readonly=True)
    amount_currency = fields.Monetary(currency_field="currency_id", readonly=True)
    amount_residual = fields.Monetary(currency_field="company_currency_id", readonly=True)
    amount_residual_currency = fields.Monetary(currency_field="currency_id", readonly=True)

    def action_open_journal_items(self):
        self.ensure_one()
        domain = _base_journal_item_domain(self)
        if self.account_id:
            domain.append(("account_id", "=", self.account_id.id))
        if self.currency_id:
            domain.append(("currency_id", "=", self.currency_id.id))
        if self.partner_id:
            domain.append(("partner_id", "=", self.partner_id.id))
        if self.report_section == "Unrealized foreign-currency open items":
            domain.extend([
                ("account_id.account_type", "in", ["asset_receivable", "liability_payable"]),
                "|",
                ("reconciled", "=", False),
                ("amount_residual", "!=", 0),
            ])
        return _journal_items_action(
            self,
            domain,
            name=f"{self.report_section} - {self.account_code or self.currency_id.name}",
        )

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                WITH base_lines AS (
                    SELECT line.company_id,
                           company.rebuild_source_id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           {PERIOD_CASE_SQL} AS period_key,
                           line.currency_id,
                           account.id AS account_id,
                           COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text) AS account_code,
                           COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text) AS account_name,
                           account.account_type,
                           line.partner_id,
                           line.id AS move_line_id,
                           line.debit,
                           line.credit,
                           line.balance,
                           line.amount_currency,
                           line.amount_residual,
                           line.amount_residual_currency,
                           line.reconciled
                      FROM account_move_line line
                      JOIN account_move move ON move.id = line.move_id
                      JOIN res_company company ON company.id = line.company_id
                      JOIN account_account account ON account.id = line.account_id
                     WHERE line.rebuild_source_model = 'account.move.line'
                       AND move.rebuild_source_model = 'account.move'
                       AND move.state = 'posted'
                ),
                combined AS (
                    SELECT company_id,
                           source_company_id,
                           company_currency_id,
                           period_key,
                           'Foreign currency ledger' AS report_section,
                           currency_id,
                           account_id,
                           account_code,
                           account_name,
                           account_type,
                           partner_id,
                           count(move_line_id)::integer AS move_line_count,
                           round(sum(debit)::numeric, 2) AS debit,
                           round(sum(credit)::numeric, 2) AS credit,
                           round(sum(balance)::numeric, 2) AS balance,
                           round(sum(amount_currency)::numeric, 2) AS amount_currency,
                           round(sum(amount_residual)::numeric, 2) AS amount_residual,
                           round(sum(amount_residual_currency)::numeric, 2) AS amount_residual_currency
                      FROM base_lines
                     WHERE currency_id IS NOT NULL
                       AND currency_id != company_currency_id
                     GROUP BY company_id,
                              source_company_id,
                              company_currency_id,
                              period_key,
                              currency_id,
                              account_id,
                              account_code,
                              account_name,
                              account_type,
                              partner_id
                    UNION ALL
                    SELECT company_id,
                           source_company_id,
                           company_currency_id,
                           period_key,
                           'Realized exchange gains and losses',
                           currency_id,
                           account_id,
                           account_code,
                           account_name,
                           account_type,
                           partner_id,
                           count(move_line_id)::integer,
                           round(sum(debit)::numeric, 2),
                           round(sum(credit)::numeric, 2),
                           round(sum(balance)::numeric, 2),
                           round(sum(amount_currency)::numeric, 2),
                           round(sum(amount_residual)::numeric, 2),
                           round(sum(amount_residual_currency)::numeric, 2)
                      FROM base_lines
                     WHERE account_code LIKE '666%'
                        OR account_code LIKE '766%'
                     GROUP BY company_id,
                              source_company_id,
                              company_currency_id,
                              period_key,
                              currency_id,
                              account_id,
                              account_code,
                              account_name,
                              account_type,
                              partner_id
                    UNION ALL
                    SELECT company_id,
                           source_company_id,
                           company_currency_id,
                           period_key,
                           'Unrealized foreign-currency open items',
                           currency_id,
                           account_id,
                           account_code,
                           account_name,
                           account_type,
                           partner_id,
                           count(move_line_id)::integer,
                           round(sum(debit)::numeric, 2),
                           round(sum(credit)::numeric, 2),
                           round(sum(balance)::numeric, 2),
                           round(sum(amount_currency)::numeric, 2),
                           round(sum(amount_residual)::numeric, 2),
                           round(sum(amount_residual_currency)::numeric, 2)
                      FROM base_lines
                     WHERE currency_id IS NOT NULL
                       AND currency_id != company_currency_id
                       AND account_type IN ('asset_receivable', 'liability_payable')
                       AND (reconciled IS NOT TRUE
                            OR abs(amount_residual) > 0.004
                            OR abs(amount_residual_currency) > 0.004)
                     GROUP BY company_id,
                              source_company_id,
                              company_currency_id,
                              period_key,
                              currency_id,
                              account_id,
                              account_code,
                              account_name,
                              account_type,
                              partner_id
                )
                SELECT row_number() OVER (
                           ORDER BY company_id, period_key, report_section, currency_id, account_code, partner_id NULLS FIRST
                       )::integer AS id,
                       combined.*
                  FROM combined
            )
            """,
        )


class RebuildAccountManagementSummaryLine(models.Model):
    _name = "rebuild.account.management.summary.line"
    _description = "USL Imported Cash Flow and Executive Summary Line"
    _auto = False
    _order = "company_id, period_key, report_key, line_sequence"

    company_id = fields.Many2one("res.company", readonly=True)
    source_company_id = fields.Integer(readonly=True)
    company_currency_id = fields.Many2one("res.currency", readonly=True)
    period_key = fields.Char(readonly=True)
    report_key = fields.Char(readonly=True)
    report_name = fields.Char(readonly=True)
    line_sequence = fields.Integer(readonly=True)
    line_code = fields.Char(readonly=True)
    line_name = fields.Char(readonly=True)
    metric_type = fields.Char(readonly=True)
    source_formula = fields.Char(readonly=True)
    drilldown_kind = fields.Char(readonly=True)
    move_line_count = fields.Integer(readonly=True)
    amount = fields.Monetary(currency_field="company_currency_id", readonly=True)
    metric_value = fields.Float(readonly=True)

    def action_open_journal_items(self):
        self.ensure_one()
        domain = _base_journal_item_domain(self)
        if self.drilldown_kind == "cash_received":
            domain.extend([
                ("account_id.account_type", "in", ["asset_cash", "liability_credit_card"]),
                ("debit", ">", 0),
            ])
        elif self.drilldown_kind == "cash_spent":
            domain.extend([
                ("account_id.account_type", "in", ["asset_cash", "liability_credit_card"]),
                ("credit", ">", 0),
            ])
        elif self.drilldown_kind == "cash":
            domain.append(("account_id.account_type", "in", ["asset_cash", "liability_credit_card"]))
        elif self.drilldown_kind == "revenue":
            domain.append(("account_id.account_type", "in", ["income", "income_other"]))
        elif self.drilldown_kind == "cost_of_revenue":
            domain.append(("account_id.account_type", "=", "expense_direct_cost"))
        elif self.drilldown_kind == "expenses":
            domain.append(("account_id.account_type", "in", ["expense", "expense_depreciation"]))
        elif self.drilldown_kind == "profit_loss":
            domain.append((
                "account_id.account_type",
                "in",
                ["income", "income_other", "expense", "expense_direct_cost", "expense_depreciation"],
            ))
        elif self.drilldown_kind == "receivable":
            domain.append(("account_id.account_type", "=", "asset_receivable"))
        elif self.drilldown_kind == "payable":
            domain.append(("account_id.account_type", "=", "liability_payable"))
        elif self.drilldown_kind == "net_assets":
            domain.append(("account_id.account_type", "not in", [
                "income",
                "income_other",
                "expense",
                "expense_direct_cost",
                "expense_depreciation",
            ]))
        elif self.drilldown_kind == "current_assets_liabilities":
            domain.append(("account_id.account_type", "in", [
                "asset_current",
                "asset_receivable",
                "asset_cash",
                "liability_current",
                "liability_payable",
                "liability_credit_card",
            ]))
        return _journal_items_action(
            self,
            domain,
            name=f"{self.report_name} - {self.line_name}",
        )

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                WITH base_lines AS (
                    SELECT line.company_id,
                           company.rebuild_source_id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           {PERIOD_CASE_SQL} AS period_key,
                           account.account_type,
                           line.id AS move_line_id,
                           line.debit,
                           line.credit,
                           line.balance,
                           move.date
                      FROM account_move_line line
                      JOIN account_move move ON move.id = line.move_id
                      JOIN res_company company ON company.id = line.company_id
                      JOIN account_account account ON account.id = line.account_id
                     WHERE line.rebuild_source_model = 'account.move.line'
                       AND move.rebuild_source_model = 'account.move'
                       AND move.state = 'posted'
                ),
                aggregates AS (
                    SELECT company_id,
                           source_company_id,
                           company_currency_id,
                           period_key,
                           count(move_line_id)::integer AS all_line_count,
                           greatest((max(date) - min(date) + 1), 1)::numeric AS day_count,
                           count(move_line_id) FILTER (
                               WHERE account_type IN ('asset_cash', 'liability_credit_card')
                           )::integer AS cash_line_count,
                           round(COALESCE(sum(debit) FILTER (
                               WHERE account_type IN ('asset_cash', 'liability_credit_card')
                           ), 0)::numeric, 2) AS cash_received,
                           round(COALESCE(sum(credit) FILTER (
                               WHERE account_type IN ('asset_cash', 'liability_credit_card')
                           ), 0)::numeric, 2) AS cash_spent,
                           round(COALESCE(sum(balance) FILTER (
                               WHERE account_type IN ('asset_cash', 'liability_credit_card')
                           ), 0)::numeric, 2) AS closing_cash,
                           count(move_line_id) FILTER (
                               WHERE account_type IN ('income', 'income_other')
                           )::integer AS revenue_line_count,
                           round(-COALESCE(sum(balance) FILTER (
                               WHERE account_type IN ('income', 'income_other')
                           ), 0)::numeric, 2) AS revenue,
                           count(move_line_id) FILTER (
                               WHERE account_type = 'expense_direct_cost'
                           )::integer AS cost_line_count,
                           round(COALESCE(sum(balance) FILTER (
                               WHERE account_type = 'expense_direct_cost'
                           ), 0)::numeric, 2) AS cost_of_revenue,
                           count(move_line_id) FILTER (
                               WHERE account_type IN ('expense', 'expense_depreciation')
                           )::integer AS expense_line_count,
                           round(COALESCE(sum(balance) FILTER (
                               WHERE account_type IN ('expense', 'expense_depreciation')
                           ), 0)::numeric, 2) AS expenses,
                           count(move_line_id) FILTER (
                               WHERE account_type IN ('income', 'income_other', 'expense', 'expense_direct_cost', 'expense_depreciation')
                           )::integer AS profit_loss_line_count,
                           round(-COALESCE(sum(balance) FILTER (
                               WHERE account_type IN ('income', 'income_other', 'expense', 'expense_direct_cost', 'expense_depreciation')
                           ), 0)::numeric, 2) AS net_profit,
                           count(move_line_id) FILTER (
                               WHERE account_type = 'asset_receivable'
                           )::integer AS receivable_line_count,
                           round(COALESCE(sum(balance) FILTER (
                               WHERE account_type = 'asset_receivable'
                           ), 0)::numeric, 2) AS receivables,
                           count(move_line_id) FILTER (
                               WHERE account_type = 'liability_payable'
                           )::integer AS payable_line_count,
                           round(-COALESCE(sum(balance) FILTER (
                               WHERE account_type = 'liability_payable'
                           ), 0)::numeric, 2) AS payables,
                           count(move_line_id) FILTER (
                               WHERE account_type LIKE 'asset%%'
                                  OR account_type LIKE 'liability%%'
                           )::integer AS net_asset_line_count,
                           round((
                               COALESCE(sum(balance) FILTER (WHERE account_type LIKE 'asset%%'), 0)
                               + COALESCE(sum(balance) FILTER (WHERE account_type LIKE 'liability%%'), 0)
                           )::numeric, 2) AS net_assets,
                           count(move_line_id) FILTER (
                               WHERE account_type IN ('asset_current', 'asset_receivable', 'asset_cash')
                                  OR account_type IN ('liability_current', 'liability_payable', 'liability_credit_card')
                           )::integer AS current_line_count,
                           round(COALESCE(sum(balance) FILTER (
                               WHERE account_type IN ('asset_current', 'asset_receivable', 'asset_cash')
                           ), 0)::numeric, 2) AS current_assets,
                           round(-COALESCE(sum(balance) FILTER (
                               WHERE account_type IN ('liability_current', 'liability_payable', 'liability_credit_card')
                           ), 0)::numeric, 2) AS current_liabilities
                      FROM base_lines
                     GROUP BY company_id,
                              source_company_id,
                              company_currency_id,
                              period_key
                ),
                line_sources AS (
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'cash_flow' AS report_key, 'Cash Flow Statement' AS report_name, 10 AS line_sequence,
                           'CASH_RECEIVED' AS line_code, 'Cash received' AS line_name,
                           'currency' AS metric_type, 'Debit movements on cash and credit-card accounts' AS source_formula,
                           'cash_received' AS drilldown_kind, cash_line_count AS move_line_count,
                           cash_received AS amount, cash_received::double precision AS metric_value
                      FROM aggregates
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'cash_flow', 'Cash Flow Statement', 20,
                           'CASH_SPENT', 'Cash spent',
                           'currency', 'Credit movements on cash and credit-card accounts',
                           'cash_spent', cash_line_count,
                           cash_spent, cash_spent::double precision
                      FROM aggregates
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'cash_flow', 'Cash Flow Statement', 30,
                           'CASH_SURPLUS', 'Cash surplus',
                           'currency', 'Cash received minus cash spent',
                           'cash', cash_line_count,
                           cash_received - cash_spent, (cash_received - cash_spent)::double precision
                      FROM aggregates
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'cash_flow', 'Cash Flow Statement', 40,
                           'CLOSING_CASH', 'Closing bank balance',
                           'currency', 'Closing balance of cash and credit-card accounts',
                           'cash', cash_line_count,
                           closing_cash, closing_cash::double precision
                      FROM aggregates
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'executive_summary', 'Executive Summary', 10,
                           'REVENUE', 'Total income',
                           'currency', 'Income and other income account balances with management sign',
                           'revenue', revenue_line_count,
                           revenue, revenue::double precision
                      FROM aggregates
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'executive_summary', 'Executive Summary', 20,
                           'COST_OF_REVENUE', 'Cost of revenue',
                           'currency', 'Direct-cost expense account balances',
                           'cost_of_revenue', cost_line_count,
                           cost_of_revenue, cost_of_revenue::double precision
                      FROM aggregates
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'executive_summary', 'Executive Summary', 30,
                           'GROSS_PROFIT', 'Gross profit',
                           'currency', 'Revenue minus cost of revenue',
                           'profit_loss', profit_loss_line_count,
                           revenue - cost_of_revenue, (revenue - cost_of_revenue)::double precision
                      FROM aggregates
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'executive_summary', 'Executive Summary', 40,
                           'EXPENSES', 'Expenses',
                           'currency', 'Operating, depreciation and other expense account balances excluding direct costs',
                           'expenses', expense_line_count,
                           expenses, expenses::double precision
                      FROM aggregates
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'executive_summary', 'Executive Summary', 50,
                           'NET_PROFIT', 'Net profit',
                           'currency', 'Net balance of income and expense accounts with management sign',
                           'profit_loss', profit_loss_line_count,
                           net_profit, net_profit::double precision
                      FROM aggregates
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'executive_summary', 'Executive Summary', 60,
                           'RECEIVABLES', 'Receivables',
                           'currency', 'Receivable account balances',
                           'receivable', receivable_line_count,
                           receivables, receivables::double precision
                      FROM aggregates
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'executive_summary', 'Executive Summary', 70,
                           'PAYABLES', 'Payables',
                           'currency', 'Payable account balances with liability sign',
                           'payable', payable_line_count,
                           payables, payables::double precision
                      FROM aggregates
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'executive_summary', 'Executive Summary', 80,
                           'NET_ASSETS', 'Net assets',
                           'currency', 'Asset balances minus liability balances',
                           'net_assets', net_asset_line_count,
                           net_assets, net_assets::double precision
                      FROM aggregates
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'executive_summary', 'Executive Summary', 90,
                           'GROSS_PROFIT_MARGIN', 'Gross profit margin',
                           'percent', '(Gross profit / revenue) * 100, zero when revenue is zero',
                           'profit_loss', profit_loss_line_count,
                           0::numeric,
                           CASE WHEN revenue = 0 THEN 0 ELSE round(((revenue - cost_of_revenue) / revenue * 100)::numeric, 4)::double precision END
                      FROM aggregates
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'executive_summary', 'Executive Summary', 100,
                           'NET_PROFIT_MARGIN', 'Net profit margin',
                           'percent', '(Net profit / revenue) * 100, zero when revenue is zero',
                           'profit_loss', profit_loss_line_count,
                           0::numeric,
                           CASE WHEN revenue = 0 THEN 0 ELSE round((net_profit / revenue * 100)::numeric, 4)::double precision END
                      FROM aggregates
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'executive_summary', 'Executive Summary', 110,
                           'RETURN_ON_INVESTMENT', 'Return on investments',
                           'percent', '(Net profit / current assets) * 100, zero when current assets are zero',
                           'current_assets_liabilities', current_line_count,
                           0::numeric,
                           CASE WHEN current_assets = 0 THEN 0 ELSE round((net_profit / current_assets * 100)::numeric, 4)::double precision END
                      FROM aggregates
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'executive_summary', 'Executive Summary', 120,
                           'AVERAGE_DEBTORS_DAYS', 'Average debtors days',
                           'days', '(Receivables / revenue) * days in source period, zero when revenue is zero',
                           'receivable', receivable_line_count,
                           0::numeric,
                           CASE WHEN revenue = 0 THEN 0 ELSE round((receivables / revenue * day_count)::numeric, 4)::double precision END
                      FROM aggregates
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'executive_summary', 'Executive Summary', 130,
                           'AVERAGE_CREDITORS_DAYS', 'Average creditors days',
                           'days', '(Payables / (cost of revenue + expenses)) * days in source period, zero when denominator is zero',
                           'payable', payable_line_count,
                           0::numeric,
                           CASE WHEN cost_of_revenue + expenses = 0 THEN 0 ELSE round((payables / (cost_of_revenue + expenses) * day_count)::numeric, 4)::double precision END
                      FROM aggregates
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'executive_summary', 'Executive Summary', 140,
                           'SHORT_TERM_CASH_FORECAST', 'Short term cash forecast',
                           'currency', 'Receivables less payables',
                           'current_assets_liabilities', current_line_count,
                           receivables - payables, (receivables - payables)::double precision
                      FROM aggregates
                    UNION ALL
                    SELECT company_id, source_company_id, company_currency_id, period_key,
                           'executive_summary', 'Executive Summary', 150,
                           'CURRENT_ASSETS_TO_LIABILITIES', 'Current assets to liabilities',
                           'ratio', 'Current assets / current liabilities, zero when current liabilities are zero',
                           'current_assets_liabilities', current_line_count,
                           0::numeric,
                           CASE WHEN current_liabilities = 0 THEN 0 ELSE round((current_assets / current_liabilities)::numeric, 4)::double precision END
                      FROM aggregates
                )
                SELECT row_number() OVER (
                           ORDER BY company_id, period_key, report_key, line_sequence
                       )::integer AS id,
                       line_sources.*
                  FROM line_sources
            )
            """,
        )


class RebuildAccountRevenueSpendingMonth(models.Model):
    _name = "rebuild.account.revenue.spending.month"
    _description = "Monthly Revenue, Spending and Net Contribution"
    _auto = False
    _order = "company_id, month, account_id, partner_id, move_line_id"

    company_id = fields.Many2one("res.company", readonly=True)
    company_currency_id = fields.Many2one("res.currency", readonly=True)
    month = fields.Date(readonly=True)
    move_line_id = fields.Many2one("account.move.line", readonly=True)
    account_id = fields.Many2one("account.account", readonly=True)
    partner_id = fields.Many2one("res.partner", readonly=True)
    analytic_plan_ids = fields.Many2many(
        "account.analytic.plan",
        "rebuild_revenue_spending_analytic_plan_rel",
        "report_id",
        "analytic_plan_id",
        readonly=True,
    )
    analytic_account_ids = fields.Many2many(
        "account.analytic.account",
        "rebuild_revenue_spending_analytic_account_rel",
        "report_id",
        "analytic_account_id",
        readonly=True,
    )
    is_current_fiscal_year = fields.Boolean(
        compute="_compute_is_current_fiscal_year",
        search="_search_is_current_fiscal_year",
    )
    line_count = fields.Integer(readonly=True)
    revenue = fields.Monetary(
        currency_field="company_currency_id",
        readonly=True,
    )
    spending = fields.Monetary(
        currency_field="company_currency_id",
        readonly=True,
    )
    net_contribution = fields.Monetary(
        currency_field="company_currency_id",
        readonly=True,
    )

    @api.model
    def _current_fiscal_year_bounds(self, company):
        today = fields.Date.context_today(self.with_company(company))
        return date_utils.get_fiscal_year(
            today,
            day=company.fiscalyear_last_day,
            month=int(company.fiscalyear_last_month),
        )

    def _compute_is_current_fiscal_year(self):
        bounds_by_company = {
            company.id: self._current_fiscal_year_bounds(company)
            for company in self.mapped("company_id")
        }
        for row in self:
            fiscal_from, fiscal_to = bounds_by_company[row.company_id.id]
            row.is_current_fiscal_year = (
                fiscal_from <= row.month <= fiscal_to
            )

    @api.model
    def _search_is_current_fiscal_year(self, operator, value):
        if operator in ("in", "not in"):
            requested = True in value
            positive = requested if operator == "in" else not requested
        elif operator in ("=", "==", "!="):
            requested = bool(value)
            positive = requested if operator in ("=", "==") else not requested
        else:
            return NotImplemented
        company_domains = []
        for company in self.env.companies:
            fiscal_from, fiscal_to = self._current_fiscal_year_bounds(company)
            company_domains.append(fields.Domain.AND([
                fields.Domain("company_id", "=", company.id),
                fields.Domain(
                    "month",
                    ">=",
                    fields.Date.to_string(fiscal_from),
                ),
                fields.Domain(
                    "month",
                    "<=",
                    fields.Date.to_string(fiscal_to),
                ),
            ]))
        domain = fields.Domain.OR(company_domains)
        return list(domain if positive else ~domain)

    def action_open_journal_items(self):
        self.ensure_one()
        domain = [
            ("company_id", "=", self.company_id.id),
            ("move_id.state", "=", "posted"),
            (
                "move_id.date",
                ">=",
                fields.Date.to_string(self.month),
            ),
            (
                "move_id.date",
                "<=",
                fields.Date.to_string(
                    fields.Date.end_of(self.month, "month"),
                ),
            ),
        ]
        domain.append((
            "account_id.account_type",
            "in",
            [
                "income",
                "income_other",
                "expense",
                "expense_other",
                "expense_direct_cost",
                "expense_depreciation",
            ],
        ))
        if self.account_id:
            domain.append(("account_id", "=", self.account_id.id))
        if self.partner_id:
            domain.append(("partner_id", "=", self.partner_id.id))
        domain.append(("id", "=", self.move_line_id.id))
        return _journal_items_action(
            self,
            domain,
            name=f"Revenue and Spending - {fields.Date.to_string(self.month)}",
        )

    def init(self):
        analytic_plan_rel = "rebuild_revenue_spending_analytic_plan_rel"
        analytic_account_rel = "rebuild_revenue_spending_analytic_account_rel"
        tools.drop_view_if_exists(self.env.cr, analytic_plan_rel)
        tools.drop_view_if_exists(self.env.cr, analytic_account_rel)
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT line.id,
                       line.company_id,
                       company.currency_id AS company_currency_id,
                       date_trunc('month', move.date)::date AS month,
                       line.id AS move_line_id,
                       line.account_id,
                       line.partner_id,
                       1::integer AS line_count,
                       round(CASE
                           WHEN account.account_type IN ('income', 'income_other')
                           THEN -line.balance
                           ELSE 0
                       END::numeric, 2) AS revenue,
                       round(CASE
                           WHEN account.account_type IN (
                               'expense',
                               'expense_other',
                               'expense_direct_cost',
                               'expense_depreciation'
                           )
                           THEN line.balance
                           ELSE 0
                       END::numeric, 2) AS spending,
                       round(-line.balance::numeric, 2) AS net_contribution
                      FROM account_move_line line
                      JOIN account_move move ON move.id = line.move_id
                      JOIN account_account account ON account.id = line.account_id
                      JOIN res_company company ON company.id = line.company_id
                     WHERE move.state = 'posted'
                       AND account.account_type IN (
                           'income',
                           'income_other',
                           'expense',
                           'expense_other',
                           'expense_direct_cost',
                           'expense_depreciation'
                       )
            )
            """,
        )
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {analytic_account_rel} AS (
                SELECT DISTINCT line.id AS report_id,
                       account_key::integer AS analytic_account_id
                  FROM account_move_line line
                  CROSS JOIN LATERAL jsonb_object_keys(
                      COALESCE(line.analytic_distribution, '{{}}'::jsonb)
                  ) AS distribution_key
                  CROSS JOIN LATERAL regexp_split_to_table(
                      distribution_key,
                      ','
                  ) AS account_key
                 WHERE line.id IN (SELECT id FROM {self._table})
            )
            """,
        )
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {analytic_plan_rel} AS (
                SELECT DISTINCT relation.report_id,
                       analytic_account.plan_id AS analytic_plan_id
                  FROM {analytic_account_rel} relation
                  JOIN account_analytic_account analytic_account
                    ON analytic_account.id = relation.analytic_account_id
            )
            """,
        )


class RebuildAccountAnalyticDistributionLine(models.Model):
    _name = "rebuild.account.analytic.distribution.line"
    _description = "USL Imported Analytic Distribution Line"
    _auto = False
    _order = "company_id, period_key, analytic_key, account_code"

    company_id = fields.Many2one("res.company", readonly=True)
    source_company_id = fields.Integer(readonly=True)
    company_currency_id = fields.Many2one("res.currency", readonly=True)
    period_key = fields.Char(readonly=True)
    analytic_key = fields.Char(readonly=True)
    analytic_account_id = fields.Many2one("account.analytic.account", readonly=True)
    analytic_code = fields.Char(readonly=True)
    analytic_name = fields.Char(readonly=True)
    account_id = fields.Many2one("account.account", readonly=True)
    account_code = fields.Char(readonly=True)
    account_name = fields.Char(readonly=True)
    move_line_count = fields.Integer(readonly=True)
    percentage = fields.Float(readonly=True)
    allocated_debit = fields.Monetary(currency_field="company_currency_id", readonly=True)
    allocated_credit = fields.Monetary(currency_field="company_currency_id", readonly=True)
    allocated_balance = fields.Monetary(currency_field="company_currency_id", readonly=True)

    def action_open_journal_items(self):
        self.ensure_one()
        domain = [
            ("company_id", "=", self.company_id.id),
            ("rebuild_source_model", "=", "account.analytic.line"),
            *_analytic_line_period_domain(self),
        ]
        if self.analytic_account_id:
            domain.append(("rebuild_analytic_account_id", "=", self.analytic_account_id.id))
        if self.account_id:
            domain.append(("general_account_id", "=", self.account_id.id))
        return _analytic_lines_action(
            self,
            domain,
            name=f"Analytic Lines - {self.analytic_name or self.analytic_key}",
        )

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                WITH analytic_line_groups AS (
                    SELECT analytic.company_id,
                           company.rebuild_source_id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           {ANALYTIC_PERIOD_CASE_SQL} AS period_key,
                           COALESCE(
                               analytic_account.rebuild_source_id::text,
                               analytic.rebuild_source_analytic_account_id::text,
                               analytic_account.id::text,
                               ''
                           ) AS analytic_key,
                           analytic_account.id AS analytic_account_id,
                           COALESCE(analytic_account.code::text, '') AS analytic_code,
                           COALESCE(analytic_account.name->>'fr_FR', analytic_account.name->>'en_US', analytic_account.name::text, analytic.name::text) AS analytic_name,
                           account.id AS account_id,
                           COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text) AS account_code,
                           COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text) AS account_name,
                           count(analytic.id)::integer AS move_line_count,
                           100.0::double precision AS percentage,
                           round(sum(CASE WHEN analytic.amount > 0 THEN analytic.amount ELSE 0 END)::numeric, 2) AS allocated_debit,
                           round(sum(CASE WHEN analytic.amount < 0 THEN -analytic.amount ELSE 0 END)::numeric, 2) AS allocated_credit,
                           round(sum(analytic.amount)::numeric, 2) AS allocated_balance
                      FROM account_analytic_line analytic
                      JOIN res_company company ON company.id = analytic.company_id
                      LEFT JOIN account_analytic_account analytic_account ON analytic_account.id = analytic.rebuild_analytic_account_id
                      LEFT JOIN account_account account ON account.id = analytic.general_account_id
                     WHERE analytic.rebuild_source_model = 'account.analytic.line'
                     GROUP BY analytic.company_id,
                              company.rebuild_source_id,
                              company.currency_id,
                              {ANALYTIC_PERIOD_CASE_SQL},
                              COALESCE(
                                  analytic_account.rebuild_source_id::text,
                                  analytic.rebuild_source_analytic_account_id::text,
                                  analytic_account.id::text,
                                  ''
                              ),
                              analytic_account.id,
                              analytic_account.code,
                              COALESCE(analytic_account.name->>'fr_FR', analytic_account.name->>'en_US', analytic_account.name::text, analytic.name::text),
                              account.id,
                              COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text),
                              COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text)
                )
                SELECT row_number() OVER (
                           ORDER BY analytic_line_groups.company_id, analytic_line_groups.period_key, analytic_line_groups.analytic_key, analytic_line_groups.account_code
                       )::integer AS id,
                       analytic_line_groups.*
                  FROM analytic_line_groups
            )
            """,
        )


class RebuildAccountFrenchStatementLine(models.Model):
    _name = "rebuild.account.french.statement.line"
    _description = "USL Imported French Annual Statement Line"
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
                           company.rebuild_source_id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           {PERIOD_CASE_SQL} AS period_key,
                           COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text) AS account_code,
                           account.account_type,
                           count(line.id) AS move_line_count,
                           round(sum(line.balance)::numeric, 2) AS balance
                      FROM account_move_line line
                      JOIN account_move move ON move.id = line.move_id
                      JOIN res_company company ON company.id = line.company_id
                      JOIN account_account account ON account.id = line.account_id
                     WHERE line.rebuild_source_model = 'account.move.line'
                       AND move.rebuild_source_model = 'account.move'
                       AND move.state = 'posted'
                     GROUP BY line.company_id,
                              company.rebuild_source_id,
                              company.currency_id,
                              {PERIOD_CASE_SQL},
                              COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text),
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
    _description = "USL Imported French Tax Package Mapping Line"
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
            raise UserError("No imported accounts currently match this tax-package line's drill-down prefixes.")
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
                           company.rebuild_source_id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           {PERIOD_CASE_SQL} AS period_key,
                           COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text) AS account_code,
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
                       AND line.rebuild_source_model = 'account.move.line'
                       AND lower(COALESCE(line.name, '')) = 'ca12'
                       AND COALESCE(account.code_store->>company.rebuild_source_id::text, account.code_store->>'1', account.code_store::text) LIKE '445%'
                     GROUP BY line.company_id,
                              company.rebuild_source_id,
                              company.currency_id,
                              period_key,
                              account_code
                ),
                assets AS (
                    SELECT asset.company_id,
                           company.rebuild_source_id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           '{BENCHMARK_PERIOD_KEY}'::text AS period_key,
                           count(asset.id)::integer AS asset_count,
                           round(sum(asset.original_value)::numeric, 2) AS original_value,
                           round(sum(asset.already_depreciated_amount_import)::numeric, 2) AS accumulated_depreciation,
                           round(sum(asset.imported_period_net_value)::numeric, 2) AS net_value
                      FROM rebuild_account_asset asset
                      JOIN res_company company ON company.id = asset.company_id
                     WHERE asset.rebuild_source_model = 'account_asset'
                     GROUP BY asset.company_id,
                              company.rebuild_source_id,
                              company.currency_id
                ),
                schedule AS (
                    SELECT schedule.company_id,
                           company.rebuild_source_id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           '{BENCHMARK_PERIOD_KEY}'::text AS period_key,
                           count(schedule.id)::integer AS schedule_line_count,
                           round(sum(schedule.depreciation_amount)::numeric, 2) AS depreciation_schedule_total
                      FROM rebuild_account_asset_depreciation_schedule_line schedule
                      JOIN res_company company ON company.id = schedule.company_id
                     GROUP BY schedule.company_id,
                              company.rebuild_source_id,
                              company.currency_id
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
