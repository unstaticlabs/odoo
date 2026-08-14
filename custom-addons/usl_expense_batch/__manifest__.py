{
    "name": "USL Expense Batches",
    "summary": "Submit and review related expenses as a lightweight claim",
    "version": "saas~19.3.1.1.0",
    "category": "Human Resources/Expenses",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": ["hr_expense", "usl_documents"],
    "data": [
        "security/expense_batch_security.xml",
        "security/ir.model.access.csv",
        "wizard/expense_batch_create_wizard_views.xml",
        "views/expense_batch_views.xml",
        "views/hr_expense_views.xml",
        "views/account_move_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "usl_expense_batch/static/src/js/expense_batch_list.js",
            "usl_expense_batch/static/src/xml/expense_batch_list.xml",
        ],
        "web.assets_unit_tests": [
            "usl_expense_batch/static/tests/expense_batch_list.test.js",
        ],
    },
    "application": False,
    "installable": True,
}
