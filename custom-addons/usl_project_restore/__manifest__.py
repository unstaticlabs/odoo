{
    "name": "USL Project Restoration",
    "summary": "Repeatable restoration of Odoo Online projects and tasks",
    "version": "saas~19.2.1.1.0",
    "category": "Services/Project",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": [
        "rebuild_account_migration",
        "project",
        "project_hr_expense",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/project_restore_views.xml",
        "views/project_task_views.xml",
    ],
    "application": False,
    "installable": True,
}
