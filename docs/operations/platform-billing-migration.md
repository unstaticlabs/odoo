# Platform billing historical restoration

## Purpose and order

Run this one-shot downstream stage only after Accounting reconstruction has
restored native companies, users, partners, products, journals, currencies,
analytics, the 51 generated moves, bank statement lines and attachments.

The temporary `usl_platform_billing_restore` add-on is mounted only in the
`platform-billing-migration` Compose profile. It reuses the temporary
Accounting source-identity mixin while reconstruction is active, extends
product records with source traces, writes evidence outside Odoo, and is
uninstalled during finalization. Finalization also removes its allow-listed
physical provenance columns before the product registry is accepted.

The retired bootstrap is identified by SHA-256
`a7617a282cb812ae051f41b5a6c15047c950bf3e8b85ef3a4014757345053791`.
Approved source revisions are identified by SHA-256. The original baseline is
`ee6d9789224a7a8ba1d9048c813939a41ffed77e13fad3b65be246cfc3f83c9e`.
The refreshed full export used by current `odoo_dev` is
`395cc8b950b592035fed41dedf0072f3487e18f10b4010f939331a5e5b51e69f`.
The importer records the exact revision used and rejects every unapproved
hash. Platform Billing counts and accounting links are revalidated for each
approved export; a matching older baseline is not assumed.

## Isolation

Use a dedicated Compose project such as `usl-odoo-fp-qa`, dedicated volumes,
and free ports such as `19469/19472`. The harness refuses any project name that
does not start with `usl-odoo-fp-`. It verifies the repository working
directory and Compose labels before operations.

The source dump defaults to the ignored checkout-local `usl-online-dump/` and
is mounted read-only. Set `USL_ONLINE_DUMP_DIR` to the approved absolute
external package path for rehearsal or production use. The source database
must already be running inside the dedicated project; this stage does not
start, stop or alter it. Both electronic-invoice live flags are forced to `0`.

## Isolated Accounting reconstruction

Run Platform Billing only inside a runtime resolved by `migration/manage`.
The recorded runtime fixes the project, source, database, ports, working
directory, and disabled live integrations. Never override those values to work
around a guard failure; inspect the recorded resource labels and IDs instead.

## Platform restoration

After Accounting validation, `migration/manage` runs install, import,
validation, idempotence, finalization, and product-boundary stages in order.
Evidence is written to the runtime's ignored private directory. These stages
are not standalone public commands.

Repeat the complete Accounting reconstruction and this downstream sequence on
the exact validation database and then `odoo_dev`, because the product install
graph and importer mappings changed.

## Blocking preflight

The importer stops before platform/session/payout writes when it finds missing source
identities, duplicate payout references, invalid bank allocations or
amounts/rates, cross-company platform ambiguity, missing generated moves,
attachment checksum differences or canonical digest drift. Shared bank links
are preserved as pooled receipt allocations. It never silently merges an
untraced target platform.

Two source products used only by the historical platform application are not
part of the general Accounting product scope. The dedicated importer restores
those products once, with source identities and resolved native account/tax
dependencies; it never recreates any of the 51 already-imported moves.

Legacy bank-candidate rows are deliberately excluded. Current transient
candidates are regenerated from operational bank statement lines.

## Rehearsal evidence

The 2026-08-04 isolated `odoo_dev` rehearsal passed from the current approved
source export. It restored 4 platforms, 3 sessions, 31 payouts and links to all
51 generated moves; 72 legacy candidate rows were excluded. Two successive
imports produced application digest
`4ef18172a624fd3f8f456a7aa0d437681c38f401f82b372969c62c124db92916`.
Finalization preserved the 4/3/31/51 business counts and final-product digest,
uninstalled the temporary module and left no migration residue.

The reconstructed Accounting target contained 5,067 moves, 11,941 lines,
3,062 bank transactions, 2,595 partial reconciliations and 1,267 full
reconciliations. The original posted benchmark slice remained balanced at
debit and credit `1,064,045.02`; no posted move was unbalanced.

## Acceptance

- All source counts match target counts dynamically.
- At least four platforms and three sessions are restored.
- All 51 generated moves have complete session/platform/payout relations.
- Their canonical names, dates, states, currencies and debit/credit lines have
  identical before/after digests.
- Repeated import produces the same application digest.
- Finalization preserves business counts/digests, uninstalls the temporary
  add-on, and leaves no migration models, fields, XML IDs, menus or add-on path
  in the product database.

## Troubleshooting

- **Missing mapping:** rerun only the Accounting stage that restores the named
  dependency; do not recreate the native record in this importer.
- **Duplicate identity/reference:** correct the source mapping or target trace
  conflict and rerun preflight. Do not merge automatically.
- **Invalid pooled bank allocation:** correct the payout shares so their total
  does not exceed the native bank transaction amount.
- **Attachment checksum mismatch:** verify source filestore availability and
  rerun the Accounting attachment stage.
- **Digest drift:** stop. Compare the generated move/line evidence before any
  further operation.
- **Source service missing:** restore the source in the dedicated project. Do
  not reuse the canonical shared source container.
