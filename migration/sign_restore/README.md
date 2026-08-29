# Odoo Online Sign restoration

This one-shot stage preserves the completed Odoo Online Sign perimeter in USL
Sign and Paperless without presenting a historical Enterprise ceremony as a
native USL Sign completion.

## Target contract

Each of the eight source requests becomes an immutable **Odoo Online
(External)** record in the normal Completed Documents collection. Its state is
`external_archived`, not native `completed`; each participant is
`external_recorded`, not native `signed`. Requested, recommended and achieved
trust remain empty. Native validation and evidence remain `not_started`, and no
USL policy snapshot, validation row, completion certificate, proof dossier or
personal-certificate claim is created.

The exact exported signed PDF is the record's viewable document. Paperless is
authoritative for five private, request-linked artifacts:

1. the exact exported signed PDF;
2. the richer user-exported Odoo Online certificate;
3. the unsigned source/template PDF from the source filestore;
4. the completion certificate attached at the original completion time; and
5. a readable PDF rendering of the sanitized JSON history, including its
   canonical JSON SHA-256, containing business dates, participants, roles,
   chatter, audit events and non-reusable field values.

The first two are also linked directly from the Sign request. All five receive
the `Odoo Online (External)` tag, a purpose tag and their source Documents
classification where available. They are linked to the request and its
participants, then Paperless permissions are synchronized.

The three inactive source templates that were never used by a request are
archived as inactive template source documents; they are not recreated as
active USL templates. Exact duplicate binaries reuse one Paperless root.

## Deliberate non-copy decisions

Bearer request/signer tokens, SMS tokens and access tokens are not read.
Eleven rendered signer marks and four reusable user signature/initial
preferences are not imported as reusable images: the signed PDFs preserve
their historical rendered use, while copying the images would create an
impersonation capability. Their source IDs, checksums, sizes and disposition
remain only in the private, ignored migration evidence. Signature/initial
values in the archived history JSON are replaced by SHA-256 fingerprints.

Source table/model identities never enter delivered product fields. Temporary
`ir.model.data` bindings make replay idempotent, then finalization removes them.
The boundary gate rejects legacy Enterprise Sign models, migration bindings and
legacy/source-specific product fields.

## Matching and failure policy

Signed exports are matched to source completion attachments by exact SHA-1 and
size, never by filename. The companion certificate is then matched to that
unique signed export filename, inspected as a PDF, and required to identify the
source request and every signer email. An export can satisfy only one request.
Missing, extra, reused or changed files fail before target request creation.

The source connection is transaction-read-only. The stage expects exactly 8
requests, 11 signers, 61 audit events, 25 chatter messages, 87 field values and
50 Sign-related attachments. Any perimeter change requires a reviewed code and
documentation update.

## Run

Run only against an isolated reconstruction project whose target already has
restored identities and `usl_documents` installed. `prepare-target` installs
or updates `usl_sign`; `all` then performs the guarded data restoration:

```bash
COMPOSE_PROJECT_NAME=codex-migration-sign \
SIGN_TARGET_DATABASE=odoo_sign_restore \
USL_ONLINE_DUMP_DIR=/absolute/path/to/usl-online-dump \
scripts/sign-restore prepare-target

COMPOSE_PROJECT_NAME=codex-migration-sign \
SIGN_TARGET_DATABASE=odoo_sign_restore \
USL_ONLINE_DUMP_DIR=/absolute/path/to/usl-online-dump \
scripts/sign-restore all
```

`all` imports, validates, replays and validates again for idempotency, removes
temporary bindings, then runs both the Sign-specific and repository-wide
product/migration boundary gates. Live electronic invoice and e-reporting
flags are forced to `0`. Evidence is written beneath
`artifacts/migration/private/sign-restore/` and must never be committed.

The standalone runner refuses `odoo_dev`, protected source/validation database
names, and non-isolated Compose project names. The canonical reconstruction
uses the narrowly scoped `SIGN_CANONICAL_TARGET=1` mode, which accepts only its
isolated `odoo_dev` and preserves metadata on checksum-identical roots already
ingested by the Documents stage. The runner does not address or stop any QA
Compose project.
