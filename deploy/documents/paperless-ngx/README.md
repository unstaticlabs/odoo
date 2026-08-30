# USL Paperless 3.0.5 distribution overlay

This directory contains the only Paperless source overlay in the USL
distribution. It applies to upstream Paperless-ngx `v3.0.5`, source commit
`8fb73b2709e4c38180a7632edf32f32fe2315961`, image digest
`sha256:65a4cabf0169ea7fbd90ab7bb28ba3f8b5909613635acda1a03ad606f34b456b`.
The backend and frontend patchers verify the SHA-256 of every changed upstream
file and reject any source drift before changing the image. The exact source
archive is pinned at
`sha256:e2b713b1e15d388c435d51acd10bf94935e651adbf31a70e061fcc40535394c6`;
the frontend builder is pinned at
`node@sha256:996f094d0487f4f9cbc9227a4ebba56e011d3653679909b04a9fa6dc7ab54aa4`.

## Patch inventory

Patch level `scoped-lexical-search-v1+permission-vector-invariance-v1+deferred-bulk-index-v1+semantic-search-api-v2+personal-gemini-v1+ollama-batch-v1+preview-contract-v1` has seven bounded feature groups:

1. `paperless_ai/semantic_api.py` adds the authenticated, read-only
   `POST /api/documents/scoped_search/` endpoint. One request carries the
   complete explicit Odoo authorization scope (up to 50,000 root IDs) and runs
   one native Tantivy query across title/OCR, archive metadata, and every
   indexed custom-field name/value. Exact-source schema/backend patches add
   the two plain-text companion fields; Paperless rebuilds only its search
   index when schema version 2 is first detected.
2. The exact-source permission patch keeps Paperless's supported bulk
   permission lifecycle and native Tantivy refresh, but marks ownership/ACL-only
   edits as embedding-invariant. Those edits no longer recompute unchanged
   BGE-M3 vectors or hit the 30-minute task limit. Every other bulk metadata or
   content edit retains the upstream vector refresh.
3. Controlled one-shot archive reconstruction may set
   `PAPERLESS_USL_DEFER_SEMANTIC_INDEX=true` while OCR, metadata, and
   permission truth are materialized. Incremental embedding tasks are then
   suppressed, the normal runtime is force-restored, and Paperless's supported
   migrate/update/compact commands must produce a complete vector inventory.
   Production admission rejects this migration-only switch.
4. The same module adds the authenticated, read-only
   `POST /api/documents/semantic_search/` endpoint. It uses Paperless's native
   Ollama embedding client and `llmindex.db`; it never opens the vector database
   from Odoo.
5. `paperless_personal_ai` is a supported Django app loaded through
   `PAPERLESS_APPS`. It owns per-user Gemini configuration, an encrypted
   user-bound credential, profile-only APIs, release checks, and runtime
   permission rechecks. The exact-source backend patch removes native global
   generative fallback and routes Paperless's existing suggestions and document
   chat through the initiating user's configuration. The exact-source Angular
   patch adds those settings to **My profile**, gates the two existing
   generative entry points independently, removes the native global LLM
   settings, and compiles the normal localized frontend.
6. The exact-source embedding/settings patch exposes the LlamaIndex Ollama
   client batch size as `PAPERLESS_AI_LLM_EMBEDDING_BATCH_SIZE`. The qualified
   value is 32: measured native-Metal throughput has already plateaued there,
   and release preflight rejects drift.
7. The image build parses the final Paperless views module and proves that
   every preview/download call matches the exact `serve_file` request
   signature. The source-archive restore then validates a non-empty preview for
   every restored document, so source or dependency drift fails before a QA or
   production cohort can be accepted.

`tests/test_semantic_api.py` is an upstream-style Django/DRF test module
covering both bounded endpoints and permission-vector invariance: permission
resolution before retrieval, object grants,
mandatory service scope, indistinguishable source-document denial, source
exclusion, empty-scope fail-closed behavior, configured lexical fields,
facets, request limits, and embedding outage behavior.

No OCR, Tantivy, local embedding, vector-index, version, ingestion, or MCP path
can resolve a Gemini credential. The only external generation paths are the
two explicitly enabled personal features. There is no Odoo chat UI.

## Scoped lexical API contract

The lexical request requires `document_ids` (at most 50,000 root IDs), accepts
plain `query` text, a bounded `limit` of at most 10,000, and one indexed field
set: `all`, `content`, or `custom_fields`. An optional structured
`custom_field_query` is applied before Tantivy retrieval. The endpoint
intersects the supplied roots with Paperless object permission and live root
identity, then applies that resulting ID set as one Tantivy term-set query.
An empty scope returns no result without opening the index. The response
contains only ranked root IDs and a truncation flag by default. MCP callers may
request a whitespace-normalized OCR excerpt capped at 500 characters; that
mode is additionally limited to 50 results and still uses the same POST. It is
read-only and never invokes embedding or generative models.

## Semantic API contract

The request accepts:

- exactly one of `query` (nonblank text, at most 2,048 characters) or
  `document_id` (an authorized root whose title and OCR become the local
  embedding query);
- `limit`: 1–50, default 10;
- `document_ids`: optional root scope of at most 10,000 IDs;
- optional tag, correspondent, document-type, and created-date facets.

