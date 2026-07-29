{
    "name": "USL Canonical Navigation",
    "summary": "Deterministic, portable and permission-aware backend workspaces",
    "version": "saas~19.2.1.0.0",
    "category": "Hidden/Tools",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": ["web"],
    "data": [
        "security/navigation_security.xml",
        "security/ir.model.access.csv",
        "views/navigation_actions.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "usl_canonical_navigation/static/src/navigation_state.js",
            "usl_canonical_navigation/static/src/navigation_service.js",
            "usl_canonical_navigation/static/src/view_state.js",
            "usl_canonical_navigation/static/src/recovery_action.js",
            "usl_canonical_navigation/static/src/recovery_action.xml",
        ],
        "web.assets_backend_lazy": [
            "usl_canonical_navigation/static/src/view_state_lazy.js",
        ],
        "web.assets_unit_tests": [
            "usl_canonical_navigation/static/tests/navigation_state.test.js",
            "usl_canonical_navigation/static/tests/navigation_view_state.test.js",
        ],
    },
    "installable": True,
}
