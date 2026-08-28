# Source-truth migration

Final source freeze, portable candidate compilation and cut-over are documented
in [Portable production migration candidate](portable-production-migration.md).
Candidate build remains blocked until this inventory and the attachment ledger
both report complete whole-source coverage.

The migration tool is a maintained repository deliverable under `migration/`
and `scripts/`. It is not part of the Odoo Community product, is not present on
the normal add-ons path, and must leave no migration models, menus, fields, or
technical provenance in a finalized product database.

## Safety boundary

The input is the preserved Odoo Online package:

- `dump.sql` is restored only into the dedicated `accounting-source-db`
  service;
- the source database is queried with PostgreSQL read-only transactions;
- the source filestore is mounted read-only;
- regulatory live flags remain `0` throughout reconstruction;
- private inventories, paths, checksums, and record evidence are written below
  ignored `artifacts/migration/private/` or the dump-bound
  `accounting_compat/private/snapshots/source-*/evidence/` directory;
- no shared Docker project is selected implicitly. Set an isolated Compose
  project or an exact source container.

Never start target Odoo against the source database. Never edit source rows to
make an importer pass.

Canonical reconstruction resolves `USL_ONLINE_DUMP_DIR` once, exports the
absolute path to every migration stage, and uses that same path for host
validation and the read-only container mount. If the variable is unset, local
development uses the ignored checkout-local `usl-online-dump/`; production
must supply the approved external package path.

Two alternatives were considered: retaining per-script maintainer-specific
defaults, or requiring every caller to repeat `--source-dir` for every stage.
Both permit path drift between host validation, audit tools and Compose. The
single exported environment contract is used instead, with a portable local
default and an explicit production override.

## Whole-source coverage ledger

`migration/source_truth/coverage.json` is the executable migration perimeter.
Every populated persistent source model and every populated relation or
unmapped table must resolve to one declared scope. Each scope states whether
the source is translated to native Community records, archived, recomputed
from version-controlled product configuration, or deliberately not copied.
The inventory also records every stored or Studio/manual field belonging to a
populated model. The dump-bound private gap report groups those fields with
the exact model and relation-table counts under the delivered or blocked scope;
it does not mistake a scope contract for value-level parity, which remains the
responsibility of that scope's importer and validation evidence.

The gate fails when:

- a populated model or table has no declared treatment;
- a populated scope does not yet have an implemented migration stage;
- a source attachment points to missing, unsafe, size-mismatched, or
  checksum-mismatched filestore data;
- the restored source cannot be proven read-only.

Run an audit without changing either database:

```bash
USL_ONLINE_DUMP_DIR=/absolute/path/to/usl-online-dump \
ACCOUNTING_COMPAT_COMPOSE_PROJECT=codex-migration-audit \
make migration-source-inventory
```

Write the human- and machine-readable private gap report without weakening the
strict gate:

```bash
USL_ONLINE_DUMP_DIR=/absolute/path/to/usl-online-dump \
ACCOUNTING_COMPAT_COMPOSE_PROJECT=codex-migration-audit \
make migration-source-report
```

The files are written beside the inventory as
`source-migration-gap-report.md` and `source-migration-gap-report.json`. A
blocked scope means this commit has no qualified lossless destination for that
data and therefore cannot produce a production candidate.

Run the strict whole-source completeness gate:

```bash
USL_ONLINE_DUMP_DIR=/absolute/path/to/usl-online-dump \
ACCOUNTING_COMPAT_COMPOSE_PROJECT=codex-migration-audit \
make migration-source-gate
```

Canonical reconstruction uses `scripts/migration-source-truth product-gate`.
That gate is equally strict for every scope shipped by the current
Distribution, while still reporting populated future application scopes as
deferred. The strict `gate` remains the acceptance test for any future claim
that every source application has been delivered.

The August 2026 source also contains configured Inventory, Manufacturing and
Quality applications, but no stock moves, move lines, pickings, quants,
valuation layers, manufacturing orders, bills of materials, quality points or
quality checks. Product restoration now translates and validates the one
warehouse, 23 locations, six routes, seven rules, 11 operation types, costing
configuration, and these exact zero transaction baselines. This completes the
source `inventory_manufacturing` scope without claiming that unrecorded physical
stock is zero. An approved dated physical count remains an external release
input and may only become a native opening inventory adjustment.

