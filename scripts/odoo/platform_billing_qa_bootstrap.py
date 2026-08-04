# ruff: noqa: F821, T201
"""Prepare deterministic Platform Billing records in the isolated QA database."""

import os
from collections import Counter

from odoo import Command, fields

TRUTHY_VALUES = {"1", "true", "yes", "on"}
MISSING_OPT_IN = "Set USL_PLATFORM_BILLING_QA_BOOTSTRAP=1 explicitly."
LIVE_GUARD_ENABLED = "QA bootstrap refuses to run with a live guard enabled."
MISSING_PLATFORM = "Create one valid Platform Billing configuration first."
MISSING_ACCOUNTS = "The source platform needs valid revenue and commission accounts."
MISSING_BANK_JOURNAL = "The QA company needs a valid bank journal."
NO_FREE_BATCH = "No free pooled QA demo batch remains."
NO_FREE_IMPORT_BATCH = "No free bank-import QA demo batch remains."
DEMO_PREFIX = "QA DEMO"
NAMED_MANAGER_LOGIN = "valentin"


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


def _grant_named_manager(env, manager_group):
    users = env["res.users"].sudo().with_context(active_test=False).search(
        [("login", "=", NAMED_MANAGER_LOGIN)],
    )
    if len(users) != 1 or not users.active or users.share:
        raise RuntimeError(
            f"Expected one active internal QA user with login {NAMED_MANAGER_LOGIN!r}.",
        )
    if manager_group not in users.group_ids:
        users.write({"group_ids": [Command.link(manager_group.id)]})
    return users


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


def _demo_bank_journal(env, company):
    allocations = env["usl.platform.billing.bank.allocation"].sudo().search(
        [
            ("payout_id.company_id", "=", company.id),
            ("payout_id.platform_id.name", "not ilike", f"{DEMO_PREFIX}%"),
        ],
    )
    journals = allocations.bank_statement_line_id.journal_id.filtered(
        lambda journal: (
            journal.active
            and journal.type == "bank"
            and (not journal.currency_id or journal.currency_id == company.currency_id)
            and journal.default_account_id.account_type == "asset_cash"
        ),
    )
    if journals:
        counts = Counter(
            allocation.bank_statement_line_id.journal_id.id
            for allocation in allocations
            if allocation.bank_statement_line_id.journal_id in journals
        )
        return env["account.journal"].sudo().browse(counts.most_common(1)[0][0])
    candidates = env["account.journal"].sudo().search(
        [("company_id", "=", company.id), ("type", "=", "bank")],
    ).filtered(
        lambda journal: (
            (not journal.currency_id or journal.currency_id == company.currency_id)
            and journal.default_account_id.account_type == "asset_cash"
            and (journal.default_account_id.code or "").startswith(("512", "508"))
        ),
    )
    return candidates[:1]


def _demo_platform(env, company):
    Platform = env["usl.platform.billing.platform"].sudo()
    platform = Platform.search(
        [
            ("company_id", "=", company.id),
            ("name", "=", f"{DEMO_PREFIX} Platform EUR"),
        ],
        limit=1,
    )
    source = Platform.with_context(active_test=False).search(
        [
            ("company_id", "=", company.id),
            ("name", "not ilike", f"{DEMO_PREFIX}%"),
        ],
        limit=1,
    )
    if not source:
        raise RuntimeError(MISSING_PLATFORM)
    income_account = source.revenue_product_id.product_tmpl_id.with_company(
        company,
    ).get_product_accounts()["income"]
    expense_account = source.commission_product_id.product_tmpl_id.with_company(
        company,
    ).get_product_accounts()["expense"]
    if (
        not income_account
        or income_account.account_type not in {"income", "income_other"}
        or not expense_account
        or expense_account.account_type
        not in {"expense", "expense_other", "expense_direct_cost"}
    ):
        raise RuntimeError(MISSING_ACCOUNTS)
    bank_journal = source.bank_journal_id or _demo_bank_journal(env, company)
    if not bank_journal:
        raise RuntimeError(MISSING_BANK_JOURNAL)
    if platform:
        platform.revenue_product_id.with_company(company).write(
            {
                "property_account_income_id": income_account.id,
                "taxes_id": [Command.clear()],
                "supplier_taxes_id": [Command.clear()],
            },
        )
        platform.commission_product_id.with_company(company).write(
            {
                "property_account_expense_id": expense_account.id,
                "taxes_id": [Command.clear()],
                "supplier_taxes_id": [Command.clear()],
            },
        )
        platform.write(
            {
                "bank_journal_id": bank_journal.id,
                "bank_label_pattern": f"{DEMO_PREFIX} EUR {{ref}}",
                "bank_label_keywords": "QA DEMO EUR,POOLED,PARTIAL",
            },
        )
        return platform
    revenue_template = source.revenue_product_id.product_tmpl_id.copy(
        {
            "name": f"{DEMO_PREFIX} Revenue",
            "taxes_id": [Command.clear()],
            "supplier_taxes_id": [Command.clear()],
            "property_account_income_id": income_account.id,
        },
    )
    commission_template = source.commission_product_id.product_tmpl_id.copy(
        {
            "name": f"{DEMO_PREFIX} Commission",
            "taxes_id": [Command.clear()],
            "supplier_taxes_id": [Command.clear()],
            "property_account_expense_id": expense_account.id,
        },
    )
    revenue_product = revenue_template.product_variant_id
    commission_product = commission_template.product_variant_id
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
            "bank_label_pattern": f"{DEMO_PREFIX} EUR {{ref}}",
            "bank_label_keywords": "QA DEMO EUR,POOLED,PARTIAL",
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


