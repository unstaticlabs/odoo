{
    "name": "USL Accounting Foundation",
    "summary": "Shared operational extensions for the USL Accounting product",
    "version": "saas~19.2.1.1.0",
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
    ],
    "data": [
        "security/expense_bank_matching_security.xml",
        "security/ir.model.access.csv",
        "wizard/expense_bank_match_wizard_views.xml",
        "views/hr_expense_bank_matching_views.xml",
    ],
    "application": False,
    "installable": True,
}
