# Accounting restoration

`usl_accounting_restore` is a one-shot, versioned importer. It is available
only to migration services and tests; the normal Odoo service cannot load it.

The importer uses the Odoo ORM to reconstruct ledger, currency, tax,
reconciliation, analytic, expense, asset, evidence, chatter, and audit facts.
Temporary source bindings remain only until dependent restoration stages
finish. Finalization then uninstalls migration modules and rejects remaining
migration models, fields, views, menus, or XML IDs.

Every run must pass exact debit/credit, journal, move, line, tax, currency,
reconciliation, analytic, FEC, report, company-control, attachment, and
idempotence gates. Technical evidence stays in the ignored runtime directory;
native records and user-visible business history remain in Odoo.

Use `migration/manage` for complete QA and transition reconstruction. See the
[migration runbook](../../docs/operations/migration.md). Focused importer tests
may invoke internal Odoo test modules but must use disposable databases.
