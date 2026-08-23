from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    decisions = env["rebuild.account.assurance.decision"].sudo().search([
        ("company_id", "=", False),
    ])
    for decision in decisions:
        company = (
            decision.external_value_id.company_id
            or decision.declaration_id.company_id
            or decision.closing_period_id.company_id
            or decision.reviewer_user_id.company_id
            or env.company
        )
        decision.company_id = company
