"""Give the existing finished-products category the external id the module now declares.

The product reconstruction created "Goods / GBC Finished Products" without an
external id. The module's data file now declares one so code can refer to the
category. Binding the id here, before that file loads, keeps the loader from
creating a second category with the same name.
"""

from odoo import SUPERUSER_ID, api

MODULE = "usl_b2c"
NAME = "product_category_gbc_finished_products"
CATEGORY_NAME = "GBC Finished Products"


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    if env.ref(f"{MODULE}.{NAME}", raise_if_not_found=False):
        return
    goods = env.ref("product.product_category_goods", raise_if_not_found=False)
    domain = [("name", "=", CATEGORY_NAME)]
    if goods:
        domain.append(("parent_id", "=", goods.id))
    category = env["product.category"].with_context(lang="en_US").search(domain, limit=2)
    if len(category) != 1:
        return
    env["ir.model.data"].create(
        {
            "module": MODULE,
            "name": NAME,
            "model": "product.category",
            "res_id": category.id,
            "noupdate": True,
        },
    )