def _open_import_demo(env, platform):
    Session = env["usl.platform.billing.session"].sudo()
    Allocation = env["usl.platform.billing.bank.allocation"].sudo()
    for batch in range(1, 10):
        suffix = "" if batch == 1 else f" — batch {batch}"
        reference_suffix = "" if batch == 1 else f"-{batch}"
        period = fields.Date.add(
            fields.Date.from_string("2026-12-01"),
            months=batch - 1,
        )
        invoice_date = fields.Date.end_of(period, "month")
        name = f"{DEMO_PREFIX} — Import a new payout{suffix}"
        session = Session.search(
            [
                ("company_id", "=", platform.company_id.id),
                ("name", "=", name),
            ],
            limit=1,
        )
        if not session:
            session = Session.create(
                {
                    "name": name,
                    "company_id": platform.company_id.id,
                    "period_month": period,
                    "invoice_date": invoice_date,
                    "due_date": invoice_date,
                    "bank_currency_id": platform.company_id.currency_id.id,
                },
            )
        bank_line = _statement_line(
            env,
            platform.bank_journal_id,
            label=(
                f"{DEMO_PREFIX} QA-IMPORT-80{reference_suffix} — "
                f"New payout EUR 80 — SELECT ME{suffix}"
            ),
            date=fields.Date.to_string(fields.Date.add(invoice_date, days=20)),
            amount=80.0,
        )
        if (
            session.state in {"draft", "ready"}
            and not session.payout_ids
            and not bank_line.is_reconciled
            and not Allocation.search_count(
                [("bank_statement_line_id", "=", bank_line.id)],
            )
        ):
            return session, bank_line
    raise RuntimeError(NO_FREE_IMPORT_BATCH)


def _open_bank_rate_demo(env, platform):
    Session = env["usl.platform.billing.session"].sudo()
    Allocation = env["usl.platform.billing.bank.allocation"].sudo()
    for batch in range(1, 10):
        suffix = "" if batch == 1 else f" — batch {batch}"
        reference_suffix = "" if batch == 1 else f"-{batch}"
        period = fields.Date.add(
            fields.Date.from_string("2027-01-01"),
            months=batch - 1,
        )
        invoice_date = fields.Date.end_of(period, "month")
        name = f"{DEMO_PREFIX} — Effective rate USD 1000 from EUR 700{suffix}"
        session = Session.search(
            [
                ("company_id", "=", platform.company_id.id),
                ("name", "=", name),
            ],
            limit=1,
        )
        if not session:
            session = Session.create(
                {
                    "name": name,
                    "company_id": platform.company_id.id,
                    "period_month": period,
                    "invoice_date": invoice_date,
                    "due_date": invoice_date,
                    "bank_currency_id": platform.company_id.currency_id.id,
                },
            )
        bank_line = _statement_line(
            env,
            platform.bank_journal_id,
            label=(
                f"{DEMO_PREFIX} FX QA-BANK-RATE-USD-1000{reference_suffix} — "
                f"EUR 700 — SELECT ME{suffix}"
            ),
            date=fields.Date.to_string(fields.Date.add(invoice_date, days=20)),
            amount=700.0,
        )
        if (
            session.state in {"draft", "ready"}
            and not session.payout_ids
            and not bank_line.is_reconciled
            and not Allocation.search_count(
                [("bank_statement_line_id", "=", bank_line.id)],
            )
        ):
            return session, bank_line
    raise RuntimeError(NO_FREE_IMPORT_BATCH)


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
    manager_group = env.ref(
        "usl_platform_billing.group_platform_billing_manager",
    )
    administrator = env.ref("base.user_admin").sudo()
    if manager_group not in administrator.group_ids:
        administrator.write(
            {"group_ids": [Command.link(manager_group.id)]},
        )
    _grant_named_manager(env, manager_group)
    _user(
        env,
        login="qa.platform.manager",
        name="QA Platform Billing Manager",
        password="qa-platform-manager",
        groups=base_user | manager_group,
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
                "bank_label_keywords": "QA DEMO FX",
            },
        )
    else:
        fx_platform.write(
            {
                "currency_id": usd.id,
                "bank_label_pattern": f"{DEMO_PREFIX} FX {{ref}}",
                "bank_label_keywords": "QA DEMO FX",
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

    import_session, import_line = _open_import_demo(env, platform)
    bank_rate_session, bank_rate_line = _open_bank_rate_demo(env, fx_platform)

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
            "import_bank_transaction": import_line.display_name,
            "bank_rate_session": bank_rate_session.display_name,
            "bank_rate_bank_transaction": bank_rate_line.display_name,
        },
    )


bootstrap(globals()["env"])
