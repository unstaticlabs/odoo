# ruff: noqa: BLE001, F821, T201, TRY301

"""Non-polluting acceptance proof for the reconstructed multi-company target.

Run through ``odoo shell``.  The business journeys execute inside a savepoint
which is deliberately rolled back; imported data is only read.
"""

import json
from datetime import date

from odoo import Command


class _AcceptanceRollback(Exception):
    pass


source_company_names = {
    1: "Unstatic Labs",
    8: "USL MEDIA",
}
companies = env["res.company"].sudo().search([
    ("name", "in", list(source_company_names.values())),
], order="name")
companies_by_name = {company.name: company for company in companies}
main_company = companies_by_name.get(source_company_names[1])
media_company = companies_by_name.get(source_company_names[8])
errors = []
if not main_company or not media_company:
    errors.append("Both reconstructed source companies must exist")

summary = {
    "companies": [],
    "combined": {},
    "shared_currency_rates": {},
    "multi_company_expenses": {},
    "journeys": {},
    "reviewer_scope": {},
}

for company in companies:
    source_company_id = next(
        source_id
        for source_id, name in source_company_names.items()
        if name == company.name
    )
    posted_lines = env["account.move.line"].sudo().search([
        ("company_id", "=", company.id),
        ("move_id.state", "=", "posted"),
        ("account_id", "!=", False),
    ])
    debit = sum(posted_lines.mapped("debit"))
    credit = sum(posted_lines.mapped("credit"))
    company_summary = {
        "source_company_id": source_company_id,
        "name": company.name,
        "currency": company.currency_id.name,
        "accounts": env["account.account"].sudo().with_context(
            active_test=False,
        ).search_count([("company_ids", "in", company.id)]),
        "journals": env["account.journal"].sudo().with_context(
            active_test=False,
        ).search_count([("company_id", "=", company.id)]),
        "taxes": env["account.tax"].sudo().with_context(
            active_test=False,
        ).search_count([("company_id", "=", company.id)]),
        "fiscal_positions": env["account.fiscal.position"].sudo().with_context(
            active_test=False,
        ).search_count([("company_id", "=", company.id)]),
        "reconciliation_models": env["account.reconcile.model"].sudo().with_context(
            active_test=False,
        ).search_count([("company_id", "=", company.id)]),
        "expense_journal": company.expense_journal_id.code,
        "posted_debit": round(debit, 2),
        "posted_credit": round(credit, 2),
    }
    if round(debit - credit, 2):
        errors.append(f"{company.name} posted ledger is unbalanced")
    summary["companies"].append(company_summary)

if companies and len(set(companies.currency_id.mapped("name"))) == 1:
    summary["combined"] = {
        "currency": companies.currency_id.name,
        "posted_debit": round(sum(
            item["posted_debit"] for item in summary["companies"]
        ), 2),
        "posted_credit": round(sum(
            item["posted_credit"] for item in summary["companies"]
        ), 2),
        "company_contributions": {
            item["name"]: {
                "debit": item["posted_debit"],
                "credit": item["posted_credit"],
            }
            for item in summary["companies"]
        },
    }
else:
    errors.append("Combined management reporting requires one shared currency")

if companies and len(set(companies.currency_id.ids)) == 1:
    shared_companies = companies.filtered(
        "rebuild_currency_rate_share_same_base",
    )
    all_rates = env["res.currency.rate"].sudo().search([
        ("company_id", "in", shared_companies.ids),
    ])
    provider_rates_by_company = {
        company.id: {
            (rate.currency_id.name, str(rate.name)): round(rate.rate, 12)
            for rate in all_rates.filtered(
                lambda candidate: (
                    candidate.company_id == company
                    and candidate.rebuild_rate_provider == "ecb"
                ),
            )
        }
        for company in shared_companies
    }
    protected_keys_by_company = {
        company.id: {
            (rate.currency_id.name, str(rate.name))
            for rate in all_rates.filtered(
                lambda candidate: (
                    candidate.company_id == company
                    and candidate.rebuild_rate_provider != "ecb"
                ),
            )
        }
        for company in shared_companies
    }
    provider_keys = set().union(*provider_rates_by_company.values())
    differences = []
    manual_exceptions = 0
    for key in provider_keys:
        values = {
            rates[key]
            for rates in provider_rates_by_company.values()
            if key in rates
        }
        if len(values) > 1:
            differences.append(key)
            continue
        for company in shared_companies:
            if key in provider_rates_by_company[company.id]:
                continue
            if key in protected_keys_by_company[company.id]:
                manual_exceptions += 1
            else:
                differences.append(key)
                break
    aligned = bool(provider_keys) and not differences
    summary["shared_currency_rates"] = {
        "companies": shared_companies.mapped("name"),
        "provider_rate_count_per_company": {
            company.name: len(provider_rates_by_company[company.id])
            for company in shared_companies
        },
        "protected_manual_exceptions": manual_exceptions,
        "aligned": aligned,
    }
    if len(shared_companies) > 1 and not aligned:
        errors.append("Same-currency ECB rate histories are not aligned")

