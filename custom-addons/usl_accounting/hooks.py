from odoo import SUPERUSER_ID, api


def post_init_hook(env):
    env = api.Environment(env.cr, SUPERUSER_ID, {})
    env["account.group"]._ensure_french_compatibility_groups()
