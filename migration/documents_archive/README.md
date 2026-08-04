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
- legal company, owner, correspondent, tags, folder path, lifecycle, explicit
  access history, and inactive/Trash state;
- unassigned enterprise files as visible `Needs attention` archive items;
- legacy public-link state as audit evidence, while deliberately revoking the
  old bearer tokens so the rebuilt Odoo authorization boundary cannot be
  bypassed.

The 77 legacy folders and their accounting/HR folder-tag settings are retained
in sealed evidence and translated into Paperless tags, folder-path metadata,
record links, company policy, and the rebuilt app's business-context rules;
they are not recreated as a second manual folder tree. The source's sole URL
document is the untouched upstream `documents` tutorial XML record, so it is
classified as recomputed distribution reference data rather than user content.

The received original is downloaded from Paperless and SHA-256 checked after
ingestion. Received PDFs and generated searchable representations must preview
as valid PDFs; other supported formats must return a non-empty preview whose
media type is recorded in evidence. The successful path asserts that
Odoo's attachment count does not increase, except for three qualified source
formats rejected by Paperless 3.0.4: one generated FEC ZIP, one accounting XML,
and one calendar evidence file. Each exact authoritative source remains an
operational Odoo attachment while Paperless holds a checksum-linked,
deterministic, searchable PDF representation. All ordinary archive binaries
belong only to Paperless.

Paperless string custom fields are limited to 128 characters. Odoo and
Paperless therefore keep compact source identities and lookup hashes. The full
source relationship, access, lifecycle, multilingual label, filename, and
checksum evidence is sealed outside the product database under:

`accounting_compat/private/snapshots/source-<dump>/evidence/`

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

`validate` intentionally executes the same idempotent contract as `import`.
It must reuse existing checksum roots, refresh authoritative Paperless
metadata, recheck originals, previews, permissions and links, and leave counts
unchanged.

## Qualified dump evidence

The complete 4 August 2026 qualification against source dump
`e1d95464d1ff633ec0db112cef50a20463f746abe94d05e5749d781b1f79cdd9`
proved:

- 567 binary Documents identities plus 9 unassigned evidence files;
- 548 exact-checksum archive roots, 0 failed groups, and no archive-binary
  increase in Odoo;
- 539 available roots, 9 roots retained in Trash, and 363 active Odoo
  business-record relationships;
- 49 source tags, 77 folder identities, 625 access-history rows, and every
  duplicate source identity retained in private evidence;
- successful original checksum, preview, version, metadata, company,
  relationship, and full object-permission read-back for every root;
- a second full validation run with the same archive/root/link counts and the
  same sealed evidence SHA-256:
  `554c3cecb791b80ce5e8bd59dbd969162ca08b83f931094cce3eecd7da453e2c`.

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

The older `source_pdf_pilot.py` remains only as historical qualification code.
It is not the canonical full migration path.