Restore the source first with the same isolated project when necessary:

```bash
USL_ONLINE_DUMP_DIR=/absolute/path/to/usl-online-dump \
ACCOUNTING_COMPAT_COMPOSE_PROJECT=codex-migration-audit \
scripts/accounting-compat source-restore
```

The private inventory is bound to the full dump SHA-256. It includes counts for
all source models and tables and a deterministic SHA-256 roll-up of every
filestore object. Historical Odoo SHA-1 values are checked only because they
are the checksums stored by Odoo; the stronger roll-up identifies the evidence
package itself.

Unreferenced filestore objects are counted and hashed, not deleted. They may be
database leftovers, but destructive cleanup is never part of reconstruction.

## Attachment disposition ledger

The source-wide inventory proves the filestore is intact. The separate
attachment ledger then accounts for every `ir.attachment` source identity and
every action that its bytes require. Run it against the same isolated source:

```bash
USL_ONLINE_DUMP_DIR=/absolute/path/to/usl-online-dump \
MIGRATION_SOURCE_CONTAINER=codex-migration-audit-accounting-source-db-1 \
make attachment-ledger
```

`make attachment-ledger-gate` is deliberately blocking until all downstream
actions have passed. The private JSON and CSV evidence are dump-SHA-bound and
record source identity, filename, owner, size, checksum, Documents identities,
and required actions. They distinguish operational attachments, Paperless
originals, signed evidence, chatter files, native images, generated thumbnails
and assets, URLs, dashboards, unassigned evidence, and credential material.
One row may require both an operational Odoo copy and a Paperless archive copy;
the ledger records both rather than incorrectly treating this legally useful
duplication as a migration duplicate.

For the current 24 August 2026 source, all 2,601 attachment rows are classified
and all 2,591 stored references pass path, size, and SHA-1 compatibility
checks. There are no unowned rows. Accounting and Projects already restore
their scoped evidence.
Identity, Product Master, and HR restore all 32 high-resolution user-authored
images byte-for-byte through native ORM fields; Odoo regenerates their smaller
variants. The Documents stage archives every legacy Documents original and
unassigned enterprise evidence file through the supported Paperless API, with
byte-for-byte read-back and preview checks. The genuine strategy PDF referenced
by an experimental AI source record is added to that archive as private,
needs-review business evidence; the AI index and configuration are not copied.
The Sign stage preserves every completed request, participant, business artifact
and history item as an external record; it deliberately does not recreate
reusable signature images. The final Collaboration and Preferences stages close
chatter, saved-filter, personalized Home, and dashboard dispositions. Valentin's
Home is rebuilt from the restored administrator mapping, source-favorite Project
relations, and the approved saved-filter perimeter using typed target actions;
no source identifiers remain in the delivered Home records. Knowledge
attachments are explicitly discarded with the approved demo-content
disposition. Three dashboard
definitions are recomputed from installed target modules and six unsupported
Enterprise dashboard payloads receive explicit not-copied evidence. No source
attachment action remains pending.

## Deterministic reconstruction

`make migrate-production SOURCE_SHA=<exact-dump-sha256>` restores the source
package, runs the strict source-wide and attachment gates, creates a clean
target, replays Accounting, installs the
Documents security model, restores identity, Product, HR, Projects, Paie TESE
and Platform Billing, rebuilds the Paperless archive, restores source-wide
external Sign records and Collaboration history, removes every temporary
migration module and its allow-listed physical provenance columns, then
applies target-only configuration. It is blocked while any shipped scope is
incomplete. The strict
whole-source gate runs before the target reset. The command refuses a partial
profile, checkpoint reuse, a dirty checkout or an unconfirmed source package.
It therefore cannot report a production migration while any Online scope or
attachment still lacks a final disposition.

The production command deliberately resets Paperless and is the authoritative
final-migration proof. Development may run `make target-reconstruct-product`
for a fresh reconstruction of the scopes currently shipped by the
Distribution, or
`make target-reconstruct-reuse-documents`: it rebuilds `odoo_dev` but retains
the already qualified Paperless archive when its private checkpoint proves the
runtime compatibility contract and archive-root state are unchanged. A newer
dump or compatible importer change runs through the complete idempotent
Documents reconciliation and ingests only missing checksums. This is a
content-addressed ingestion cache, not accepted migration evidence by itself:
the importer still recreates Odoo relationships and verifies every binary,
preview, catalog value and permission. Runtime incompatibility or archive
drift rejects reuse rather than guessing.

