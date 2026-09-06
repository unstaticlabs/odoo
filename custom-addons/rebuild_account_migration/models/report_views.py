from odoo import api, fields, models, tools

FIRST_FISCAL_YEAR_PERIOD_KEY = "Fiscal year 2024-01-10 to 2025-09-30"
CURRENT_PERIOD_KEY = "Fiscal year from 2025-10-01"
USL_MEDIA_PERIOD_KEY = "All posted accounting"

PERIOD_CASE_SQL = """
    CASE
        WHEN move.date BETWEEN DATE '2024-01-10' AND DATE '2025-09-30'
        THEN 'Fiscal year 2024-01-10 to 2025-09-30'
        WHEN move.date >= DATE '2025-10-01'
        THEN 'Fiscal year from 2025-10-01'
        ELSE 'Earlier posted accounting'
    END
"""

ANALYTIC_PERIOD_CASE_SQL = """
    CASE
        WHEN analytic.date BETWEEN DATE '2024-01-10' AND DATE '2025-09-30'
        THEN 'Fiscal year 2024-01-10 to 2025-09-30'
        WHEN analytic.date >= DATE '2025-10-01'
        THEN 'Fiscal year from 2025-10-01'
        ELSE 'Earlier analytic accounting'
    END
"""


def _period_domain(record):
    if record.period_key == FIRST_FISCAL_YEAR_PERIOD_KEY:
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
        ("move_id.state", "=", "posted"),
        *_period_domain(record),
    ]


def _analytic_line_period_domain(record):
    if record.period_key == FIRST_FISCAL_YEAR_PERIOD_KEY:
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
        name="Journal Item",
    )


def _prefix_domain(field_name, prefixes):
    return fields.Domain.OR(
        fields.Domain(field_name, "=like", f"{prefix}%")
        for prefix in prefixes
    )


