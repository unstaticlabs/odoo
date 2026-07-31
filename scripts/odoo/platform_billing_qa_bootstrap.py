# ruff: noqa: F821, T201
"""Prepare deterministic Platform Billing records in the isolated QA database."""

import os

from odoo import Command, fields

TRUTHY_VALUES = {"1", "true", "yes", "on"}
MISSING_OPT_IN = "Set USL_PLATFORM_BILLING_QA_BOOTSTRAP=1 explicitly."
LIVE_GUARD_ENABLED = "QA bootstrap refuses to run with a live guard enabled."
MISSING_PLATFORM = "Create one valid Platform Billing configuration first."
MISSING_ACCOUNTS = "The QA company needs active income and expense accounts."
NO_FREE_BATCH = "No free pooled QA demo batch remains."
DEMO_PREFIX = "QA DEMO"


def _is_enabled(name):
    return os.getenv(name, "").strip().lower() in TRUTHY_VALUES


def _user(env, *, login, name, password, groups, company):
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


def _session(env, platform, *, name, period, reference, amount=80.0):
    Session = env["usl.platform.billing.session"].sudo()
    session = Session.search(
        [
            ("company_id", "=", platform.company_id.id),
            ("name", "=", name),
        ],
        limit=1,
    )
    if session:
        return session
    period_month = fields.Date.from_string(period)
    invoice_date = fields.Date.end_of(period_month, "month")
    session = Session.create(
        {
            "name": name,
            "company_id": platform.company_id.id,
            "period_month": period_month,
            "invoice_date": invoice_date,
            "due_date": invoice_date,
            "bank_currency_id": platform.company_id.currency_id.id,
        },
    )
    env["usl.platform.billing.payout"].sudo().create(
        {
            "session_id": session.id,
            "platform_id": platform.id,
            "payout_date": invoice_date,
            "platform_reference": reference,
            "net_platform_amount": amount,
        },
    )
    session.action_check()
    session.action_generate_documents()
    session.with_context(
        skip_platform_coverage_warning=True,
    ).action_post_documents()
    return session


def _statement_line(env, journal, *, label, date, amount):
    Line = env["account.bank.statement.line"].sudo()
    line = Line.search(
        [
            ("company_id", "=", journal.company_id.id),
            ("payment_ref", "=", label),
        ],
        limit=1,
    )
    if line:
        return line
    statement = env["account.bank.statement"].sudo().create(
        {
            "name": label,
            "journal_id": journal.id,
            "date": fields.Date.from_string(date),
        },
    )
    return Line.create(
        {
            "name": label,
            "payment_ref": label,
            "journal_id": journal.id,
            "statement_id": statement.id,
            "amount": amount,
            "date": fields.Date.from_string(date),
        },
    )


def _demo_platform(env, company):
    Platform = env["usl.platform.billing.platform"].sudo()
    platform = Platform.search(
        [
            ("company_id", "=", company.id),
            ("name", "=", f"{DEMO_PREFIX} Platform EUR"),
        ],
        limit=1,
    )
    if platform:
        return platform
    source = Platform.with_context(active_test=False).search(
        [("company_id", "=", company.id)],
        limit=1,
    )
    if not source:
        raise RuntimeError(MISSING_PLATFORM)
    income_account = env["account.account"].sudo().search(
        [
            ("company_ids", "in", company.id),
            ("account_type", "in", ("income", "income_other")),
            ("active", "=", True),
        ],
        limit=1,
    )
    expense_account = env["account.account"].sudo().search(
        [
            ("company_ids", "in", company.id),
            ("account_type", "=", "expense"),
            ("active", "=", True),
        ],
        limit=1,
    )
    if not income_account or not expense_account:
        raise RuntimeError(MISSING_ACCOUNTS)
    revenue_product = source.revenue_product_id.copy(
        {
            "name": f"{DEMO_PREFIX} Revenue",
            "taxes_id": [Command.clear()],
            "supplier_taxes_id": [Command.clear()],
            "property_account_income_id": income_account.id,
            "property_account_expense_id": expense_account.id,
        },
    )
    commission_product = source.commission_product_id.copy(
        {
            "name": f"{DEMO_PREFIX} Commission",
            "taxes_id": [Command.clear()],
            "supplier_taxes_id": [Command.clear()],
            "property_account_income_id": income_account.id,
            "property_account_expense_id": expense_account.id,
        },
    )
    partner = env["res.partner"].sudo().create(
        {
            "name": f"{DEMO_PREFIX} Content Platform",
            "company_id": company.id,
            "is_company": True,
        },
    )
    general_journal = source.compensation_journal_id or env[
        "account.journal"
    ].sudo().search(
        [("company_id", "=", company.id), ("type", "=", "general")],
        limit=1,
    )
    bank_journal = source.bank_journal_id or env["account.journal"].sudo().search(
        [("company_id", "=", company.id), ("type", "=", "bank")],
        limit=1,
    )
    return Platform.create(
        {
            "name": f"{DEMO_PREFIX} Platform EUR",
            "company_id": company.id,
            "partner_id": partner.id,
            "commission_rate": 20.0,
            "currency_id": company.currency_id.id,
            "revenue_product_id": revenue_product.id,
            "commission_product_id": commission_product.id,
            "sale_journal_id": source.sale_journal_id.id,
            "purchase_journal_id": source.purchase_journal_id.id,
            "compensation_journal_id": general_journal.id,
            "bank_journal_id": bank_journal.id,
            "bank_label_pattern": f"{DEMO_PREFIX} {{ref}}",
            "bank_label_keywords": "QA DEMO,POOLED,PARTIAL",
            "auto_create_compensation": bool(general_journal),
        },
    )


