{
    "name": "USL Product Feedback",
    "summary": "Conversational AI-assisted product feedback on native Project tasks",
    "version": "saas~19.3.2.0.0",
    "category": "Productivity",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": ["base_setup", "project", "mail", "web"],
    "data": [
        "security/feedback_security.xml",
        "security/ir.model.access.csv",
        "data/feedback_project.xml",
        "data/feedback_agent_cron.xml",
        "views/feedback_submission_views.xml",
        "views/feedback_task_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "usl_feedback/static/src/lib/html_to_image.js",
            "usl_feedback/static/src/js/feedback_page_preview.js",
            "usl_feedback/static/src/js/feedback_messaging_menu.js",
            "usl_feedback/static/src/js/feedback_chat_window.js",
            "usl_feedback/static/src/xml/feedback_messaging_menu.xml",
            "usl_feedback/static/src/scss/feedback.scss",
        ],
        "web.assets_unit_tests": [
            "usl_feedback/static/tests/feedback_messaging_menu.test.js",
        ],
        "web.assets_tests": [
            "usl_feedback/static/tests/tours/feedback_tour.js",
        ],
    },
    "application": False,
    "installable": True,
}
