"""Prepare synthetic operations and production-named identity companies."""

# Odoo shell script; ``env`` is supplied by Odoo.
# ruff: noqa: EM101, F821, T201

from odoo import Command

operations = env.company.sudo()  # noqa: F821
operations.write({
    "name": "QA Operations",
    "country_id": env.ref("base.fr").id,  # noqa: F821
    "account_fiscal_country_id": env.ref("base.fr").id,  # noqa: F821
    "currency_id": env.ref("base.EUR").id,  # noqa: F821
})
if operations.chart_template != "fr_comp":
    env["account.chart.template"].try_loading(  # noqa: F821
        "fr_comp",
        company=operations,
        install_demo=False,
    )

# The Platform Billing bootstrap intentionally augments one valid business
# configuration instead of inventing accounting defaults. A clean QA database
# therefore receives the smallest explicit, native configuration required by
# that reusable fixture.
Account = env["account.account"].sudo().with_company(operations)  # noqa: F821
income_account = Account.search(
    [
        ("company_ids", "in", operations.ids),
        ("account_type", "in", ("income", "income_other")),
    ],
    limit=1,
)
expense_account = Account.search(
    [
        ("company_ids", "in", operations.ids),
        (
            "account_type",
            "in",
            ("expense", "expense_other", "expense_direct_cost"),
        ),
    ],
    limit=1,
)
if not income_account or not expense_account:
    raise RuntimeError("The French QA chart lacks income or expense accounts")

Journal = env["account.journal"].sudo().with_company(operations)  # noqa: F821
journals = {
    journal_type: Journal.search(
        [("company_id", "=", operations.id), ("type", "=", journal_type)],
        limit=1,
    )
    for journal_type in ("sale", "purchase", "general", "bank")
}
missing_journals = [name for name, journal in journals.items() if not journal]
if missing_journals:
    raise RuntimeError(
        "The French QA chart lacks journals: " + ", ".join(missing_journals),
    )

Partner = env["res.partner"].sudo()  # noqa: F821
platform_partner = Partner.search(
    [("name", "=", "QA Platform Source"), ("company_id", "=", operations.id)],
    limit=1,
)
if not platform_partner:
    platform_partner = Partner.create({
        "name": "QA Platform Source",
        "company_id": operations.id,
        "is_company": True,
    })

Product = env["product.product"].sudo().with_company(operations)  # noqa: F821
revenue_product = Product.search(
    [("name", "=", "QA Platform Revenue"), ("company_id", "=", operations.id)],
    limit=1,
)
if not revenue_product:
    revenue_product = Product.create({
        "name": "QA Platform Revenue",
        "company_id": operations.id,
        "sale_ok": True,
        "purchase_ok": False,
        "property_account_income_id": income_account.id,
        "taxes_id": [Command.clear()],
    })
commission_product = Product.search(
    [("name", "=", "QA Platform Commission"), ("company_id", "=", operations.id)],
    limit=1,
)
if not commission_product:
    commission_product = Product.create({
        "name": "QA Platform Commission",
        "company_id": operations.id,
        "sale_ok": False,
        "purchase_ok": True,
        "property_account_expense_id": expense_account.id,
        "supplier_taxes_id": [Command.clear()],
    })

Platform = env["usl.platform.billing.platform"].sudo()  # noqa: F821
if not Platform.search(
    [("name", "=", "QA Platform Source"), ("company_id", "=", operations.id)],
    limit=1,
):
    Platform.create({
        "name": "QA Platform Source",
        "company_id": operations.id,
        "partner_id": platform_partner.id,
        "commission_rate": 20.0,
        "currency_id": operations.currency_id.id,
        "revenue_product_id": revenue_product.id,
        "commission_product_id": commission_product.id,
        "sale_journal_id": journals["sale"].id,
        "purchase_journal_id": journals["purchase"].id,
        "compensation_journal_id": journals["general"].id,
        "bank_journal_id": journals["bank"].id,
        "bank_label_pattern": "QA payout {ref}",
        "bank_label_keywords": "QA PLATFORM",
        "auto_create_compensation": True,
    })

company = env["res.company"].sudo().search([("name", "=", "Unstatic Labs")])  # noqa: F821
if len(company) > 1:
    raise RuntimeError("Clean QA has duplicate Unstatic Labs companies")
if not company:
    company = env["res.company"].sudo().create({  # noqa: F821
        "name": "Unstatic Labs",
        "country_id": env.ref("base.fr").id,  # noqa: F821
        "currency_id": env.ref("base.EUR").id,  # noqa: F821
    })
media_company = env["res.company"].sudo().search([("name", "=", "USL MEDIA")])  # noqa: F821
if len(media_company) > 1:
    raise RuntimeError("Clean QA has duplicate USL MEDIA companies")
if not media_company:
    media_company = env["res.company"].sudo().create({  # noqa: F821
        "name": "USL MEDIA",
        "country_id": env.ref("base.fr").id,  # noqa: F821
        "currency_id": env.ref("base.EUR").id,  # noqa: F821
    })
historical = env["res.users"].sudo().with_context(active_test=False).search(  # noqa: F821
    [("login", "=", "roger@xaic.cat")],
    limit=1,
)
if not historical:
    historical = env["res.users"].sudo().with_context(  # noqa: F821
        no_reset_password=True,
    ).create({
        "active": True,
        "company_id": company.id,
        "company_ids": [(6, 0, company.ids)],
        "login": "roger@xaic.cat",
        "name": "Roger (historical)",
    })
reviewer = env["res.users"].sudo().with_context(active_test=False).search(  # noqa: F821
    [("login", "=", "prosper")],
    limit=1,
)
if not reviewer:
    reviewer = env["res.users"].sudo().with_context(  # noqa: F821
        no_reset_password=True,
    ).create({
        "active": True,
        "company_id": company.id,
        "company_ids": [(6, 0, company.ids)],
        "login": "prosper",
        "name": "Prosper",
    })
env.cr.commit()  # noqa: F821
print(
    "Clean QA companies: "
    f"operations={operations.display_name}, identity={company.display_name}, "
    f"review={media_company.display_name}",
)
