{
    "name": "USL Locale",
    "summary": "Shared day-first dates and European number presentation",
    "version": "saas~19.2.1.0.0",
    "category": "Hidden",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": ["web"],
    "data": [
        "data/european_date_format.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "usl_locale/static/src/js/european_date_format.js",
        ],
        "web.assets_unit_tests": [
            "usl_locale/static/tests/european_date_format.test.js",
        ],
    },
    "auto_install": True,
    "installable": True,
}
