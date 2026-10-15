{
    "name": "USL Project",
    "summary": "Focused project workflow compatibility for Community Odoo",
    "version": "saas~19.3.1.0.15",
    "category": "Services/Project",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": ["mail_tracking", "project"],
    "data": [
        "data/project_task_cron.xml",
        "views/project_project_views.xml",
        "views/project_task_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "usl_project/static/src/favorite_project_navigation.js",
            "usl_project/static/src/project_task_title.js",
            "usl_project/static/src/task_github_properties.js",
            "usl_project/static/src/task_github_properties.xml",
            "usl_project/static/src/task_swimlanes/*.js",
            "usl_project/static/src/task_swimlanes/*.xml",
            "usl_project/static/src/task_swimlanes/*.scss",
        ],
        "web.assets_tests": [
            "usl_project/static/tests/tours/task_navigation.js",
            "usl_project/static/tests/tours/task_github_reference.js",
        ],
        "web.assets_unit_tests": [
            "usl_project/static/tests/favorite_project_navigation.test.js",
            "usl_project/static/tests/project_task_title.test.js",
            "usl_project/static/tests/task_swimlane.test.js",
            "usl_project/static/tests/task_github_properties.test.js",
        ],
    },
    "application": False,
    "installable": True,
    "post_init_hook": "post_init_hook",
}