The main checkout owns the default Compose project. A linked worktree must
pass a dedicated `COMPOSE_PROJECT` and non-conflicting published ports. Every
reconstruction helper verifies existing containers' Compose project and
working-directory labels before stopping or changing them.

Before any source restore or target reset, reconstruction also inspects the
shared Docker runtime. Production rejects every foreign running Compose
project. Development is fail-closed when Docker has less than 12 GiB and a
foreign project is running, because an 8 GiB shared VM demonstrably OOM-killed
the otherwise qualified atomic Accounting import. The preflight never stops a
foreign project. Its owner must quiesce it or the host must allocate more
Docker memory. `USL_MIGRATION_ALLOW_CONCURRENT_DOCKER=1` is an explicit
development-only escape hatch and is forbidden in production.

Every stage must be idempotent and must bind its run to `source-<first 12
characters of dump SHA-256>`. Project restoration previously used a constant
snapshot label; it now derives that identity from `dump.sql` for both import
and validation.

## Current audited perimeter

For source dump `ad313e28586fafa27a4f6a266df57080456613dff1c8c2c6d7e012732bf633b1`,
exported on 24 August 2026 from Odoo Online `saas~19.3`, the audit found 232
populated persistent models and 93 populated relation or unmapped tables. It
verified 2,591 referenced filestore objects across 2,029 files without an
integrity error.

Accounting, global identity, Product Master, Inventory/Manufacturing
configuration, B2C commerce, HR, Projects, Paie TESE, Platform Billing,
Collaboration, signed evidence and the Paperless Documents archive have
implemented translation or archive stages. The strict gate covers all 19
audited scopes and dispositions 226,836 source records with zero blocked
records, relation rows or stored fields.

The former incomplete scopes are now closed explicitly. AI configuration and
Sales/Marketing configuration are discarded as experiments or default setup.
Studio implementation metadata is not copied; its maintained Distribution
behavior and expense-matching candidates are recomputed. Seven source-backed
saved filters are migrated;
six native filters and two exports are recomputed, and AI/marketing exports are
discarded. Nine standard spreadsheet-dashboard payloads are recomputed where a
native target exists or rejected where the Enterprise dashboard module is not
part of the Distribution. The one genuine private strategy PDF is retained
byte-for-byte as a restricted manager-only `usl.document`, without copying its
obsolete AI index, embeddings or agent configuration. There are no pending
attachment actions.

Knowledge is not a Distribution product. Its 66 source messages are default or
demo content and are deliberately not copied. Another 554 omitted Collaboration
messages are generated technical configuration notes without customer or
operational narrative. The 199 messages attached only to 83 retired Documents
folders and the default tutorial URL are also retained solely in sealed
migration evidence; their file move/trash history is already represented by
the final binary archive. Migration must not generate placeholder PDFs for
these nodes. These decisions do not add permanent compatibility models or a
shadow archive inside Odoo.

The separate physical opening-stock evidence item remains a B2C operational
go-live prerequisite even though it is not a fact contained in the source
database. The migration correctly creates no unsupported historical stock
moves, quants or valuation layers.

The guarded Sign stage, artifact contract and final-state checks are documented
in [Restore Odoo Online Sign records](sign-online-restoration.md).

### B2C commerce stage

B2C creates its canonical records after Accounting and Product restoration,
then finalizes relationships only after Accounting, Product, Projects and full
Documents restoration. It parses 39 checksum-locked dump files directly from
the read-only source filestore plus one checksum-locked private Medusa line
export in the same canonical source package. It creates immutable restricted
evidence and requires all 40 files in final Documents. The source has zero
native sales, payments and stock operations, so the stage does not manufacture
any. It fingerprints Accounting, reconciliations, bank data, native payment
records, named supplier documents, Sales, Purchase, Stock and product cost
history before and after import.

The current evidence baseline is 304 canonical orders, 457 line rows (235 Etsy
plus 222 Medusa), 1,821 payment/refund/fee events, 261 fulfilment/COGS rows,
2,893 immutable evidence rows, 109 aliases and 81 monthly session scopes. Nine
aliases are verified by exact canonical internal reference and 100 are
explicitly not applicable; none is unexplained pending. All 180 critical moves
have verified provider/month session links, 81 have unique bank links, and 14
direct identifier relationships cover 10 events. The Odoo Online dump and its
filestore remain primary; the Medusa sold-item file is explicitly post-dump
supplemental provider evidence and never masquerades as a source-database
record. Repeat import must preserve those counts and the same protected
fingerprint. Detailed file and column dispositions live in
`migration/b2c_restore/source-field-matrix.md`.

