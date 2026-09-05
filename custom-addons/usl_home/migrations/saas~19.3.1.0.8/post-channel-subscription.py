from odoo import SUPERUSER_ID, api
from odoo.addons.usl_home.hooks import ensure_distribution_update_channel


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    ensure_distribution_update_channel(env)