The configured `odoo-integration` service identity must supply
`document_ids`; an omitted scope is forbidden and an empty scope returns no
results without touching the embedding backend. Direct Paperless users may
omit it, but Paperless resolves their owned, unowned, and object-granted roots
before vector retrieval. For document similarity, the source must be visible
and, for the service identity, must itself occur in the explicit scope. It is
removed from the candidate set before retrieval. Historical version IDs are
added only after their authorized root has been selected. The vector filter is
split below SQLite's bound-parameter limit and is rechecked after retrieval.

Each response item contains the Paperless root ID, rank, similarity, a
whitespace-normalized excerpt capped at 500 characters, and bounded display
metadata. Results are collapsed to one hit per root. Missing index or Ollama
availability returns HTTP 503 with a structured `semantic_unavailable`
warning; Odoo's hybrid facade uses that signal to keep lexical search
operational.

The endpoint is included in Paperless's generated OpenAPI schema through
`drf-spectacular`. It never invokes a generative model.

## Personal Gemini contract

Only an active Paperless identity that is both a member of the configured
internal Documents group and mapped to Pocket ID by the governed USL identity
sync may configure personal Gemini. Ordinary and read-only internal users are
eligible without administrator approval. Anonymous, portal, inactive,
unmapped, and service identities are denied. Every API resolves only
`request.user`; even a superuser cannot select another user's profile.

Both metadata suggestions and document chat default to off and can be enabled
or revoked independently. The provider and endpoint are fixed to Google Gemini
and `https://generativelanguage.googleapis.com/v1beta/openai/`. The approved
stable model allowlist is `gemini-3.7-flash` (default) and
`gemini-3.6-flash`; `latest`, preview, experimental, and arbitrary endpoint
values are rejected. Google documents both stable IDs and the OpenAI-compatible
endpoint in its [model catalogue](https://ai.google.dev/gemini-api/docs/models)
and [compatibility guide](https://ai.google.dev/gemini-api/docs/openai).

The API key is accepted only as a write-only value. It is encrypted with a
random per-credential AES-256-GCM data-encryption key; that key is wrapped by a
versioned AES-256-GCM master key. Associated data binds both layers to the
Paperless user ID, credential revision, and master-key identity. Ciphertexts
therefore cannot be moved across users or revisions. The master-key ring is a
Compose/Docker secret mounted at `/run/secrets/usl_personal_ai_master_keys` and
identified only by `USL_PERSONAL_AI_MASTER_KEYS_PATH`. It is never copied into
an environment variable. Resolution lazily rewraps an old data key under the
active master-key version without decrypting and re-encrypting the stored API
key.

The key is never returned, redisplayed, stored in browser storage, exported,
sent to Odoo or MCP, or included in logs and exception chains. Provider errors
are converted to credential-free failures. **Test connection** calls only the
fixed `/models` endpoint and verifies that the selected model is available; it
does not send document content. Disable takes effect at the next authorization
check. Delete disables both features and erases every encrypted credential
field immediately.

`python manage.py check_personal_ai_release` fails if a native global
generative setting is present in either environment or database, if an inline
master-key environment variable exists, or if the mounted key ring is invalid.
Operational setup, rotation, revocation, incident response, and restore rules
are in `docs/operations/personal-gemini-runbook.md`.

## Alternatives and removal plan

Two credible non-overlay alternatives were evaluated:

- Paperless's native similar-document helper accepts an existing document and
  a candidate ID list, but it is not an authenticated API, returns ORM objects,
  and provides neither the mandatory service-scope contract nor bounded
  excerpts. Its only arbitrary-text surface is document chat, which invokes a
  generative provider rather than exposing ranked retrieval results.
- Paperless's public `more_like_id` API is a credible no-overlay fallback, but
  it uses Tantivy rather than the qualified BGE-M3 index and intersects
  candidates after retrieval, so it cannot prove the required vector
  pre-retrieval scope.
- An Odoo vector index or direct access to Paperless PostgreSQL or
  `llmindex.db` would avoid a Paperless API change, but would split archive
  authority, duplicate embeddings, and bypass Paperless's index lifecycle and
  permission boundary.

The overlay is therefore the smallest compatible option. Submit the endpoint
upstream with this test module. Remove this directory and switch the Compose
image to the qualified upstream digest once Paperless publishes an equivalent
versioned endpoint with mandatory pre-retrieval permission scope and a
service-scope fail-closed contract.

For personal generation, three credible designs were compared:

- Paperless's native global LLM configuration was rejected because it shares
  one administrator-controlled credential across all users and cannot express
  personal consent, independent toggles, user ownership, or cross-user key
  isolation.
- Storing keys and proxying generation through Odoo was rejected because it
  would make Odoo a second secret and chat authority, introduce the explicitly
  excluded Odoo chat UI, and couple Paperless generation to Odoo availability.
- A supported Paperless Django app plus exact-source Angular build is selected.
  Runtime DOM/script injection was considered but rejected because it bypasses
  Angular compilation, localization, and component tests and is brittle across
  upstream releases.

Remove the personal-generation patch when upstream provides an equivalent
per-user, encrypted, independently revocable settings contract and a supported
frontend extension point. Until then, any Paperless upgrade must update every
hash guard, rebuild all locales, rerun the backend/frontend suites, and reprove
that search, OCR, indexing, and MCP never resolve a personal credential.