expense_users = env["res.users"].sudo().with_context(active_test=False).search([
    ("usl_expense_multi_company", "=", True),
    ("active", "=", True),
    ("share", "=", False),
])
expense_profiles = {}
for user in expense_users:
    profile_companies = env["hr.employee"].sudo().with_context(
        active_test=False,
    ).search([
        ("user_id", "=", user.id),
        ("active", "=", True),
        ("company_id", "in", user.company_ids.ids),
    ]).company_id
    missing = user.company_ids - profile_companies
    expense_profiles[user.login] = {
        "allowed_companies": user.company_ids.mapped("name"),
        "employee_companies": profile_companies.mapped("name"),
        "missing_companies": missing.mapped("name"),
    }
    if missing:
        errors.append(
            f"{user.login} lacks expense profiles for: "
            + ", ".join(missing.mapped("name")),
        )
summary["multi_company_expenses"] = expense_profiles
if len(companies) > 1 and not expense_users:
    errors.append("No governed multi-company expense user is configured")

reviewer = env["res.users"].sudo().search([("login", "=", "prosper")], limit=1)
if reviewer and main_company and media_company:
    reviewer_env = env(
        user=reviewer.id,
        context={
            **env.context,
            "allowed_company_ids": reviewer.company_ids.ids,
        },
    )
    media_move_count = reviewer_env["account.move"].search_count([
        ("company_id", "=", media_company.id),
    ])
    main_move_count = reviewer_env["account.move"].search_count([
        ("company_id", "=", main_company.id),
    ])
    media_custom_counts = {
        model_name: reviewer_env[model_name].search_count([
            ("company_id", "=", media_company.id),
        ])
        for model_name in (
            "rebuild.account.overview",
            "rebuild.account.hygiene.issue",
            "rebuild.account.closing.period",
            "rebuild.account.declaration",
        )
    }
    reviewer_companies = set(reviewer.company_ids.mapped("name"))
    required_reviewer_companies = {main_company.name, media_company.name}
    summary["reviewer_scope"] = {
        "login": reviewer.login,
        "allowed_companies": sorted(reviewer_companies),
        "multi_company_ui": reviewer.has_group("base.group_multi_company"),
        "main_move_count": main_move_count,
        "media_move_count": media_move_count,
        "media_custom_counts": media_custom_counts,
    }
    if not required_reviewer_companies.issubset(reviewer_companies):
        errors.append("Accounting reviewer lacks the approved two-company scope")
    if not reviewer.has_group("base.group_multi_company"):
        errors.append("Accounting reviewer lacks the native multi-company UI group")
    if not main_move_count or not media_move_count:
        errors.append("Accounting reviewer cannot read both companies' ledgers")
else:
    errors.append("The reconstructed Accounting reviewer is missing")

