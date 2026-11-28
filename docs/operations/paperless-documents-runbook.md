# Paperless and Documents operations

Paperless is the archive backend for the native Odoo Documents application.
It runs as a profile of the unified runtime and has no standalone QA or
pre-production stack.

## Authority and access

Odoo owns companies, business links, and access decisions. Paperless owns the
archive originals, previews, OCR text, metadata, versions, search data, and
Trash. Users enter through Odoo or the governed Pocket ID route; Paperless
roles must not grant broader access than Odoo.

Files attached to supported Odoo records remain immediately available in Odoo
and enter the archive asynchronously with their company, record, and safe
classification context.

## Runtime checks

Inspect a protected local runtime without restarting it:

```bash
migration/manage transition status --runtime <runtime-id>
```

Confirm:

- Odoo, Paperless web, PostgreSQL, broker, task workers, and search/vector
  services are healthy;
- native macOS Ollama or the Linux production container matches the recorded
  BGE manifest;
- originals and previews are readable;
- OCR, metadata, permissions, business links, and versions are consistent;
- queues contain no unexplained pending, processing, or failed work;
- Tantivy and vector coverage match the accepted archive identity.

## Office and email previews

Paperless extracts Office text through Tika and renders its PDF preview through
Gotenberg. Email previews also use Gotenberg. Compose uses 9.8 by default. Paperless chooses the API
from the selected image's version tag, so a local image pin to Gotenberg 8
continues to work. The Paperless image alone uses the Gotenberg 8 API. When a
separately managed stack moves to Gotenberg 9, update its renderer pin and set
`USL_PAPERLESS_GOTENBERG_API_MAJOR=9` on the Paperless webserver.
While the managed renderer stays on Gotenberg 8, an earlier Paperless image
can use that same renderer. To roll back after activating Gotenberg 9,
restore the renderer pin and the matching API setting together before
rolling back to a Paperless image that only supports Gotenberg 8.

After changing the renderer, import a synthetic Office document and email
through Paperless. Check that search finds their text, the originals download
unchanged, and the previews use the configured PDF/A profile. The renderer's health
endpoint only confirms that it is ready to receive a request.

## User and permission reconciliation

Pocket ID subjects map to existing Odoo users. Odoo company and record rules
remain authoritative. Reconcile Paperless users only through the governed
Pocket ID helper for the same runtime identity; never create a parallel local
user or infer permissions from an email address alone.

For an access incident:

1. identify the Odoo user, active companies, business record, and document;
2. verify the Odoo ACL and record rule without `sudo`;
3. inspect the cached Paperless permission and exact document identity;
4. reproduce on a disposable clone;
5. repair the governing Odoo relationship or synchronization behavior;
6. confirm that another company cannot read the record.

## Recovery

The production recovery unit includes Paperless PostgreSQL, broker state,
media, data/search, Trash, export, Tantivy, vectors, and the matching Odoo and
Ollama state. Independent restore must reach exact parity without OCR,
re-ingestion, vector rebuild, or model download. See
[Production operations](production.md).

## External providers

Personal Gemini and other providers are explicit user opt-ins. Missing or
invalid personal credentials must fail without exposing secrets or blocking
the local BGE path. Keep provider keys outside runtime JSON, Compose scope,
logs, screenshots, and committed configuration.
