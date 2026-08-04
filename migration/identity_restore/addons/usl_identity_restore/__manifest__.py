{
    "name": "USL Identity Restoration",
    "summary": "Temporary Odoo Online identity migration machinery",
    "version": "saas~19.2.1.0.0",
    "category": "Technical",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": ["rebuild_account_migration", "usl_pocketid"],
    "data": ["security/ir.model.access.csv"],
    "application": False,
    "installable": True,
}
