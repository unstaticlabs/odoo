{
    "name": "USL Platform Billing Historical Restore",
    "summary": "Temporary reconstruction of Odoo Online platform-billing history",
    "version": "saas~19.2.1.0.0",
    "category": "Accounting/Accounting",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": [
        "usl_accounting_restore",
        "usl_platform_billing",
    ],
    "data": [
        "security/ir.model.access.csv",
    ],
    "application": False,
    "installable": True,
}
