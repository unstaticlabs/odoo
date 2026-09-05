from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Upgrade legacy fiscal-year tasks to the period-aware French schedule."""
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    Rule = env["rebuild.account.declaration.rule"].sudo().with_context(
        accounting_definition_seed=True,
    )

    for rule in Rule.search([("code", "=", "FR_2571")]):
        rule.write({
            "trigger_kind": "not_first_fiscal_year",
            "deadline_rule": "is_instalments",
        })
    for rule in Rule.search([("code", "=", "FR_2572")]):
        rule.write({"deadline_rule": "is_balance"})
    for rule in Rule.search([("code", "=", "FR_2065")]):
        rule.write({
            "tax_form_codes": (
                "2065-SD,2033-A-SD,2033-B-SD,2033-C-SD,2033-D-SD,"
                "2033-E-SD,2033-F-SD,2033-G-SD"
            ),
            "supporting_form_codes": "2065-bis-SD, 2033 A-G-SD",
            "deadline_rule": "result_return",
        })
    Rule.search([("code", "=", "FR_2033")]).write({
        "active": False,
        "lifecycle": "deprecated",
    })
    for rule in Rule.search([("code", "=", "FR_3517_S")]):
        rule.write({"deadline_rule": "ca12_fiscal"})
    for rule in Rule.search([("code", "=", "FR_3514")]):
        rule.write({
            "period_basis": "calendar_month",
            "deadline_rule": "vat_instalment",
        })
    for rule in Rule.search([("code", "=", "FR_RCM_2777")]):
        rule.write({
            "period_basis": "calendar_month",
            "trigger_kind": "dividend_transactions",
            "deadline_rule": "next_month_15",
        })

    env["rebuild.account.declaration.rule"]._ensure_governance_metadata()
    env["rebuild.account.declaration"]._sync_all_profiled_companies()