def _open_pooled_demo(env, platform):
    for batch in range(1, 10):
        suffix = "" if batch == 1 else f" — batch {batch}"
        reference_suffix = "" if batch == 1 else f"-{batch}"
        first_period = fields.Date.add(
            fields.Date.from_string("2026-08-01"),
            months=(batch - 1) * 5,
        )
        second_period = fields.Date.add(first_period, months=1)
        bank_date = fields.Date.add(second_period, months=1, days=19)
        first = _session(
            env,
            platform,
            name=f"{DEMO_PREFIX} — Pooled A{suffix} — delayed EUR 80",
            period=fields.Date.to_string(first_period),
            reference=f"QA-POOL-A{reference_suffix}",
        )
        second = _session(
            env,
            platform,
            name=f"{DEMO_PREFIX} — Pooled B{suffix} — delayed EUR 80",
            period=fields.Date.to_string(second_period),
            reference=f"QA-POOL-B{reference_suffix}",
        )
        bank_line = _statement_line(
            env,
            platform.bank_journal_id,
            label=(
                f"{DEMO_PREFIX} — Pooled receipt EUR 160 — "
                f"SELECT ME{suffix}"
            ),
            date=fields.Date.to_string(bank_date),
            amount=160.0,
        )
        if (
            first.state == "posted"
            and second.state == "posted"
            and not bank_line.is_reconciled
            and not first.payout_ids.bank_allocation_ids
            and not second.payout_ids.bank_allocation_ids
        ):
            return first, second, bank_line
    raise RuntimeError(NO_FREE_BATCH)


