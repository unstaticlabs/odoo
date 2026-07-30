{
    "name": "USL Accounting Foundation",
    "summary": "Shared operational extensions for the USL Accounting product",
    "version": "saas~19.2.1.4.2",
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
        "security/immediate_settlement_security.xml",
        "security/ir.model.access.csv",
        "data/immediate_settlement_sequence.xml",
    ],
    "application": False,
    "installable": True,
}