### Platform Billing stage

Platform Billing runs after Accounting, Projects, the Documents archive, the
idempotent post-Documents B2C refresh, and Paie TESE. The temporary importer
links source platforms, sessions and payouts to the already reconstructed
native journal entries. It does not recreate those entries or import the
source suggestion cache: suggestions are recomputed from current bank data. A
repeated import must retain the same application and ledger digests.
Finalization uninstalls temporary modules in reverse dependency order, keeps
the complete temporary add-ons path available until the last uninstall, then
runs Platform Billing's schema scrub and every product-only boundary check.
This prevents an ordinary Odoo registry from starting while a temporary module
is still installed. The final scrub removes allow-listed source columns before
the ordinary product registry is accepted.

### Documents archive stage

`make documents-restore` installs the delivered Documents modules and replays
the complete source Documents perimeter into a separately managed Paperless
3.0.5 archive. On an existing reconstructed target, it first upgrades the
accounting parent module so stored business views are current before the
Documents accounting extension is revalidated. It is not a filestore copy:

- one root is created per exact binary checksum, while every duplicate source
  identity remains in the sealed migration manifest;
- source tags, folder meaning, accounting journal rules, correspondent
  Contacts, access policy, lifecycle, company, inactive state, and originals
  are preserved or explicitly translated into user-facing tags, document
  types, correspondents and business links;
- the original Documents creation timestamp is the rebuilt app's **Added**
  date. Paperless's own read-only `added` value separately records when the
  archive engine received the reconstructed item;
- accounting moves, source/move Contacts, recognized archive institutions and
  employee folders become explicit Odoo links when the target record is
  deterministically mapped. The source contains no document directly in its
  configured project folders; a future qualified dump with one is rejected
  until that mapping has been implemented;
- unused source tag definitions and superseded accounting rules remain in the
  sealed manifest but are pruned from the live Paperless catalog. Source
  identifiers, folder paths and migration provenance never become product
  custom fields or menus;
- legacy public bearer links are revoked by policy and only their hashes remain
  in private evidence;
- Paperless originals are downloaded and SHA-256 verified; received PDFs and
  generated searchable representations must preview as valid PDFs, while other
  supported formats must return a non-empty typed preview; API v10 and actual
  permission read-back must pass, and successful
  runs may not add an Odoo binary attachment;
- incremental BGE-M3 work is deferred only during governed bulk replay. Normal
  runtime is restored before one supported migrate/update/compact pass, and
  both the Paperless task inventory and vector/document parity must pass before
  the stage can seal its checkpoint;
- the three qualified text containers rejected by Paperless 3.0.5—the generated
  FEC ZIP, an accounting XML, and calendar evidence—remain byte-for-byte Odoo
  operational attachments and are checksum-linked to deterministic, searchable
  PDF archive representations;
- unsupported files are retained byte-for-byte in a visible failed migration
  quarantine and keep the stage blocked.

After the legacy Documents archive is restored, the migration service runs the
delivered native-attachment bridge once across the final business records. It
reuses Paperless roots by checksum, adds missing task/project, accounting,
expense, TESE and Platform Billing relationships and classification, and emits
an external result classifying every durable attachment as archived or
explicitly excluded. Pending, failed, duplicate or unaccounted eligible files
block full reconstruction. Repeating the pass creates neither another archive
root nor another business consequence.

The standalone runner defaults to the isolated `codex-migration-full` project
and Paperless port `28010`; it refuses development/QA projects and reserved
ports. Canonical `odoo_dev` is accepted only through the guarded
`target-reconstruct` orchestration, which resets its disposable Paperless
archive before replay. A second non-resetting rehearsal must reuse every
checksum root and relationship. Complete run evidence is dump-SHA-bound and
stored outside the delivered database.

The guarded development reuse command preserves those volumes across the
clean Odoo reconstruction only after verifying
`artifacts/migration/private/checkpoints/<compose-project>/paperless-ingestion.json`.
The checkpoint contains hashes and counts only, is ignored by Git, and is
atomically replaced only after a complete successful Documents validation.
It never bypasses the importer or becomes product database state.

