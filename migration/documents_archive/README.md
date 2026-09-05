# Documents archive migration

This directory contains migration-only code for rebuilding and verifying the
Paperless-backed Documents archive. It is invoked through
[`migration/manage`](../manage), never through the normal Odoo add-ons path.

## Boundary

The delivered `usl_documents` modules own runtime behavior. This directory
owns only one-shot source extraction, archive reconstruction, release identity,
portable filestore handling, final evidence, and recovery qualification.

Migration must preserve:

- every source original and attachment disposition;
- Paperless originals, previews, OCR text, metadata, permissions, stable
  document identities, links, and version history;
- search/Tantivy state and complete vector coverage;
- the exact pinned BGE model manifest;
- coordinated Paperless PostgreSQL, media, data/search, Trash, export, broker,
  and Ollama state.

The final Odoo database must not retain migration models, source bindings,
technical provenance fields, or migration menus.

## Fresh-source rule

QA and transition reconstruction always start from the frozen Online package.
Do not reuse a database, Paperless checkpoint, OCR result, vector archive,
candidate, or reconstruction seed. The only archive record retained by this
package is final runtime-scoped evidence sealed after successful processing;
it is not a resume input.

## Local and production Ollama

On macOS, `migration/manage` selects the qualified native Ollama and omits the
Docker Ollama service. An installed but unreachable service fails closed.
Linux production uses the pinned container image. Every path verifies model
identity before embedding work.

## Acceptance

The Documents stage must prove:

- source-wide attachment coverage and readable originals;
- exact checksums and stable record/document links;
- OCR, preview, metadata, permission, and version parity;
- no unexplained pending, processing, or failed work;
- Tantivy search readiness and complete vector coverage;
- independent cohort restore without OCR, re-ingestion, vector rebuild, or
  model download;
- product/migration boundary cleanup.

Use `migration/manage qa status` for non-mutating runtime inspection and the
candidate/cohort commands for portable artifact validation. See the
[migration runbook](../../docs/operations/migration.md) for the complete
lifecycle.
