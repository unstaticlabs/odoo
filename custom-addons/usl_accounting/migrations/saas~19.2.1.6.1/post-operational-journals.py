from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Company = env["res.company"].sudo()
    Journal = env["account.journal"].sudo().with_context(active_test=False)
    Account = env["account.account"].sudo().with_context(active_test=False)
    candidates = Company.browse()
    for company in Company.search([]):
        if Journal.search_count([("company_id", "=", company.id)]) and (
            Account.search_count([("company_ids", "in", company.id)])
        ):
            candidates |= company
    candidates._usl_ensure_operational_accounting_journals()
