# Odoo Online Documents archive migration

This migration tool translates the complete legacy Odoo Online Documents
binary perimeter into the Paperless-backed Documents product. It lives outside
the normal Odoo add-ons path. The source database and filestore are always
read-only; private source values and run evidence are ignored by Git.

## What is migrated

- every binary-backed `documents.document` identity;
- every exact original, grouped into one Paperless root per byte-identical
  checksum;
- every legacy link to a migrated accounting record;
- legal company, owner, useful classification, source received date, lifecycle,
  explicit access history, and inactive/Trash state;
- unassigned enterprise files as visible `Needs attention` archive items;
- legacy public-link state as audit evidence, while deliberately revoking the
  old bearer tokens so the rebuilt Odoo authorization boundary cannot be
  bypassed.

The 77 legacy folders and their accounting/HR folder-tag settings are retained
in sealed evidence and translated into real Paperless tags, document types,
correspondents, record links, company policy, and the rebuilt app's
business-context rules. Folder paths and source identifiers are not copied to
the live product as custom fields, and the folder tree is not recreated as a
second filing system. The source's sole URL
document is the untouched upstream `documents` tutorial XML record, so it is
classified as recomputed distribution reference data rather than user content.

The received original is downloaded from Paperless and SHA-256 checked after
ingestion. Received PDFs and generated searchable representations must preview
as valid PDFs; other supported formats must return a non-empty preview whose
media type is recorded in evidence. The successful path asserts that
Odoo's attachment count does not increase, except for three qualified source
formats rejected by Paperless 3.0.5: one generated FEC ZIP, one accounting XML,
and one calendar evidence file. Each exact authoritative source remains an
operational Odoo attachment while Paperless holds a checksum-linked,
deterministic, searchable PDF representation. The Documents migration never
creates a second Odoo copy of an ordinary archive binary. An attachment already
restored as native business history (for example, a vendor-bill chatter
attachment) remains intact under its owning migration policy; Paperless keeps
the independently verified authoritative archive original.

The full source relationship, access, lifecycle, multilingual label, filename,
folder lineage, and checksum evidence is sealed outside the product database
under:

`accounting_compat/private/snapshots/source-<dump>/evidence/`

The source `documents.document.create_date` is preserved as the document's
received/added timestamp in Odoo. Paperless's supported API exposes its own
archive-ingestion `added` timestamp as read-only, so that separate operational
timestamp remains the time at which Paperless received the reconstructed
archive item. The user-facing Odoo **Added** date consistently uses the
preserved source timestamp and falls back to Paperless's timestamp only for
documents first received directly by Paperless.

Only tags that classify at least one migrated document remain in the live
catalog. Empty legacy rules are recorded in evidence and pruned. Folder and
accounting context may derive useful tags, document types, correspondents and
links, but the translator never invents an uncertain business relationship.

No legacy sharing token is written there in clear text; only its SHA-256 is
recorded.

## Deterministic runner

The runner refuses the main development and Documents QA projects and their
reserved ports. Its safe defaults are:

- Compose project: `codex-migration-full`;
- target database: `odoo_dev`;
- Paperless browser port: `28010`;
- Paperless: exactly `3.0.4`, API v10;
- e-invoice and e-reporting live flags: `0`.

The source database must already have been restored in the same isolated
Compose project by the canonical accounting source-restore stage. Run the full
stage with:

```bash
COMPOSE_PROJECT_NAME=codex-migration-full \
USL_ONLINE_DUMP_DIR=/absolute/path/to/usl-online-dump \
make documents-restore
```

Useful stage commands are:

```bash
make documents-restore-install
make documents-restore-import
make documents-restore-validate
make documents-restore-serve
make documents-restore-status
```

The focused `import` and `validate` stages are available only inside an active
reconstruction, before migration finalization removes the temporary source
bindings. They fail before changing Paperless when pointed at an already
finalized product database. To rebuild a finalized development target and
publish its reusable private QA seed, run `make qa-cache-refresh` instead.
The reconstruction preflights its checkout/project-specific Pocket ID
configuration before starting any database work, so stale environment ownership
cannot fail only after a long Accounting import.

If a QA cache refresh fails after exact Accounting validation, its final output
offers `make qa-cache-resume`. That command restores and checks the same source
package, reruns exact Accounting parity, and continues the downstream stages
without resetting or replaying the ledger. It fails closed when parity cannot
be proven; production migration always remains a complete fresh run.

Fresh development/QA archive builds use a bounded three-worker Paperless pool
to avoid serial OCR and metadata processing. Production remains conservative at
one worker unless the operator explicitly qualifies another value; accepted
values are one through four. Hydrating a qualified QA seed submits no OCR work.

`validate` intentionally executes the same idempotent contract as `import`.
It must reuse existing checksum roots, refresh authoritative Paperless
metadata, recheck originals, previews, permissions and links, and leave counts
unchanged.

## Development ingestion checkpoint

The canonical fresh path remains:

```bash
make migrate-production SOURCE_SHA=<exact dump SHA-256>
```

It resets Paperless and proves a complete ingestion from nothing, but proceeds
only when the source-wide and attachment gates are complete. To reconstruct
the currently shipped product scopes during development, use
`make target-reconstruct-product`. Repeated
development runs of the same qualified input may use:

```bash
make target-reconstruct-reuse-documents
```

This preserves only the Paperless volumes. It first verifies a private
checkpoint containing the compatible pinned Paperless/OCR runtime and
archive-root digest/counts. The dump and transformation digests are retained
as provenance: when either changes, the full idempotent importer reconciles
the new inputs and only missing binary checksums are ingested. Odoo is still
rebuilt, and every business link, permission, original and preview is
revalidated. Runtime incompatibility, a deliberately bumped reuse contract or
archive drift aborts before importing and instructs the operator to use the
fresh command. A successful validation atomically reseals the ignored
checkpoint under `artifacts/migration/private/checkpoints/`.

This optimization is deliberately disabled by the pre-production release
orchestrator. It is analogous to reusing one verified build stage, not to
restoring an unverified database snapshot.

### Portable worktree QA seed

The same-project checkpoint cannot be shared safely by linked worktrees. Use
`make qa` instead: it restores a credential-sanitized Odoo snapshot and the
official Paperless exporter bundle into new, independently writable volumes.
The ignored seed under `artifacts/migration/private/qa-seeds/` is sealed against
the source dump and filestore, migration code, archive content, OCR settings
and actual resolved runtime image IDs. It is never uploaded or committed.

`DOCUMENTS_RESTORE_PROFILE` supports `full`, `accounting`, `hr` and `smoke`.
Semantic selection retains whole checksum groups and never splits duplicates
from their Odoo relationships. `smoke` deterministically covers both companies
plus accounting evidence, HR-restricted material, Trash, duplicates,
unassigned evidence, PDF/image/Tika formats and permissions. The numeric limit
remains an internal diagnostic and cannot seal reusable evidence.

Final production migration always uses the fresh `full` profile. Cached QA
uses the official importer without OCR; target finalization then rebuilds Odoo
links and governed identity mappings for the isolated environment.

This stage deliberately uses only the non-human archive service identity. It
does not copy Online users, tokens, passwords, SSO links or connection state.
After all source-data stages and migration finalization pass,
`make target-finalize` switches the archive to the runtime `odoo-integration`
owner, provisions governed Pocket identities in Paperless, maps them to their
existing Odoo users by immutable subject, and synchronizes the Odoo-authorized
document set. That separation keeps user access reproducible without pretending
target SSO is source data.

## Qualified dump evidence

The complete 18 August 2026 qualification against source dump
`395cc8b950b592035fed41dedf0072f3487e18f10b4010f939331a5e5b51e69f`
proved:

- 657 binary Documents identities plus 9 unassigned evidence files;
- 638 exact-checksum archive roots and 0 failed groups;
- 636 available roots, 2 roots retained in Trash, and 863 active Odoo
  business-record relationships: 427 accounting entries, 411 Contacts, 15
  employees, and 10 Paie TESE records;
- 638 preserved source-added timestamps and one missing native operational
  attachment restored (`1602` before, `1603` after);
- 29 useful source-derived tags with no empty tag, 17 used document types, and
  69 correspondents in the qualified archive;
- 47 unused source tag/rule names and the obsolete empty `KBis` type excluded
  from the final live catalog, with zero `Legacy Odoo` custom fields remaining;
- 56 source tag definitions, 88 folder identities, 722 access-history rows, and every
  duplicate source identity retained in private evidence;
- successful original checksum, preview, version, metadata, company,
  relationship, and full object-permission read-back for every root;
- a sealed full-run evidence SHA-256 of
  `159d2f6f9b9a49fd2dc95a6039ca05fea1579cf04bb342aaf8837ec95ec98a74`.

One newly exported supplier-invoice attachment declares itself as PDF but
contains corrupt base64-like source bytes. The exact original remains attached
in Odoo; the migration produces a deterministic searchable Paperless
representation and records the exception instead of silently discarding or
rewriting the evidence.

Paperless's own `document_sanity_checker` completed without an integrity
error. It reported only five informational no-OCR items; those originals and
their typed previews remain valid.

For a qualification run before the complete perimeter:

```bash
DOCUMENTS_RESTORE_LIMIT=3 make documents-restore-import
```

Limited runs write separately named evidence and never count as full migration
acceptance.

`serve` exposes the disposable migrated target only on the isolated acceptance
ports: Odoo `28080` and Paperless `28010`. It validates Odoo's runtime database
name and filter before reporting the URLs. The runner refuses the main branch's
`8069`/`8010` ports and the Documents feature QA `18080`/`18010` ports.

## Failure behavior

Missing mappings, duplicate target identities, changed source bytes,
incompatible APIs, incorrect restored originals, empty previews, unsafe
permissions, or inconsistent relationships stop the stage.

If Paperless cannot consume a file, the exact bytes are placed in an explicitly
failed Odoo migration quarantine attachment with source trace. The stage still
fails. This fallback prevents data loss without falsely reporting the item as
archived; the format must be resolved and the stage rerun before the Documents
scope can pass. The three dump- and identity-qualified exceptions are described
above; arbitrary unsupported files remain rejected.

The canonical full migration is the only supported source-document path.
Qualification experiments are not shipped as alternate runners.
