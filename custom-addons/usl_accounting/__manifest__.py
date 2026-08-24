{
    "name": "USL Accounting Foundation",
    "summary": "Shared operational extensions for the USL Accounting product",
    "version": "saas~19.3.1.7.0",
    "category": "Accounting/Accounting",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": [
        "account",
        "account_payment",
        "account_reconcile_oca",
        "analytic",
        "hr_expense",
        "spreadsheet_account",
        "usl_locale",
    ],
    "data": [
        "security/expense_bank_matching_security.xml",
        "security/immediate_settlement_security.xml",
        "security/ir.model.access.csv",
        "data/immediate_settlement_sequence.xml",
        "wizard/expense_bank_match_wizard_views.xml",
        "views/hr_expense_bank_matching_views.xml",
        "views/immediate_settlement_views.xml",
        "views/res_users_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "usl_accounting/static/src/js/immediate_settlement.js",
            "usl_accounting/static/src/scss/payment_suggestions.scss",
            "usl_accounting/static/src/xml/account_payment_suggestions.xml",
        ],
        "web.assets_unit_tests": [
            "usl_accounting/static/tests/payment_suggestions.test.js",
        ],
        "web.assets_tests": [
            "usl_accounting/static/tests/tours/immediate_settlement_tour.js",
        ],
    },
    "application": False,
    "installable": True,
}
