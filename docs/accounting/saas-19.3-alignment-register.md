# SaaS 19.3 alignment register

## Frozen inputs

- Upstream: `upstream/saas-19.3` at
  `efb98f932f3a568ce550a26ebde06da0e14e65d3` (23 August 2026).
- Previous Distribution: `19-usl` at
  `627e0e52995de4a93f3c4e55db545bbc3d1c11c7`, preserved as
  `archive/19-usl-pre-saas-19.3-20260824`.
- Online source: Odoo `saas~19.3.1.3`, dump SHA-256
  `ad313e28586fafa27a4f6a266df57080456613dff1c8c2c6d7e012732bf633b1`.
- Multi-company source: `a9d27c4b8f164142f9d120a41b15c29d3b76b2e3`, preserved as
  `archive/feat-multicompany-accounting-pre-saas-19.3-20260824`.

Source and target use the same Odoo generation. Reconstruction translates the
Online Enterprise data into native Community, pinned OCA and USL product
records; it is not an in-place database upgrade.

## Integration decisions

- The 19.2 and 19.3 upstream histories were not merged. The verified USL
  final-state delta was replayed on the frozen 19.3 upstream commit.
- The custom fiscal-year sequence and resequencing corrections remain because
  19.3 does not provide equivalent behavior.
- Multi-company is part of the qualified Distribution. Stable operational
  models, tables and XML IDs remain unchanged.
- Active runtime and reconstruction identifiers use 19.3. Historical
  `saas~19.2.*` module migration directories and the 19.2 alignment record are
  retained as installed-version evidence.
- OCA modules remain pinned to reviewed 19.0 commits. Their SaaS compatibility
  adaptations live under `oca-patches/saas-19.3/` and are reapplied
  deterministically.
- SaaS 19.3 replaced `account.group` with parent accounts before the pinned
  OCA financial reports adopted that hierarchy. `usl_accounting` therefore
  retains the stable prefix-group model as a tested compatibility bridge; it
  can be removed only after OCA reports and reconstructed hierarchy parity use
  the native model.
- Both electronic-invoice live guards remain disabled throughout migration and
  qualification.

## Qualification record

The aligned product passed a clean installation with 317 post-install tests,
two consecutive upgrades, the complete pinned OCA reconciliation suite (49
backend tests plus desktop and mobile browser suites), and the USL desktop and
mobile frontend suites.

The fresh reconstruction of the frozen Online source produced:

- 2 companies, 5,425 accounting moves and 12,991 lines;
- 5,258 posted moves, with debit and credit balanced and no duplicate source
  representation;
- 432 expenses, 113 payments, 3,095 bank transactions, 1,340 full and 2,861
  partial reconciliations;
- 3 native assets, 31 linked posted depreciation moves and 91 schedule lines;
- 3 employees, 4 source HR versions and 13 employee types;
- 18 projects, 1,910 tasks, 20,945 messages, 8,328 tracking values, 2,224
  followers and 44 project attachments;
- 10 TESE payslips and accounting entries, 10 payroll PDFs and 4 profiles;
- 4 platforms, 3 billing sessions, 31 payouts and all 51 linked accounting
  moves, with an unchanged ledger digest.

Accounting import, validation and the TESE and Platform Billing restorations
were each repeated without changing their business counts or digests. The
source/target current-period debit and credit are both EUR 1,746,386.67, with
no account, journal or profit-and-loss difference. Historical source sequence
exceptions are preserved exactly rather than silently resequenced.

The only transformation notes are explicit compatibility translations: nine
Enterprise expense states map from `in_payment` to the Community `paid`
state, and two stale stored untaxed values are recomputed from their source
lines. Legacy Platform Billing cache statuses remain external migration
evidence and do not become product records.

The local target deliberately used the `documents-smoke` profile rather than
waiting for the complete 645-group Paperless ingestion. Its eight deterministic
groups cover both companies, Accounting, HR, access rules, PDF, image, Tika,
Trash, duplicate and unassigned cases; all eight archived successfully with no
processing or failed item. This is sufficient for local development and is
explicitly not release evidence. Full OCR and archive parity remain an
on-demand final migration, pre-production and recovery gate.

Multi-company acceptance passed for both reconstructed companies. It proved
EUR 2,900,936.82 of balanced combined posted debit and credit, isolated
company contributions, a complete USL MEDIA invoice/bill/payment/bank/expense
journey, Prosper's zero access to USL MEDIA records, and 1,949 aligned
provider-owned ECB rates per company. The finalizer mirrors existing ECB rows
offline; it does not retrieve rates or overwrite manual exceptions.

The reconstruction initially exposed 12 target-only Project tasks: four native
onboarding todos created while restoring users and eight future occurrences
created while restoring already-closed recurring tasks. The migration now
suppresses both target conveniences and rejects any target-only Project task.
Focused identity and recurrence regression tests pass. A fresh final
reconstruction must confirm the product total remains the 1,910 source tasks.

The complete Paperless archive, pre-production and coordinated recovery gates
remain outstanding. The 19.2 register remains historical and must not be edited
to describe 19.3 evidence.

## Upstream ancestry refresh: 26 August 2026

The Distribution refreshed its official `saas-19.3` ancestry without changing
the generation or rewriting the original frozen alignment evidence above:

- Distribution base: `f302ae6cdb43b47e1bb2c705e1f4f716a27ce7d5`;
- previous upstream: `efb98f932f3a568ce550a26ebde06da0e14e65d3`;
- refreshed upstream: `aef56898d9ea5a97948af04c03ae101d17b8b4a3`;
- upstream delta: 106 commits, 1,013 files, 19,857 insertions and 10,697
  deletions;
- upstream migrations and runtime dependency changes: none; the only manifest
  change adds demo warehouse data for the Indian localization;
- overlapping downstream/core file: `addons/account/models/account_move.py`.

The merge completed without textual conflicts. In the overlapping file,
upstream removed an obsolete invoice-line onchange ordering workaround while
the independent USL fiscal-year sequence adaptation remained intact. The
matching `addons/account/wizard/account_resequence.py` adaptation also remains:
the refreshed upstream still has no equivalent company-governed extension
point. Removing either downstream patch was therefore rejected in favor of
retaining the documented fiscal-year boundary contract and its focused tests.

The upstream delta is translation-heavy and also changes Accounting, ORM date
handling, Mail and attachment storage, authentication, HR, Projects, stock,
POS, Sales, Website and localization behavior. The refresh therefore requires
focused upstream and USL module tests, clean-install and partial Documents QA,
a fresh product reconstruction, product/migration boundary checks and the
French catalogue and user-documentation gates. The shared full-QA cache is not
refreshed by a linked worktree; that release-only gate remains separate from
this integration candidate.
