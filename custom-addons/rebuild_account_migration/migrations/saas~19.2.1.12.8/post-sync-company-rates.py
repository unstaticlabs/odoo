from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    companies = env["res.company"].sudo().search([])
    companies._rebuild_synchronize_existing_shared_ecb_rates()
