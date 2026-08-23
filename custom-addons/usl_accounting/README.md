# USL Accounting Foundation

Technical module name: `usl_accounting`

This module owns shared operational Accounting extensions that are reused by
Controls, Reports and the compatibility product module:

- governed fiscal-year behavior;
- payment and partner suggestions;
- three-action foreign-currency settlement: native Add, exact Settle with
  native FX, and immediate-event payment-rate valuation on the existing bank
  move;
- bank matching and reconciliation compatibility;
- company-paid expense matching against unreconciled bank transactions;
- analytic measures and entry-direction safeguards;
- scoped read-only accounting evidence protection.

It extends native Odoo and pinned OCA models. It does not own reconstruction
models, source traces, report definitions, Controls, declarations, e-invoice
activation or normal Accounting menus.

Existing database XML IDs owned by `rebuild_account_migration` remain there
during the staged compatibility period. New foundation behavior—including the
foreign-currency settlement views, audit security and payment-widget
assets—belongs directly to this module. Do not add a reverse dependency on the
compatibility module.

## Company-paid expense matching

Accounting Managers can open a Draft, Submitted or Approved expense and use
**Find bank transactions**. The feature ranks at most five same-company bank
debits using amount, date, currency, vendor and reference facts. It shows those
facts rather than a confidence score.

Only an exact amount within the expense currency rounding can be selected.
After explicit confirmation, **Use and reconcile** runs the native expense
submit, approve and post methods, selects the exact outstanding line from the
native company payment and reconciles it through the pinned OCA bank-matching
API. Native duplicate review, analytic validation, lock dates and permissions
remain blocking. Any failure rolls back the complete request.

Candidate rows are operational and recomputable. They are not accounting
truth, do not run from a cron and never replace the native payment, journal
entry or reconciliation records.
