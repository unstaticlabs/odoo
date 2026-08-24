# USL Paperless 3.0.5 distribution overlay

This directory contains the only Paperless source overlay in the USL
distribution. It applies to upstream Paperless-ngx `v3.0.5`, source commit
`8fb73b2709e4c38180a7632edf32f32fe2315961`, image digest
`sha256:65a4cabf0169ea7fbd90ab7bb28ba3f8b5909613635acda1a03ad606f34b456b`.
The build rejects any other upstream `paperless/urls.py` content before it
changes the image.

## Patch inventory

Patch level `semantic-search-api-v1` has two production changes:

1. `paperless_ai/semantic_api.py` adds the authenticated, read-only
   `POST /api/documents/semantic_search/` endpoint. It uses Paperless's native
   Ollama embedding client and `llmindex.db`; it never opens the vector database
   from Odoo.
2. `apply_overlay.py` adds one import and one URL registration to upstream
   `paperless/urls.py` after verifying its exact SHA-256.

`tests/test_semantic_api.py` is an upstream-style DRF test module covering
permission resolution before retrieval, object grants, mandatory service
scope, empty-scope fail-closed behavior, facets, request limits, and embedding
outage behavior.

No upstream model, migration, task, OCR, Tantivy, version, metadata, or
generative-AI behavior is replaced.

## API contract

The request accepts:

- `query`: required nonblank text, at most 2,048 characters;
- `limit`: 1–50, default 10;
- `document_ids`: optional root scope of at most 10,000 IDs;
- optional tag, correspondent, document-type, and created-date facets.

The configured `odoo-integration` service identity must supply
`document_ids`; an omitted scope is forbidden and an empty scope returns no
results without touching the embedding backend. Direct Paperless users may
omit it, but Paperless resolves their owned, unowned, and object-granted roots
before vector retrieval. Historical version IDs are added only after their
authorized root has been selected. The vector filter is split below SQLite's
bound-parameter limit and is rechecked after retrieval.

Each response item contains the Paperless root ID, rank, similarity, a
whitespace-normalized excerpt capped at 500 characters, and bounded display
metadata. Results are collapsed to one hit per root. Missing index or Ollama
availability returns HTTP 503 with a structured `semantic_unavailable`
warning; Odoo's hybrid facade uses that signal to keep lexical search
operational.

The endpoint is included in Paperless's generated OpenAPI schema through
`drf-spectacular`. It never invokes a generative model.

## Alternatives and removal plan

Two credible non-overlay alternatives were evaluated:

- Paperless's native similar-document helper is permission scoped, but its
  input is an existing document and it returns ORM objects. Its only arbitrary
  text-query surface is document chat, which invokes a generative provider and
  does not expose ranked retrieval results. It cannot implement local search
  or MCP without violating the no-generative-search requirement.
- An Odoo vector index or direct access to Paperless PostgreSQL, Tantivy, or
  `llmindex.db` would avoid a Paperless API change, but would split archive
  authority, duplicate embeddings, and bypass Paperless's index lifecycle and
  permission boundary.

The overlay is therefore the smallest compatible option. Submit the endpoint
upstream with this test module. Remove this directory and switch the Compose
image to the qualified upstream digest once Paperless publishes an equivalent
versioned endpoint with mandatory pre-retrieval permission scope and a
service-scope fail-closed contract.