def bootstrap(env):
    if not _is_enabled("USL_PLATFORM_BILLING_QA_BOOTSTRAP"):
        raise RuntimeError(MISSING_OPT_IN)
    if (
        _is_enabled("USL_EINVOICE_LIVE_ENABLED")
        or _is_enabled("USL_EREPORTING_LIVE_ENABLED")
    ):
        raise RuntimeError(LIVE_GUARD_ENABLED)

    company = env.company.sudo()
    base_user = env.ref("base.group_user")
    _user(
        env,
        login="qa.platform.manager",
        name="QA Platform Billing Manager",
        password="qa-platform-manager",
        groups=base_user
        | env.ref("usl_platform_billing.group_platform_billing_manager")
        | env.ref("analytic.group_analytic_accounting"),
        company=company,
    )
    _user(
        env,
        login="qa.platform.operator",
        name="QA Platform Billing Operator",
        password="qa-platform-operator",
        groups=base_user
        | env.ref("usl_platform_billing.group_platform_billing_operator"),
        company=company,
    )
    _user(
        env,
        login="qa.platform.reviewer",
        name="QA Platform Billing Reviewer",
        password="qa-platform-reviewer",
        groups=base_user
        | env.ref("usl_platform_billing.group_platform_billing_reader"),
        company=company,
    )
    _user(
        env,
        login="qa.platform.accountant",
        name="QA Accountant Without Platform Billing",
        password="qa-platform-accountant",
        groups=base_user | env.ref("account.group_account_user"),
        company=company,
    )

    platform = _demo_platform(env, company)
    bank_journal = platform.bank_journal_id
    pooled_a, pooled_b, pooled_line = _open_pooled_demo(env, platform)
    delayed = _session(
        env,
        platform,
        name=f"{DEMO_PREFIX} — Delayed unpaid EUR 80",
        period="2027-06-01",
        reference="QA-DELAYED-80",
    )

    partial = _session(
        env,
        platform,
        name=f"{DEMO_PREFIX} — Partial receipt — EUR 30 of EUR 80",
        period="2026-10-01",
        reference="QA-PARTIAL-80",
    )
    partial_line = _statement_line(
        env,
        bank_journal,
        label=f"{DEMO_PREFIX} — Partial receipt EUR 30 — reconciled",
        date="2026-11-15",
        amount=30.0,
    )
    Allocation = env["usl.platform.billing.bank.allocation"].sudo()
    if (
        not partial_line.is_reconciled
        and not Allocation.search_count(
            [
                ("payout_id", "=", partial.payout_ids.id),
                ("bank_statement_line_id", "=", partial_line.id),
            ],
        )
    ):
        Allocation._action_create(
            {
                "payout_id": partial.payout_ids.id,
                "bank_statement_line_id": partial_line.id,
                "bank_amount": 30.0,
                "payout_amount": 30.0,
                "score": 100,
                "detection_reason": "QA demo partial receipt",
            },
        )
        partial.action_reconcile_bank()
    _statement_line(
        env,
        bank_journal,
        label=f"{DEMO_PREFIX} — Partial remainder EUR 50 — SELECT ME",
        date="2026-12-15",
        amount=50.0,
    )

    usd = env.ref("base.USD")
    usd.active = True
    fx_platform = env["usl.platform.billing.platform"].sudo().search(
        [
            ("company_id", "=", company.id),
            ("name", "=", f"{DEMO_PREFIX} Platform USD"),
        ],
        limit=1,
    )
    if not fx_platform:
        fx_platform = platform.copy(
            {
                "name": f"{DEMO_PREFIX} Platform USD",
                "currency_id": usd.id,
                "bank_label_pattern": f"{DEMO_PREFIX} FX {{ref}}",
            },
        )
    fx_session = _session(
        env,
        fx_platform,
        name=f"{DEMO_PREFIX} — FX actual EUR 72 for USD 80",
        period="2026-11-01",
        reference="QA-FX-USD-80",
    )
    fx_line = _statement_line(
        env,
        bank_journal,
        label=f"{DEMO_PREFIX} FX QA-FX-USD-80 — EUR 72 actual",
        date="2026-12-20",
        amount=72.0,
    )
    if (
        not fx_line.is_reconciled
        and not Allocation.search_count(
            [
                ("payout_id", "=", fx_session.payout_ids.id),
                ("bank_statement_line_id", "=", fx_line.id),
            ],
        )
    ):
        Allocation._action_create(
            {
                "payout_id": fx_session.payout_ids.id,
                "bank_statement_line_id": fx_line.id,
                "bank_amount": 72.0,
                "payout_amount": 80.0,
                "score": 100,
                "detection_reason": "QA demo EUR actual for USD payout",
            },
        )
        fx_session.action_reconcile_bank()

    import_session = env["usl.platform.billing.session"].sudo().search(
        [
            ("company_id", "=", company.id),
            ("name", "=", f"{DEMO_PREFIX} — Import a new payout"),
        ],
        limit=1,
    )
    if not import_session:
        import_session = env["usl.platform.billing.session"].sudo().create(
            {
                "name": f"{DEMO_PREFIX} — Import a new payout",
                "company_id": company.id,
                "period_month": fields.Date.from_string("2026-12-01"),
                "invoice_date": fields.Date.from_string("2026-12-31"),
                "due_date": fields.Date.from_string("2026-12-31"),
                "bank_currency_id": company.currency_id.id,
            },
        )
    _statement_line(
        env,
        bank_journal,
        label=f"{DEMO_PREFIX} QA-IMPORT-80 — New payout EUR 80 — SELECT ME",
        date="2026-12-20",
        amount=80.0,
    )

    env.cr.commit()
    print(
        {
            "company": company.display_name,
            "pooled_sessions": [pooled_a.display_name, pooled_b.display_name],
            "pooled_bank_transaction": pooled_line.display_name,
            "delayed_session": delayed.display_name,
            "partial_session": partial.display_name,
            "fx_session": fx_session.display_name,
            "import_session": import_session.display_name,
        },
    )


bootstrap(globals()["env"])
