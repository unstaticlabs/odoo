{
    "name": "USL Product Master Restoration",
    "summary": "Temporary Odoo Online product-master migration machinery",
    "version": "saas~19.3.1.1.0",
    "category": "Technical",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    # Keep Delivery and MRP explicit for update paths where usl_b2c was already
    # installed before those native dependencies were introduced.  They own
    # source-backed product customs fields and the ``mrp_operation`` picking
    # type; none may be coerced into a generic custom substitute.
    "depends": ["delivery", "mrp", "usl_accounting_restore", "usl_b2c"],
    "data": ["security/ir.model.access.csv"],
    "application": False,
    "installable": True,
}
