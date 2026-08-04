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
  ignored `artifacts/migration/private/`;
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

Run the blocking completeness gate:

```bash
USL_ONLINE_DUMP_DIR=/absolute/path/to/usl-online-dump \
ACCOUNTING_COMPAT_COMPOSE_PROJECT=codex-migration-audit \
make migration-source-gate
```

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

## Deterministic reconstruction

`make target-reconstruct` restores the source package, runs the whole-source
gate, creates a clean target, replays Accounting, restores Projects, validates
both, removes the temporary Projects migration module, and applies target-only
configuration. It is intentionally blocked while any populated business scope
is incomplete. This prevents Accounting-and-Projects parity from being
mistaken for complete company migration.

Every stage must be idempotent and must bind its run to `source-<first 12
characters of dump SHA-256>`. Project restoration previously used a constant
snapshot label; it now derives that identity from `dump.sql` for both import
and validation.

## Current audited perimeter

For source dump `e1d95464d1ff633ec0db112cef50a20463f746abe94d05e5749d781b1f79cdd9`,
the audit found 214 populated persistent models and 90 populated relation or
unmapped tables. It verified 2,312 referenced filestore objects across 1,774
files without an integrity error.

Accounting, global identity, Product Master, HR, and Projects have implemented
translation stages.
The gate remains blocked—correctly—on collaboration history, unscoped
attachments, Documents, Knowledge, Sign, user preferences,
sales/marketing configuration, Studio data, and source AI
configuration. These are engineering migration gaps, not approved exclusions.

The HR stage restores the full native Community perimeter: employees, all
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

The identity stage restores all source contacts, users, company memberships,
supported access groups, contact categories, industries, and bank accounts. It
maps the Online administrator to the Pocket-managed `valentin` target identity;
built-in runtime users remain native. Passwords, TOTP seeds, API keys, sessions,
and OAuth state are never selected. Group identities that belong to still-open
Documents, Sales, or Sign scopes remain explicitly deferred in the identity
evidence rather than being silently dropped.

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
