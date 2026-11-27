# USL Paperless 3.5.1 distribution overlay

This directory contains the only Paperless source overlay in the USL
distribution. It applies to upstream Paperless-ngx `v3.5.1`, source commit
`35b0439195bbdb87a6d519152e6782abe0720c3b`, image index digest
`sha256:8508853b8a0f8394074260957d0be186c8c64f346973b24a7583de4580f306b9`.
The backend and frontend patchers verify the SHA-256 of every changed upstream
file and reject any source drift before changing the image. The exact source
archive is pinned at
`sha256:8416949e59c86befe73b8ac761ca5056a50972ebe9ef7db1d66990745ac7a49a`;
the frontend builder is pinned at
`node@sha256:996f094d0487f4f9cbc9227a4ebba56e011d3653679909b04a9fa6dc7ab54aa4`.

## Patch inventory

Patch level `scoped-lexical-search-v1+permission-vector-invariance-v2+deferred-bulk-index-v1+semantic-search-api-v2+personal-gemini-v1+ollama-batch-v1+preview-contract-v1+byte-range-download-v1+workflow-ai-disabled-v1+tag-scores-v1+atomic-index-v1+reviewed-tags-v1` has twelve bounded feature groups:

1. `paperless_ai/semantic_api.py` adds the authenticated, read-only
   `POST /api/documents/scoped_search/` endpoint. One request carries the
   complete explicit Odoo authorization scope (up to 50,000 root IDs) and runs
   one native Tantivy query across title/OCR, archive metadata, and every
   indexed custom-field name/value. Exact-source schema/backend patches add
   the two plain-text companion fields. They change Paperless's schema
   fingerprint, so Paperless rebuilds only its search index once. Like
   upstream simple search since 3.4, every query token must occur, in any
   order and in any of the scoped fields.
2. The exact-source permission patch keeps Paperless's supported bulk
   permission lifecycle and native Tantivy refresh, but marks ownership/ACL-only
   edits as embedding-invariant. Those edits no longer recompute unchanged
   BGE-M3 vectors or hit the 30-minute task limit. Every other bulk metadata or
   content edit retains the upstream vector refresh. Since Paperless 3.5, a bulk
   edit runs as one queued task that refreshes the documents itself
   (`documents/bulk_operations.py`), so the patch applies the same rule there.
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
8. `paperless_usl_ranges.py` adds authenticated HEAD and bounded single-range
   download responses. It preserves exact version and original/archive
   selection while allowing Odoo to stream large binaries without buffering.
9. Paperless 3.1 added an "Apply AI suggestions" workflow action. It runs
   without an initiating person and would generate as the document owner.
   The exact-source patches hide the action in the workflow editor, make the
   API refuse it, and make the worker refuse any stored instance. Personal
   Gemini generation stays tied to a person's own request.
10. The optional tag scores expose the native classifier's estimate for a
    tag the caller can read. The evaluation procedure below measures accuracy.
11. A semantic index rebuild keeps the last complete SQLite database available
    while the configured embedding model fills a second file on the same volume.
    Before publication, SQLite flushes the new index's write-ahead journal
    and checks the standalone database. Once readers finish, SQLite flushes
    the previous index’s journal before the atomic switch.
    A failed checkpoint, an interrupted rebuild or a busy reader leaves the
    previous index in place. The next update retries. Allow room for both
    databases and the new index's journal during the build. Incremental edits and semantic
    queries require the model name recorded in the index. A legacy table without
    a model name is rebuilt at the next index update. For the interval after a
    model change, lexical search remains available. The application's model name
    and deployment's Ollama digest check serve different purposes.

12. Learned tags can use an optional minimum model score. Paperless stores a
    receipt for each automatic addition. Classification and undo preserve
    subsequent manual changes.

`tests/test_workflow_ai_disabled.py` proves that the workflow action cannot be
saved and that a stored instance never generates.

`tests/test_semantic_api.py` is an upstream-style Django/DRF test module
covering both bounded endpoints and permission-vector invariance: permission
resolution before retrieval, object grants,
mandatory service scope, indistinguishable source-document denial, source
exclusion, empty-scope fail-closed behavior, configured lexical fields,
facets, request limits, and embedding outage behavior.

No OCR, Tantivy, local embedding, vector-index, version, ingestion, workflow,
or MCP path can resolve a Gemini credential. The only external generation paths are the
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

## Evaluate automatic tag proposals

Request `GET /api/documents/{id}/suggestions/?include_tag_scores=1` to
receive a `tag_scores` object alongside the usual suggestions. Its keys are
Paperless tag IDs. Its values are the local model's output for each proposed
tag the caller can see. Several tags may score highly on the same document.
A text-matching rule can propose a tag without a model score. A missing
classifier or a model trained on only one outcome also yields no score.

The scored request recomputes the proposals for the requesting user's access.
Ordinary requests keep their existing shared suggestion cache. In Odoo,
call `usl.document.document_detail(id, check_archive=True, include_tag_scores=True)`.
A readable, unapplied tag proposal with a score carries `classifier_score`.
An older Paperless server can continue returning its usual suggestions.

To assess a threshold, freeze one model revision and independently label a
held-out sample, including documents with no applicable automatic tag. Keep
documents from the same series together when splitting training and evaluation.
For each document, store its `tag_scores` and reviewed `expected_tag_ids` in
a private JSON-lines file. Include every reviewed document, including rows
with an empty score object or empty label list. Use the Paperless IDs for both
columns. The evaluator only prints aggregate counts and rates.

```sh
python3 deploy/documents/paperless-ngx/evaluate_tag_scores.py reviews.jsonl --threshold 0.85
```

Precision answers how often the proposed tags above the threshold were right.
Recall answers how many of the expected tags the threshold recovered.
With zero proposals precision is undefined. Review both measures and the
number of examples before selecting an automatic policy. The scored API
and evaluator perform no tag writes.

### Apply and review one learned tag

In Odoo Documents, open a learned tag and choose **Score rule**. A new
installation has no score rules. Set the minimum separately for each tag using
independently reviewed documents, including examples which should stay untagged.
An eligible tag must be proposed by Paperless and have a real score at or
above its minimum. Dates, correspondents and tags without a policy retain
their native matching behavior.

Open **Automatic tag history** on a document to see the measured score, the
minimum used and the time of each change. A native retagging command groups
its changes into one batch. An intake classifies one document at a time.
**Review batch** lists the documents the current user can still edit. Undo
removes only links created by that run and still owned by it. A manual
removal or a previous undo prevents the classifier from reapplying that
document/tag pair. People can still add or remove the tag themselves.
Replaying an undo is safe even if a response was lost.

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
The global settings include `PAPERLESS_AI_LLM_EXTRA_PARAMS`, which Paperless
3.4 added. A personal client always sends an empty parameter set.
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
