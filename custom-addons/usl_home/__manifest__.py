{
    "name": "USL Home",
    "summary": "Personalized launcher and operational attention cockpit",
    "version": "saas~19.3.1.0.4",
    "category": "Productivity",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": ["web", "mail", "contacts", "project"],
    "data": [
        "security/ir.model.access.csv",
        "security/usl_home_security.xml",
        "views/usl_home_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "usl_home/static/src/home/**/*",
            "usl_home/static/src/add_to_home/**/*",
        ],
        "web.assets_unit_tests": [
            "usl_home/static/tests/home.test.js",
        ],
        "web.assets_tests": [
            "usl_home/static/tests/tours/home_tours.js",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": True,
}
