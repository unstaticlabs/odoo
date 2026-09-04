from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Normalize visible result dossiers and new-company VAT periods."""
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    Rule = env["rebuild.account.declaration.rule"].sudo().with_context(
        accounting_definition_seed=True,
    )
    for rule in Rule.search([("code", "=", "FR_2065")]):
        rule.write({
            "name": f"IS result dossier - 2065 and 2033 A-G ({rule.version})",
            "form_code": "2065-SD result dossier",
            "tax_form_codes": (
                "2065-SD,2033-A-SD,2033-B-SD,2033-C-SD,2033-D-SD,"
                "2033-E-SD,2033-F-SD,2033-G-SD"
            ),
            "supporting_form_codes": "2065-bis-SD, 2033 A-G-SD",
            "applicability_guidance": (
                "The 2065 filing and its 2033 A-G simplified-regime annexes "
                "form one result dossier."
            ),
        })
    env["rebuild.account.declaration"]._sync_all_profiled_companies()
