{
    "name": "USL Enable Banking",
    "summary": "Connect personal bank accounts and import booked transactions",
    "version": "saas~19.3.1.0.1",
    "category": "Accounting/Accounting",
    "author": "Unstatic Labs",
    "license": "AGPL-3",
    "depends": ["usl_accounting", "account_statement_import_online"],
    "data": [
        "security/enablebanking_security.xml",
        "security/ir.model.access.csv",
        "views/enablebanking_views.xml",
    ],
    "external_dependencies": {"python": ["httpx", "python-jose"]},
    "installable": True,
    "application": False,
}
