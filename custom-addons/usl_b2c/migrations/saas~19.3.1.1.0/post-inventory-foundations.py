from odoo import SUPERUSER_ID, api

from odoo.addons.usl_b2c.hooks import activate_inventory_foundations


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    activate_inventory_foundations(env)
    env["b2c.product.alias"].sudo().search([])._compute_alias_key()
