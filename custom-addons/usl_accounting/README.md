# USL Accounting Foundation

Technical module name: `usl_accounting`

This module owns shared operational Accounting extensions that are reused by
Controls, Reports and the compatibility product module:

- governed fiscal-year behavior;
- payment and partner suggestions;
- exact foreign-amount settlement for company-currency bank transactions whose
  foreign amount was estimated by Odoo;
- bank matching and reconciliation compatibility;
- analytic measures and entry-direction safeguards;
- scoped read-only accounting evidence protection.

It extends native Odoo and pinned OCA models. It does not own reconstruction
models, source traces, report definitions, Controls, declarations, e-invoice
activation or normal Accounting menus.

Existing database XML IDs, views, actions and security records remain in
`rebuild_account_migration` during the staged compatibility period. Do not add
a reverse dependency on that module.
