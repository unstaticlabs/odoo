{
    "name": "USL Accounting Migration",
    "summary": "Source-trace, import-run and discrepancy records for USL accounting reconstruction.",
    "version": "19.0.1.0.0",
    "category": "Accounting/Accounting",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": [
        "account",
        "account_payment",
        "analytic",
        "l10n_fr_account",
    ],
    "data": [
        "security/rebuild_account_security.xml",
        "security/ir.model.access.csv",
        "views/rebuild_account_migration_views.xml",
    ],
    "application": False,
    "installable": True,
}
