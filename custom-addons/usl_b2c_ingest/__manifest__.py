{
    "name": "USL B2C Ingestion",
    "summary": "Ingest channel exports into B2C commerce, Sales, Inventory and Accounting",
    "version": "saas~19.3.1.0.1",
    "category": "Sales/Sales",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": [
        "usl_b2c",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/ingest_security.xml",
        "views/channel_views.xml",
        "views/import_batch_views.xml",
        "views/import_row_views.xml",
        "views/import_issue_views.xml",
        "views/oss_return_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
}
