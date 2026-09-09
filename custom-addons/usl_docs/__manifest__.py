{
    "name": "USL Documentation",
    "summary": "Serve the release's user guide and prove it against browser journeys",
    "version": "saas~19.3.1.0.0",
    "category": "Hidden",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": ["web", "web_tour", "usl_locale"],
    "data": [],
    "assets": {
        "web.assets_tests": [
            "usl_docs/static/tests/journey_runner.js",
        ],
    },
    "auto_install": True,
    "installable": True,
}
