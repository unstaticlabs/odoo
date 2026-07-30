{
    "name": "USL Platform Billing",
    "summary": "Turn content-platform payouts into auditable invoices and bills",
    "version": "saas~19.2.1.1.1",
    "category": "Accounting/Accounting",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": [
        "account",
        "account_reconcile_oca",
        "analytic",
        "mail",
    ],
    "data": [
        "security/platform_billing_security.xml",
        "security/ir.model.access.csv",
        "wizard/bank_import_wizard_views.xml",
        "wizard/post_confirm_wizard_views.xml",
        "views/platform_views.xml",
        "views/payout_views.xml",
        "views/session_views.xml",
        "views/account_move_views.xml",
        "views/menu_views.xml",
    ],
    "assets": {
        "web.assets_tests": [
            "usl_platform_billing/static/tests/tours/platform_billing_tours.js",
        ],
    },
    "application": True,
    "installable": True,
}
