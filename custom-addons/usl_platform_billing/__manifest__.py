{
    "name": "USL Platform Billing",
    "summary": "Turn content-platform payouts into auditable invoices and bills",
    "version": "saas~19.3.1.3.3",
    "category": "Accounting/Accounting",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": [
        "account",
        "account_reconcile_oca",
        "analytic",
        "mail",
        "usl_accounting",
        "usl_documents",
    ],
    "data": [
        "security/platform_billing_security.xml",
        "security/ir.model.access.csv",
        "data/french_translation_overrides.xml",
        "wizard/bank_import_wizard_views.xml",
        "wizard/post_confirm_wizard_views.xml",
        "views/platform_views.xml",
        "views/payout_views.xml",
        "views/session_views.xml",
        "views/account_move_views.xml",
        "views/menu_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "usl_platform_billing/static/src/bank_record_preview.js",
            "usl_platform_billing/static/src/bank_record_preview.xml",
        ],
        "web.assets_tests": [
            "usl_platform_billing/static/tests/tours/platform_billing_tours.js",
        ],
        "web.assets_unit_tests": [
            "usl_platform_billing/static/src/bank_record_preview.js",
            "usl_platform_billing/static/src/bank_record_preview.xml",
            "usl_platform_billing/static/tests/bank_record_preview.test.js",
        ],
    },
    "application": True,
    "installable": True,
}
