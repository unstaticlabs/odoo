from odoo import SUPERUSER_ID, api


def migrate(cr, _version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    sessions = env["usl.platform.billing.session"].with_context(
        active_test=False,
        tracking_disable=True,
    ).search([])
    sessions._refresh_state()