class RebuildAccountFinancialStatementLine(models.Model):
    _name = "rebuild.account.financial.statement.line"
    _description = "USL Financial Statement Line"
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
                           company.id AS source_company_id,
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
                           COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text) AS account_code,
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
                 WHERE move.state = 'posted'
                     GROUP BY line.company_id,
                              company.id,
                              company.currency_id,
                              {PERIOD_CASE_SQL},
                              account.id,
                              COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text),
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
    _description = "USL VAT and Tax Report Line"
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
                           company.id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           {PERIOD_CASE_SQL} AS period_key,
                           'Tax grid tags' AS report_section,
                           tag.id AS tax_tag_id,
                           tag.id AS source_tax_tag_id,
                           COALESCE(tag.name->>'fr_FR', tag.name->>'en_US', tag.name::text) AS tax_tag_name,
                           account.id AS account_id,
                           COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text) AS account_code,
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
                 WHERE move.state = 'posted'
                     GROUP BY line.company_id,
                              company.id,
                              company.currency_id,
                              {PERIOD_CASE_SQL},
                              tag.id,
                              tag.id,
                              COALESCE(tag.name->>'fr_FR', tag.name->>'en_US', tag.name::text),
                              account.id,
                              COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text),
                              COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text)
                ),
                vat_account_lines AS (
                    SELECT line.company_id,
                           company.id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           {PERIOD_CASE_SQL} AS period_key,
                           'VAT accounts' AS report_section,
                           NULL::integer AS tax_tag_id,
                           NULL::integer AS source_tax_tag_id,
                           NULL::text AS tax_tag_name,
                           account.id AS account_id,
                           COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text) AS account_code,
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
                 WHERE move.state = 'posted'
                       AND COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text) LIKE '445%'
                     GROUP BY line.company_id,
                              company.id,
                              company.currency_id,
                              {PERIOD_CASE_SQL},
                              account.id,
                              COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text),
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
    _description = "USL EC/OSS Tax Review Line"
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
                           company.id AS source_company_id,
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
                           COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text) AS account_code,
                           COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text) AS account_name,
                           account.account_type,
                           tax.id AS tax_id,
                           tax.id AS source_tax_id,
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
                 WHERE move.state = 'posted'
                ),
                tag_rel_lines AS (
                    SELECT line.id AS move_line_id,
                           line.company_id,
                           company.id AS source_company_id,
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
                           COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text) AS account_code,
                           COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text) AS account_name,
                           account.account_type,
                           NULL::integer AS tax_id,
                           NULL::integer AS source_tax_id,
                           NULL::text AS tax_name,
                           tag.id AS tax_tag_id,
                           tag.id AS source_tax_tag_id,
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
                 WHERE move.state = 'posted'
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
    _description = "USL Bank Reconciliation Line"
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
                       company.id AS source_company_id,
                       company.currency_id AS company_currency_id,
                       {PERIOD_CASE_SQL} AS period_key,
                       move.date,
                       journal.id AS journal_id,
                       journal.code AS journal_code,
                       bsl.id AS statement_line_id,
                       bsl.id AS source_statement_line_id,
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
                 WHERE move.state = 'posted'
                 GROUP BY bsl.id,
                          bsl.company_id,
                          company.id,
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
    _description = "USL Currency Gain, Loss and Exposure Line"
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
                           company.id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           {PERIOD_CASE_SQL} AS period_key,
                           line.currency_id,
                           account.id AS account_id,
                           COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text) AS account_code,
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
                 WHERE move.state = 'posted'
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
    _description = "USL Cash Flow and Executive Summary Line"
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
                           company.id AS source_company_id,
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
                 WHERE move.state = 'posted'
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
        return company.rebuild_compute_fiscalyear_dates(
            today,
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
    _description = "USL Analytic Distribution Line"
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
            *_analytic_line_period_domain(self),
        ]
        if self.analytic_account_id:
            domain.append(("account_id", "=", self.analytic_account_id.id))
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
                           company.id AS source_company_id,
                           company.currency_id AS company_currency_id,
                           {ANALYTIC_PERIOD_CASE_SQL} AS period_key,
                           COALESCE(
                               analytic_account.id::text,
                               analytic.account_id::text,
                               analytic_account.id::text,
                               ''
                           ) AS analytic_key,
                           analytic_account.id AS analytic_account_id,
                           COALESCE(analytic_account.code::text, '') AS analytic_code,
                           COALESCE(analytic_account.name->>'fr_FR', analytic_account.name->>'en_US', analytic_account.name::text, analytic.name::text) AS analytic_name,
                           account.id AS account_id,
                           COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text) AS account_code,
                           COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text) AS account_name,
                           count(analytic.id)::integer AS move_line_count,
                           100.0::double precision AS percentage,
                           round(sum(CASE WHEN analytic.amount > 0 THEN analytic.amount ELSE 0 END)::numeric, 2) AS allocated_debit,
                           round(sum(CASE WHEN analytic.amount < 0 THEN -analytic.amount ELSE 0 END)::numeric, 2) AS allocated_credit,
                           round(sum(analytic.amount)::numeric, 2) AS allocated_balance
                      FROM account_analytic_line analytic
                      JOIN res_company company ON company.id = analytic.company_id
                      LEFT JOIN account_analytic_account analytic_account ON analytic_account.id = analytic.account_id
                      LEFT JOIN account_account account ON account.id = analytic.general_account_id
                     GROUP BY analytic.company_id,
                              company.id,
                              company.currency_id,
                              {ANALYTIC_PERIOD_CASE_SQL},
                              COALESCE(
                                  analytic_account.id::text,
                                  analytic.account_id::text,
                                  analytic_account.id::text,
                                  ''
                              ),
                              analytic_account.id,
                              analytic_account.code,
                              COALESCE(analytic_account.name->>'fr_FR', analytic_account.name->>'en_US', analytic_account.name::text, analytic.name::text),
                              account.id,
                              COALESCE(account.code_store->>company.id::text, account.code_store->>'1', account.code_store::text),
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
