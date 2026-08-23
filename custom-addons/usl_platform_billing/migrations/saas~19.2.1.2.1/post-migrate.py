from odoo import SUPERUSER_ID, Command, api


def migrate(cr, _version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    manager = env.ref("usl_platform_billing.group_platform_billing_manager")
    analytic = env.ref("analytic.group_analytic_accounting")
    if analytic not in manager.implied_ids:
        manager.write({"implied_ids": [Command.link(analytic.id)]})
