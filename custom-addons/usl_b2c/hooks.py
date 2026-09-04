from odoo import SUPERUSER_ID, api


INVENTORY_FOUNDATION_SETTINGS = {
    "group_product_variant": True,
    "group_stock_multi_locations": True,
    "group_stock_production_lot": True,
    "group_uom": True,
}


def activate_inventory_foundations(env):
    """Apply the native feature switches once, preserving their side effects."""
    settings = env["res.config.settings"].sudo().create(
        INVENTORY_FOUNDATION_SETTINGS,
    )
    settings.execute()


def post_init_hook(env):
    """Configure the native B2C foundation on a clean installation."""
    env = api.Environment(env.cr, SUPERUSER_ID, {})
    activate_inventory_foundations(env)
    provider = env.ref("delivery.payment_provider_cod", raise_if_not_found=False)
    if provider and provider.state != "disabled":
        provider.write({"state": "disabled"})
