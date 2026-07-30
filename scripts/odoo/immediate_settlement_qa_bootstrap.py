"""Prepare a disposable Exact Foreign-Amount Settlement QA database."""

import logging
import os
from datetime import timedelta

from odoo import Command, fields

_logger = logging.getLogger(__name__)

TRUTHY_VALUES = {"1", "true", "yes", "on"}
DEFAULT_QA_DATABASE = "odoo_immediate_settlement_qa"
MISSING_OPT_IN = "Set USL_IMMEDIATE_SETTLEMENT_QA_BOOTSTRAP=1 explicitly."
LIVE_GUARD_ENABLED = "QA bootstrap refuses to run with a live guard enabled."
UNSAFE_DATABASE = "QA bootstrap refuses to run outside its named disposable database."
EXISTING_ACCOUNTING = (
    "QA bootstrap cannot change the company currency after accounting entries exist."
)
MISSING_CHART = "The French chart could not provide the QA accounting records."
INVALID_QA_FACTS = "The generated exact-settlement QA facts are inconsistent."


def _is_enabled(name):
    return os.getenv(name, "").strip().lower() in TRUTHY_VALUES


def _ensure_user(env, *, login, name, password, groups, company):
    user = env["res.users"].sudo().search([("login", "=", login)], limit=1)
    values = {
        "name": name,
        "login": login,
        "password": password,
        "email": f"{login}@example.invalid",
        "company_id": company.id,
        "company_ids": [Command.set(company.ids)],
        "group_ids": [Command.set(groups.ids)],
        "active": True,
    }
    if user:
        user.write(values)
    else:
        user = (
            env["res.users"]
            .with_context(no_reset_password=True)
            .sudo()
            .create(values)
        )
    return user


def _ensure_rate(env, currency, company, rate_date, rate):
    currency_rate = env["res.currency.rate"].sudo().search(
        [
            ("currency_id", "=", currency.id),
            ("company_id", "=", company.id),
            ("name", "=", rate_date),
        ],
        limit=1,
    )
    values = {
        "currency_id": currency.id,
        "company_id": company.id,
        "name": rate_date,
        "rate": rate,
    }
    if currency_rate:
        currency_rate.write(values)
    else:
        env["res.currency.rate"].sudo().create(values)


def _ensure_partner(env, reference, name):
    partner = env["res.partner"].sudo().search(
        [("ref", "=", reference)],
        limit=1,
    )
    values = {
        "name": name,
        "ref": reference,
        "supplier_rank": 1,
    }
    if partner:
        partner.write(values)
    else:
        partner = env["res.partner"].sudo().create(values)
    return partner


def _ensure_case(
    env,
    *,
    company,
    currency,
    purchase_journal,
    bank_journal,
    expense_account,
    reference,
    partner_name,
    document_date,
    statement_date,
):
    statement_reference = f"SHINE CARD {reference.rsplit('-', 1)[-1]} 4.40"
    bill = env["account.move"].sudo().search(
        [
            ("company_id", "=", company.id),
            ("move_type", "=", "in_invoice"),
            ("ref", "=", reference),
        ],
        limit=1,
    )
    partner = _ensure_partner(env, reference, partner_name)
    if not bill:
        bill = (
            env["account.move"]
            .sudo()
            .with_company(company)
            .create(
                {
                    "move_type": "in_invoice",
                    "company_id": company.id,
                    "partner_id": partner.id,
                    "currency_id": currency.id,
                    "journal_id": purchase_journal.id,
                    "invoice_date": document_date,
                    "date": document_date,
                    "ref": reference,
                    "invoice_line_ids": [
                        Command.create(
                            {
                                "name": "Cloudflare service",
                                "account_id": expense_account.id,
                                "quantity": 1.0,
                                "price_unit": 5.0,
                                "tax_ids": [Command.clear()],
                            },
                        ),
                    ],
                },
            )
        )
        bill.action_post()

    statement_line = env["account.bank.statement.line"].sudo().search(
        [
            ("company_id", "=", company.id),
            ("journal_id", "=", bank_journal.id),
            ("payment_ref", "=", statement_reference),
        ],
        limit=1,
    )
    if not statement_line:
        statement_line = (
            env["account.bank.statement.line"]
            .sudo()
            .with_company(company)
            .create(
                {
                    "journal_id": bank_journal.id,
                    "date": statement_date,
                    "payment_ref": statement_reference,
                    "amount": -4.40,
                    "partner_id": partner.id,
                },
            )
        )
    return bill, statement_line


