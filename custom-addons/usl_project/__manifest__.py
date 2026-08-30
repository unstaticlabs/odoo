{
    "name": "USL Project",
    "summary": "Focused project workflow compatibility for Community Odoo",
    "version": "saas~19.3.1.0.6",
    "category": "Services/Project",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": ["mail_tracking", "project"],
    "data": [
        "views/project_project_views.xml",
        "views/project_task_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "usl_project/static/src/project_task_title.js",
        ],
        "web.assets_unit_tests": [
            "usl_project/static/tests/project_task_title.test.js",
        ],
    },
    "application": False,
    "installable": True,
}
