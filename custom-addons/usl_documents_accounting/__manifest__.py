{
    "name": "USL Documents — Accounting Contexts",
    "summary": "Paperless evidence on USL tax declarations and closing workspaces",
    "version": "saas~19.3.1.2.0",
    "category": "Accounting/Accounting",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": ["usl_documents", "rebuild_account_migration"],
    "data": [
        "data/bank_statement_archive_cron.xml",
        "views/accounting_document_views.xml",
        "views/bank_statement_views.xml",
    ],
    "installable": True,
}
