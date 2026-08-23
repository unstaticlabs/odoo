from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["res.company"].sudo().search([])._usl_ensure_operational_accounting_journals()
