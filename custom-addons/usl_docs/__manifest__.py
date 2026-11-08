{
    "name": "USL Documentation",
    "summary": "Serve the release's user guide and prove it against browser journeys",
    "version": "saas~19.3.1.1.0",
    "category": "Hidden",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": ["web", "web_tour", "usl_locale"],
    "data": [
        "views/templates.xml",
    ],
    "assets": {
        # The guide's own page: no framework, one stylesheet, one script.
        "usl_docs.assets_guide": [
            "usl_docs/static/src/scss/guide.scss",
            "usl_docs/static/src/js/guide.js",
        ],
        # The Help entry of the user menu opens the guide on the current screen.
        "web.assets_backend": [
            "usl_docs/static/src/js/help_menu.js",
        ],
        # The journey runner ships only with the test assets.
        "web.assets_tests": [
            "usl_docs/static/tests/journey_runner.js",
        ],
        "web.assets_unit_tests": [
            "usl_docs/static/tests/help_menu.test.js",
        ],
    },
    "auto_install": True,
    "installable": True,
}
