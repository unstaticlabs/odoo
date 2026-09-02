"""Archive the obsolete zero-stock Master Lock allocation placeholder."""

from odoo import SUPERUSER_ID, api
from odoo.exceptions import UserError


PLACEHOLDER_CODE = "PADLOCK_MASTER_9120EUR_ASSORTED_UNALLOCATED"
ASSORTED_PACK_CODE = "GBC-ML-9120-QCOLNOP"


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    products = env["product.product"].with_context(active_test=False).search(
        [("default_code", "=", PLACEHOLDER_CODE)],
    )
    if not products:
        return
    if len(products) != 1:
        raise UserError(
            f"Expected one product with internal reference {PLACEHOLDER_CODE!r}; "
            f"found {len(products)}.",
        )

    product = products[0]
    if env["stock.quant"].sudo().search_count(
        [("product_id", "=", product.id), ("quantity", "!=", 0)],
    ):
        raise UserError(
            f"Cannot archive {PLACEHOLDER_CODE!r}: it has non-zero stock.",
        )
    if env["stock.move"].sudo().search_count([("product_id", "=", product.id)]):
        raise UserError(
            f"Cannot archive {PLACEHOLDER_CODE!r}: it has stock movements.",
        )

    pack = env["product.product"].with_context(active_test=False).search(
        [("default_code", "=", ASSORTED_PACK_CODE)],
    )
    if len(pack) != 1:
        raise UserError(
            f"Expected one supplier pack with internal reference {ASSORTED_PACK_CODE!r}; "
            f"found {len(pack)}.",
        )
    boms = env["mrp.bom"].with_context(active_test=False).search(
        [("product_id", "=", pack.id)],
    )
    conflicting = boms.filtered(
        lambda bom: len(bom.bom_line_ids) != 1
        or bom.bom_line_ids.product_id != product
        or bom.bom_line_ids.product_qty != 4
    )
    if conflicting:
        raise UserError(
            f"Cannot retire the allocation recipe for {ASSORTED_PACK_CODE!r}: "
            "its bill of materials no longer matches the reviewed four-unit recipe.",
        )
    boms.write({"active": False})
    product.product_tmpl_id.write(
        {
            "active": False,
            "sale_ok": False,
            "purchase_ok": False,
            "b2c_catalog_classification": "legacy",
            "b2c_inventory_role": "pending_allocation",
            "b2c_opening_stock_state": "not_evidenced",
        },
    )
