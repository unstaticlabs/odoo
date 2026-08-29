{
    "name": "USL Product Feedback",
    "summary": "Private in-app product feedback on native Project tasks",
    "version": "saas~19.3.1.0.0",
    "category": "Productivity",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": ["project", "web"],
    "data": [
        "security/feedback_security.xml",
        "security/ir.model.access.csv",
        "data/feedback_project.xml",
        "views/feedback_submission_views.xml",
        "views/feedback_task_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "usl_feedback/static/src/js/feedback_user_menu.js",
            "usl_feedback/static/src/scss/feedback.scss",
        ],
        "web.assets_unit_tests": [
            "usl_feedback/static/tests/feedback_user_menu.test.js",
        ],
        "web.assets_tests": [
            "usl_feedback/static/tests/tours/feedback_tour.js",
        ],
    },
    "application": False,
    "installable": True,
}
