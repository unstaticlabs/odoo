{
    "name": "USL Project Restoration",
    "summary": "Temporary Odoo Online project migration machinery",
    "version": "saas~19.3.1.1.2",
    "category": "Services/Project",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": [
        "usl_accounting_restore",
        "usl_project",
        "project_hr_expense",
    ],
    "data": [
        "security/ir.model.access.csv",
    ],
    "application": False,
    "installable": True,
}
