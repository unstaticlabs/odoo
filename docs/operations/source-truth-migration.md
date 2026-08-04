# Source-truth migration

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

The Accounting harness resolves `--source-dir` once and exports that absolute
path to every Compose child. Host validation and the read-only container mount
therefore cannot silently select different source packages.

## Whole-source coverage ledger

`migration/source_truth/coverage.json` is the executable migration perimeter.
Every populated persistent source model and every populated relation or
unmapped table must resolve to one declared scope. Each scope states whether
the source is translated to native Community records, archived, recomputed
from version-controlled product configuration, or deliberately not copied.

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

For the current source, all 2,322 attachment rows are classified and all 2,312
stored references pass path, size, and SHA-1 compatibility checks. There are no
unowned rows. Accounting and Projects already restore their scoped evidence.
Identity, Product Master, and HR restore all 32 high-resolution user-authored
images byte-for-byte through native ORM fields; Odoo regenerates their smaller
variants. The Documents stage archives every legacy Documents original and
unassigned enterprise evidence file through the supported Paperless API, with
byte-for-byte read-back and preview checks. Sign, Knowledge, preference, AI,
and collaboration actions continue to keep the attachment gate blocked until
their own stages pass.

## Deterministic reconstruction

`make target-reconstruct` restores the source package, runs the current
Distribution gate, creates a clean target, replays Accounting, installs the
Documents security model, restores identity, Product, HR, Projects, Paie TESE
and Platform Billing, rebuilds the Paperless archive, removes every temporary
migration module and its allow-listed physical provenance columns, then
applies target-only configuration. It is blocked while any shipped scope is
incomplete. The strict
whole-source gate separately prevents this product claim from being mistaken
for delivery of every Online application.

The main checkout owns the default Compose project. A linked worktree must
pass a dedicated `COMPOSE_PROJECT` and non-conflicting published ports. Every
reconstruction helper verifies existing containers' Compose project and
working-directory labels before stopping or changing them.

Every stage must be idempotent and must bind its run to `source-<first 12
characters of dump SHA-256>`. Project restoration previously used a constant
snapshot label; it now derives that identity from `dump.sql` for both import
and validation.

## Current audited perimeter

For source dump `e1d95464d1ff633ec0db112cef50a20463f746abe94d05e5749d781b1f79cdd9`,
the audit found 214 populated persistent models and 90 populated relation or
unmapped tables. It verified 2,312 referenced filestore objects across 1,774
files without an integrity error.

Accounting, global identity, Product Master, HR, Projects, Paie TESE, Platform
Billing and the Paperless Documents archive have implemented translation
stages. The current Distribution gate passes. The strict whole-source gate
remains blocked—correctly—on collaboration history, unscoped attachments,
Knowledge, Sign, user preferences, sales/marketing configuration, Studio data
and source AI configuration. These are explicit future product scopes, not
silently copied or represented as current product parity.

### Platform Billing stage

Platform Billing runs after Accounting, Projects and Paie TESE and before the
Documents archive. The temporary importer links source platforms, sessions and
payouts to the already reconstructed native journal entries. It does not
recreate those entries or import the source suggestion cache: suggestions are
recomputed from current bank data. A repeated import must retain the same
application and ledger digests. Finalization uninstalls the importer and
removes its allow-listed physical source columns before the ordinary product
registry is accepted.

### Documents archive stage

`make documents-restore` installs the delivered Documents modules and replays
the complete source Documents perimeter into a separately managed Paperless
3.0.4 archive. On an existing reconstructed target, it first upgrades the
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
- the three qualified text containers rejected by Paperless 3.0.4—the generated
  FEC ZIP, an accounting XML, and calendar evidence—remain byte-for-byte Odoo
  operational attachments and are checksum-linked to deterministic, searchable
  PDF archive representations;
- unsupported files are retained byte-for-byte in a visible failed migration
  quarantine and keep the stage blocked.

The standalone runner defaults to the isolated `codex-migration-full` project
and Paperless port `28010`; it refuses development/QA projects and reserved
ports. Canonical `odoo_dev` is accepted only through the guarded
`target-reconstruct` orchestration, which resets its disposable Paperless
archive before replay. A second non-resetting rehearsal must reuse every
checksum root and relationship. Complete run evidence is dump-SHA-bound and
stored outside the delivered database.

The qualified full import and validation baseline reconciled 567 source
Documents identities and 9 unassigned evidence files into 548 checksum roots,
with 0 failures and 9 roots in Trash. It restored 745 exact business
relationships (363 accounting entries, 359 Contacts, 14 employees, and 9
Paie TESE records), preserved all 548 source-added timestamps, and retained one
unsupported authoritative original in Odoo alongside its searchable Paperless
representation. The current run manifest reports exact
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