def bootstrap(env):
    if not _is_enabled("USL_IMMEDIATE_SETTLEMENT_QA_BOOTSTRAP"):
        raise RuntimeError(MISSING_OPT_IN)
    if (
        _is_enabled("USL_EINVOICE_LIVE_ENABLED")
        or _is_enabled("USL_EREPORTING_LIVE_ENABLED")
    ):
        raise RuntimeError(LIVE_GUARD_ENABLED)

    expected_database = os.getenv(
        "ODOO_IMMEDIATE_SETTLEMENT_QA_DATABASE",
        DEFAULT_QA_DATABASE,
    )
    if (
        env.cr.dbname != expected_database
        or expected_database == "odoo_dev"
        or not expected_database.endswith("_qa")
    ):
        raise RuntimeError(UNSAFE_DATABASE)

    company = env.company.sudo()
    eur = env.ref("base.EUR")
    eur.active = True
    if company.currency_id != eur:
        posted_moves = env["account.move"].sudo().search_count(
            [
                ("company_id", "=", company.id),
                ("state", "=", "posted"),
            ],
        )
        if posted_moves:
            raise RuntimeError(EXISTING_ACCOUNTING)
        company.currency_id = eur

    france = env.ref("base.fr")
    company.write(
        {
            "name": "Exact Settlement QA",
            "country_id": france.id,
            "account_fiscal_country_id": france.id,
            "currency_id": eur.id,
            "immediate_settlement_max_days": 3,
            "immediate_settlement_max_rate_deviation": 3.0,
            "restrictive_audit_trail": False,
        },
    )
    if company.chart_template != "fr_comp":
        env["account.chart.template"].try_loading(
            "fr_comp",
            company=company,
            install_demo=False,
        )

    purchase_journal = env["account.journal"].sudo().search(
        [
            ("company_id", "=", company.id),
            ("type", "=", "purchase"),
        ],
        limit=1,
    )
    bank_journal = env["account.journal"].sudo().search(
        [
            ("company_id", "=", company.id),
            ("type", "=", "bank"),
        ],
        limit=1,
    )
    expense_account = env["account.account"].sudo().search(
        [
            ("company_ids", "in", company.id),
            ("account_type", "=", "expense"),
            ("active", "=", True),
        ],
        order="code",
        limit=1,
    )
    if (
        not purchase_journal
        or not bank_journal
        or not bank_journal.suspense_account_id
        or not expense_account
        or not company.currency_exchange_journal_id
        or not company.expense_currency_exchange_account_id
        or not company.income_currency_exchange_account_id
    ):
        raise RuntimeError(MISSING_CHART)

    bank_journal.write({"reconcile_mode": "edit"})
    bank_journal.suspense_account_id.reconcile = True

    usd = env.ref("base.USD")
    usd.active = True
    document_date = fields.Date.today() - timedelta(days=2)
    statement_date = document_date + timedelta(days=1)
    delayed_document_date = fields.Date.today() - timedelta(days=12)
    _ensure_rate(
        env,
        usd,
        company,
        delayed_document_date - timedelta(days=1),
        5.0 / 4.38,
    )
    _ensure_rate(
        env,
        usd,
        company,
        document_date - timedelta(days=1),
        5.0 / 4.38,
    )
    _ensure_rate(
        env,
        usd,
        company,
        document_date,
        5.03 / 4.40,
    )

    settle_bill, settle_statement = _ensure_case(
        env,
        company=company,
        currency=usd,
        purchase_journal=purchase_journal,
        bank_journal=bank_journal,
        expense_account=expense_account,
        reference="QA-IMS-CLOUDFLARE-SETTLE",
        partner_name="Cloudflare · Settle demo",
        document_date=document_date,
        statement_date=statement_date,
    )
    add_bill, add_statement = _ensure_case(
        env,
        company=company,
        currency=usd,
        purchase_journal=purchase_journal,
        bank_journal=bank_journal,
        expense_account=expense_account,
        reference="QA-IMS-CLOUDFLARE-ADD",
        partner_name="Cloudflare · Add comparison",
        document_date=document_date,
        statement_date=statement_date,
    )
    delayed_bill, delayed_statement = _ensure_case(
        env,
        company=company,
        currency=usd,
        purchase_journal=purchase_journal,
        bank_journal=bank_journal,
        expense_account=expense_account,
        reference="QA-IMS-DELAYED-BLOCKER",
        partner_name="Cloudflare · Delayed blocker",
        document_date=delayed_document_date,
        statement_date=statement_date,
    )

    base_user = env.ref("base.group_user")
    accountant = _ensure_user(
        env,
        login="qa.settlement",
        name="QA Settlement Accountant",
        password="qa-settlement",
        groups=base_user | env.ref("account.group_account_manager"),
        company=company,
    )
    _ensure_user(
        env,
        login="qa.viewer",
        name="QA Settlement Viewer",
        password="qa-viewer",
        groups=base_user | env.ref("account.group_account_readonly"),
        company=company,
    )

    settle_term = settle_bill.line_ids.filtered(
        lambda line: line.display_type == "payment_term" and not line.reconciled,
    )
    _liquidity, settle_source, _other = settle_statement._seek_for_lines()
    settle_eligibility = settle_bill.with_user(
        accountant,
    )._get_immediate_settlement_eligibility(
        settle_source.with_user(accountant),
    )
    _liquidity, delayed_source, _other = delayed_statement._seek_for_lines()
    delayed_eligibility = delayed_bill.with_user(
        accountant,
    )._get_immediate_settlement_eligibility(
        delayed_source.with_user(accountant),
    )
    _logger.info(
        "QA facts: terms=%s foreign=%s company=%s bank=%s bank_currency=%s "
        "bank_foreign=%s eligible=%s reason=%s synthetic=%s difference=%s "
        "delayed_eligible=%s delayed_reason=%s",
        len(settle_term),
        abs(settle_term.amount_residual_currency),
        abs(settle_term.amount_residual),
        abs(settle_statement.amount),
        settle_statement.foreign_currency_id.display_name,
        settle_statement.amount_currency,
        settle_eligibility["eligible"],
        settle_eligibility["reason"],
        settle_eligibility.get("synthetic_foreign_amount"),
        settle_eligibility.get("settlement_difference"),
        delayed_eligibility["eligible"],
        delayed_eligibility["reason"],
    )
    if (
        len(settle_term) != 1
        or not usd.is_zero(abs(settle_term.amount_residual_currency) - 5.0)
        or not eur.is_zero(abs(settle_term.amount_residual) - 4.38)
        or not eur.is_zero(abs(settle_statement.amount) - 4.40)
        or settle_statement.foreign_currency_id
        or settle_statement.amount_currency
        or not settle_eligibility["eligible"]
        or not usd.is_zero(
            settle_eligibility["synthetic_foreign_amount"] - 5.03,
        )
        or not eur.is_zero(settle_eligibility["settlement_difference"] - 0.02)
        or delayed_eligibility["eligible"]
    ):
        raise RuntimeError(INVALID_QA_FACTS)

    env.cr.commit()
    _logger.info("Exact Foreign-Amount Settlement QA is ready.")
    _logger.info(
        "Settle case: %s %s %s",
        settle_bill.display_name,
        settle_bill.id,
        settle_statement.id,
    )
    _logger.info(
        "Add case: %s %s %s",
        add_bill.display_name,
        add_bill.id,
        add_statement.id,
    )
    _logger.info(
        "Delayed blocker: %s %s %s",
        delayed_bill.display_name,
        delayed_bill.id,
        delayed_statement.id,
    )


bootstrap(globals()["env"])
