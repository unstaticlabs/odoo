# Platform billing historical restoration

## Purpose and order

Run this one-shot downstream stage only after Accounting reconstruction has
restored native companies, users, partners, products, journals, currencies,
analytics, the 51 generated moves, bank statement lines and attachments.

The temporary `usl_platform_billing_restore` add-on is mounted only in the
`platform-billing-migration` Compose profile. It extends product records with
source traces during reconstruction, writes evidence outside Odoo, and is
uninstalled during finalization.

The retired bootstrap is identified by SHA-256
`a7617a282cb812ae051f41b5a6c15047c950bf3e8b85ef3a4014757345053791`.
Approved source revisions are identified by SHA-256. The original baseline is
`ee6d9789224a7a8ba1d9048c813939a41ffed77e13fad3b65be246cfc3f83c9e`.
The refreshed full export used by current `odoo_dev` is
`e1d95464d1ff633ec0db112cef50a20463f746abe94d05e5749d781b1f79cdd9`.
Both contain the same Platform Billing slice (4 platforms, 3 sessions, 31
payouts, 72 legacy candidates and 51 generated moves). The importer records
the exact revision used and rejects every unapproved hash.

## Isolation

Use a dedicated Compose project such as `usl-odoo-fp-24ee`, dedicated volumes,
and free ports such as `19469/19472`. The harness refuses any project name that
does not start with `usl-odoo-fp-`. It verifies the repository working
directory and Compose labels before operations.

The source dump defaults to
`/Users/valentin/Code/odoo/usl-online-dump` and is mounted read-only. The source
database must already be running inside the dedicated project; this stage does
not start, stop or alter it. Both electronic-invoice live flags are forced to
`0`.

## Isolated Accounting reconstruction

Export all of the following values before the upstream Accounting
reconstruction. `ACCOUNTING_COMPAT_REQUIRE_ISOLATED_PROJECT` prevents an
accidental fallback to the canonical Compose project, while
`ACCOUNTING_COMPAT_VERIFY_COMPOSE_SCOPE` checks existing container project and
working-directory labels before every Compose operation.

```bash
export COMPOSE_PROJECT_NAME=usl-odoo-fp-24ee
export ODOO_SAAS_COMPOSE_PROJECT=usl-odoo-fp-24ee
export ACCOUNTING_COMPAT_COMPOSE_PROJECT=usl-odoo-fp-24ee
export ACCOUNTING_COMPAT_REQUIRE_ISOLATED_PROJECT=1
export ACCOUNTING_COMPAT_VERIFY_COMPOSE_SCOPE=1
export ACCOUNTING_COMPAT_SOURCE_DIR=/Users/valentin/Code/odoo/usl-online-dump
export USL_ONLINE_DUMP_DIR=/Users/valentin/Code/odoo/usl-online-dump
export ODOO_HTTP_PORT=19469
export ODOO_GEVENT_PORT=19472
export USL_EINVOICE_LIVE_ENABLED=0
export USL_EREPORTING_LIVE_ENABLED=0
```

Run the documented Accounting exact reconstruction and `odoo_dev`
reconstruction using that environment. Never unset the isolated-project
requirement to work around a guard failure; inspect the project labels and
working directory instead.

## Platform restoration commands

After Accounting validation passes, select the target database and run:

```bash
export PLATFORM_BILLING_TARGET_DATABASE=odoo_dev

make platform-billing-restore-install
make platform-billing-restore-import
make platform-billing-restore-validate
make platform-billing-restore-idempotence
make platform-billing-restore-finalize
make platform-billing-product-validate
```

`make platform-billing-restore` performs the full sequence, including a second
import/validation pass. Evidence is written to the ignored
`artifacts/platform-billing-restore/` directory.

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

The 2026-07-30 isolated rehearsal passed on both the exact validation database
and `odoo_dev`. Each target restored 4 platforms, 3 sessions, 31 payouts and
links to all 51 generated moves; 72 legacy candidate rows were excluded. Two
successive imports produced application digest
`ca766eff52149a543a2e243de08772d47f2b09389122b87d805355980b939b60`.
Finalization preserved the business counts and digest, uninstalled the
temporary module and left no migration residue.

The exact Accounting target contained 5,044 moves, 11,871 lines, 414 verified
attachments, 3,046 bank lines and 3,844 reconciliations. The original posted
benchmark slice remained balanced at debit and credit `1,064,045.02`.

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