The qualified 18 August 2026 full import and validation baseline for the prior
dump reconciled
657 source Documents identities and 9 unassigned evidence files into 638
checksum roots, with 0 failures and 2 source roots in Trash. It restored 863 exact
business relationships (427 accounting entries, 411 Contacts, 15 employees,
and 10 Paie TESE records), preserved all 638 source-added timestamps, and
restored one missing native operational attachment (1,602 before, 1,603
after). One newly exported supplier
invoice declares corrupt base64-like bytes as PDF; its exact original remains
in Odoo and its deterministic searchable representation is explicitly
classified in the archive evidence. The current run manifest reports exact
relationship totals by model, derived classification totals, every excluded
empty catalog value, preservation of every source-added timestamp, and removal
of all earlier `Legacy Odoo` custom fields. A second full run must produce the
same archive/root/link/catalog counts without creating another attachment or
business relationship. For dump SHA-256
`e1d95464d1ff633ec0db112cef50a20463f746abe94d05e5749d781b1f79cdd9`, the
clean import evidence SHA-256 is
`07b41266218444060609c797c9665d4e63400603a88e8dc8edefc700fa156aa3`; the
non-resetting reuse proof is
`24ff482cfaa855d1ed44748571c59b4884fb674bfadc325a37786e664082de38`.
Paperless's archive sanity checker reported no integrity error. Browser
acceptance rendered both pages of an actual restored PDF, followed its restored
vendor-bill link, and verified that the native Odoo search facet and selected
document survive returning from that record.

The HR stage restores the full native Community perimeter: employees, their
original high-resolution images, all
effective-dated employee versions (including an unassigned contract template),
resources, working calendars and their attendance intervals, departments,
jobs, work locations, contract and departure reference data, payroll structure
types, skills, skill levels, and résumé line types. It preserves private contact,
identity, compensation, bank-allocation, and employment fields through the ORM,
then proves that a non-HR internal user cannot read a private employee field.
`hr.employee.public` is a runtime SQL view and is deliberately recomputed rather
than copied as an independent data set. Chatter, attachments, Documents folder
identities, and Studio/TESE fields remain owned by their declared migration
scopes and are counted as delegated HR evidence rather than silently discarded.
The source contains one stale resource timezone that disagrees with its current
employee version. Odoo 19 computes the resource timezone from that version, so
the target deterministically uses the effective-dated employee value and records
the source disagreement in the run evidence instead of creating an ORM state the
target would immediately overwrite.
The identity stage necessarily creates a default calendar when it creates a
company. The HR stage replaces that generated placeholder with the restored
source calendar and removes it only after proving that no company, employee,
version, payroll structure, or leave still references it.

The identity stage restores all source contacts and their original
high-resolution images, users, company memberships,
supported access groups, contact categories, industries, and bank accounts. It
maps the Online administrator to the Pocket-managed `valentin` target identity;
built-in runtime users remain native. Passwords, TOTP seeds, API keys, sessions,
and OAuth state are never selected. Enterprise Documents manager/system
memberships map to the delivered Documents manager role because its security
model is installed before identity restoration. Sales and Sign group
identities remain explicitly deferred rather than being silently dropped.

Credentials and runtime state are the exception: passkeys, TOTP devices,
sessions, device logs, certificates, IAP credentials, tokens, and transient
signaling are deliberately not copied. Users are reconstructed separately and
must re-enroll through the target Pocket ID policy.

## Adding a migration stage

1. Inventory the source relationships and binaries without exposing private
   values in Git.
2. Choose a native Community model, an explicit archive representation, or a
   documented non-migratable treatment.
3. Implement the importer under `migration/`; do not add source extraction or
   migration provenance to `custom-addons/`.
4. Make source identity, ordering, retries, and duplicate handling
   deterministic.
5. Compare counts, stable identities, relationships, material field digests,
   and every copied binary checksum.
6. Test a second run and an interrupted/retried run.
7. Mark the scope `implemented` only when those checks pass on the current dump.
8. Run `make product-migration-boundary` after finalization.

Record transformations and honest non-equivalences. Matching row counts alone
is not proof: required business relationships, permissions, dates, chatter,
attachments, and legal originals must also resolve.