if media_company:
    try:
        with env.cr.savepoint():
            media_env = env(
                context={
                    **env.context,
                    "allowed_company_ids": [media_company.id],
                    "tracking_disable": True,
                    "mail_create_nolog": True,
                },
            )
            journals = media_env["account.journal"].search([
                ("company_id", "=", media_company.id),
            ])
            sale_journal = journals.filtered(lambda journal: journal.type == "sale")[:1]
            purchase_journal = journals.filtered(
                lambda journal: journal.type == "purchase",
            )[:1]
            bank_journal = journals.filtered(lambda journal: journal.type == "bank")[:1]
            general_journal = journals.filtered(
                lambda journal: journal.type == "general",
            )[:1]
            required_journals = {
                "sale": sale_journal,
                "purchase": purchase_journal,
                "bank": bank_journal,
                "general": general_journal,
            }
            missing = [name for name, journal in required_journals.items() if not journal]
            if missing:
                raise RuntimeError(
                    "USL MEDIA is missing journals: " + ", ".join(missing),
                )

            income_account = media_env["account.account"].search([
                ("company_ids", "in", media_company.id),
                ("active", "=", True),
                ("account_type", "=", "income"),
            ], limit=1)
            expense_account = media_env["account.account"].search([
                ("company_ids", "in", media_company.id),
                ("active", "=", True),
                ("account_type", "=", "expense"),
            ], limit=1)
            partner = media_env["res.partner"].create({
                "name": "USL MEDIA multi-company acceptance",
                "company_id": media_company.id,
            })

            moves = media_env["account.move"]
            for move_type, journal, account, amount in (
                ("out_invoice", sale_journal, income_account, 120.0),
                ("out_refund", sale_journal, income_account, 20.0),
                ("in_invoice", purchase_journal, expense_account, 80.0),
                ("in_refund", purchase_journal, expense_account, 10.0),
            ):
                move = media_env["account.move"].create({
                    "move_type": move_type,
                    "company_id": media_company.id,
                    "journal_id": journal.id,
                    "partner_id": partner.id,
                    "invoice_date": date.today(),
                    "invoice_line_ids": [Command.create({
                        "name": f"Acceptance {move_type}",
                        "account_id": account.id,
                        "quantity": 1.0,
                        "price_unit": amount,
                    })],
                })
                move.action_post()
                moves |= move

            general_move = media_env["account.move"].create({
                "company_id": media_company.id,
                "journal_id": general_journal.id,
                "date": date.today(),
                "ref": "Multi-company acceptance entry",
                "line_ids": [
                    Command.create({
                        "name": "Acceptance debit",
                        "account_id": expense_account.id,
                        "debit": 5.0,
                    }),
                    Command.create({
                        "name": "Acceptance credit",
                        "account_id": income_account.id,
                        "credit": 5.0,
                    }),
                ],
            })
            general_move.action_post()

            customer_invoice = moves.filtered(
                lambda move: move.move_type == "out_invoice",
            )
            payment = media_env["account.payment"].create({
                "company_id": media_company.id,
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": partner.id,
                "amount": customer_invoice.amount_total,
                "date": date.today(),
                "journal_id": bank_journal.id,
                "payment_method_line_id": (
                    bank_journal.inbound_payment_method_line_ids[:1].id
                ),
            })
            payment.action_post()
            receivable_lines = (
                customer_invoice.line_ids | payment.move_id.line_ids
            ).filtered(
                lambda line: line.account_id.account_type == "asset_receivable",
            )
            receivable_lines.reconcile()

            bank_line = media_env["account.bank.statement.line"].create({
                "company_id": media_company.id,
                "journal_id": bank_journal.id,
                "date": date.today(),
                "payment_ref": "Multi-company acceptance bank line",
                "partner_id": partner.id,
                "amount": -42.0,
            })

            employee = media_env["hr.employee"].create({
                "name": "USL MEDIA acceptance employee",
                "company_id": media_company.id,
            })
            product = media_env["product.product"].create({
                "name": "USL MEDIA acceptance expense",
                "can_be_expensed": True,
                "standard_price": 12.0,
            })
            product.product_tmpl_id.with_company(
                media_company,
            ).property_account_expense_id = expense_account
            expense = media_env["hr.expense"].with_context(
                rebuild_source_materialization=True,
            ).create({
                "name": "USL MEDIA acceptance expense",
                "employee_id": employee.id,
                "product_id": product.id,
                "company_id": media_company.id,
                "payment_mode": "own_account",
                "total_amount_currency": 12.0,
            })
            receipt = media_env["ir.attachment"].create({
                "name": "usl-media-acceptance-receipt.pdf",
                "type": "binary",
                "raw": b"USL MEDIA acceptance receipt",
                "res_model": "hr.expense",
                "res_id": expense.id,
            })
            expense.message_main_attachment_id = receipt
            expense.action_submit()
            expense._do_approve()
            post_action = expense.action_post()
            if post_action:
                post_wizard = media_env["hr.expense.post.wizard"].with_context(
                    post_action["context"],
                ).browse(post_action["res_id"])
                post_wizard.action_post_entry()

            summary["journeys"] = {
                "company": media_company.name,
                "invoice_types": sorted(moves.mapped("move_type")),
                "all_documents_posted": all(
                    move.state == "posted" for move in moves
                ),
                "general_entry_posted": general_move.state == "posted",
                "payment_state": payment.state,
                "payment_posted": payment.move_id.state == "posted",
                "customer_invoice_settled": (
                    customer_invoice.payment_state == "paid"
                ),
                "bank_transaction_created": bool(bank_line.move_id),
                "expense_state": expense.state,
                "expense_move_posted": bool(
                    expense.account_move_id
                    and expense.account_move_id.state == "posted",
                ),
            }
            if not all((
                summary["journeys"]["all_documents_posted"],
                summary["journeys"]["general_entry_posted"],
                summary["journeys"]["payment_posted"],
                summary["journeys"]["customer_invoice_settled"],
                summary["journeys"]["bank_transaction_created"],
                summary["journeys"]["expense_move_posted"],
            )):
                errors.append("One or more USL MEDIA accounting journeys failed")
            raise _AcceptanceRollback()
    except _AcceptanceRollback:
        pass
    except Exception as exc:
        errors.append(f"USL MEDIA workflow exception: {exc}")

summary["status"] = "passed" if not errors else "failed"
summary["errors"] = errors
print(json.dumps(summary, indent=2, sort_keys=True, default=str))
if errors:
    raise RuntimeError("; ".join(errors))
