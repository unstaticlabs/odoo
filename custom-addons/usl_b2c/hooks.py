from odoo import SUPERUSER_ID, api


def post_init_hook(env):
    """Keep Delivery's install-time Cash on Delivery provider inactive."""
    env = api.Environment(env.cr, SUPERUSER_ID, {})
    provider = env.ref("delivery.payment_provider_cod", raise_if_not_found=False)
    if provider and provider.state != "disabled":
        provider.write({"state": "disabled"})
