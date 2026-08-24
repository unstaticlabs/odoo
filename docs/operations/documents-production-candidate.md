# Documents production-candidate delivery

This runbook records the bounded delivery plan and evidence for the
Odoo–Paperless Documents production candidate. It is updated checkpoint by
checkpoint. A row marked **planned** or **partial** is not release evidence.

## Repository and release boundary

- Odoo development branch: `codex/fix-seamless-paperless-documents`.
- Odoo starting commit: `6d5ea36048bf2e4d352b2bb49995485fba61e168`.
- Odoo base: `origin/19-usl` at
  `e3b64c209acf0c4f50baa1a9ee519d8eb2c9b621`.
- Odoo MCP development branch: `codex/paperless-documents-mcp`.
- Odoo MCP starting commit and base:
  `fd4627afa7a2aa43ac2f06744d48bb76fe627fdc`.
- Source dump: `/Users/roger/projects/odoo/usl-online-dump/dump.sql`, SHA-256
  `0b9916db4807206f63b654bd2933ac89b0aab30ba7e0a1004edc4c060490238f`.
- The Odoo branch is advanced only with ordinary scoped commits. Do not reset,
  rebase, merge, amend, force-push, or rewrite its validated history.
- The MCP branch remains independent from `main` and preserves unrelated MCP
  work.

No checkpoint pointer is created until its complete gate passes. Existing
checkpoint pointers are immutable.

## Isolated QA/demo environment

Status: **partial source-derived QA with a deterministic synthetic Documents
overlay**. It is directly usable for product journeys, but it is not source,
migration, accounting, release-cohort, or production-parity evidence.

| Resource | Scoped value |
|---|---|
| Compose project | `usl-odoo-paperless-193-0824` |
| Odoo database | `odoo_dev` inside the project-only `postgres-data` volume |
| Odoo filestore | project-only `odoo-data` volume |
| Odoo | `http://odoo.localhost:19669` |
| Odoo gevent/websocket | port `19672` |
| Paperless | `http://paperless.localhost:19010` |
| Pocket ID | `http://pocket-id.localhost:19411` |
| Ollama | `http://127.0.0.1:19434` |
| Odoo MCP development Worker | `http://127.0.0.1:19787/mcp` |
| Focused Documents MCP | planned at `http://127.0.0.1:19787/documents/mcp` |
| MCP Inspector | port `19788` reserved and currently unpublished |
| MCP development state | `/private/tmp/usl-odoo-paperless-193-0824-mcp-state` |

The internal database name matches the conventional developer name, but it is
not the canonical database: the Compose ownership label points to this
worktree and every mutable PostgreSQL, filestore, Paperless, Valkey, Pocket ID,
Ollama, and MCP state path is scoped to this project. No other worktree's
container, network, volume, or port is shared.

The environment keeps `USL_EINVOICE_LIVE_ENABLED=0` and
`USL_EREPORTING_LIVE_ENABLED=0`. It must never contact a live French invoice,
directory, e-reporting, or production provider.

Paperless is exactly `3.0.5` at image digest
`sha256:65a4cabf0169ea7fbd90ab7bb28ba3f8b5909613635acda1a03ad606f34b456b`.
The qualified derived Paperless image is
`usl-paperless-ngx:3.0.5-usl.1` at manifest digest
`sha256:b811135ed1a675882be6b95d78c1753e8acfc8cf5837ffa30ace4d0b4f48ab3b`.
It is built from the exact base above and contains only the documented
`semantic-search-api-v1` overlay. The isolated Ollama service is exactly `0.30.11` at image digest
`sha256:c484b703176aa19dfc0a54cbfb60ab8094b38faa04283fb77eba1d33319e5eca`.
Its application-facing model is `usl-bge-m3:documents-20260824-rc1`, model
digest `7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab`,
with 1,024-dimensional F16 embeddings, an 8,192-token context, 1.2 GB model
size, and the upstream MIT license. Ollama is a private tracked Compose service
with a project-owned model volume; only the explicit QA override publishes it
on loopback.

### QA personas

| Login | Purpose | Odoo Documents | Paperless mapping |
|---|---|---:|---:|
| `documents-manager` | Documents manager | manager, HR | yes |
| `documents-user` | ordinary internal user | user | yes |
| `documents-readonly` | read-only accounting evidence | accountant reader | yes |
| `documents-restricted` | restricted internal user | none | no |
| `documents-hr` | HR-authorized user | user, HR | yes |
| `documents-multi` | two-company user | user | yes |
| `documents-portal` | portal evidence submitter | none | no |
| `documents-unmapped` | Pocket-only unmapped lifecycle identity | none | no |

Pocket ID authentication proves identity only; Odoo groups and companies remain
authoritative. Generate a fresh, short-lived local link without printing or
persisting it in release evidence:

```bash
POCKET_ID_ENV_FILE="$PWD/.pocket-id-usl-odoo-paperless-193-0824.env" \
  scripts/pocket-id-dev one-time-link documents-manager
```

The same command accepts another listed Pocket username. Ordinary password
login remains disabled by the `sso_only` policy.

### Synthetic overlay

The overlay adds 21 clearly named `documents-qa-*` native attachments and two
Paperless roots without modifying production services. It covers:

- project/task direct and chatter attachments;
- vendor bill, customer invoice, journal, payment, asset, expense, and expense
  batch evidence;
- TESE/payroll and HR-confidential evidence;
- Platform Billing platform, session, and payout evidence;
- final-output and transient-preview examples;
- portal-submitted evidence;
- same-content compatible and incompatible-company examples;
- a two-version Paperless root;
- an external Paperless mailroom intake;
- two companies, a restricted project, and the canary phrase
  `DOCUMENTS-QA-CANARY-9F3A7D`.

Avoid full OCR while the policy and retrieval implementation is changing. The
synthetic text documents are sufficient for deterministic policy, search, and
authorization checks.

To stop only this environment while preserving its volumes:

```bash
docker compose \
  --env-file .pocket-id-usl-odoo-paperless-193-0824.env \
  -p usl-odoo-paperless-193-0824 \
  -f compose.yaml -f compose.pocket-id.yaml \
  --profile paperless stop
```

Do not add `--volumes` during ordinary cleanup or review.

## Architecture decision: archive policy

Three credible approaches were compared.

1. Extend the existing `ir.attachment` bridge, operation queue, context
   adapters, and links. Persist origin and resolved policy on the native
   attachment and copy the immutable resolution onto each operation and link.
   This is selected because it preserves immediate native upload, one queue,
   deterministic retries, the existing composite identity, and Odoo's security
   authority without a core patch.
2. Infer policy only when the background worker runs. This has fewer fields,
   but is rejected because retries and backfill could change meaning after a
   record lifecycle, company, or relationship change. It cannot provide a
   complete attachment ledger or auditable reason.
3. Introduce OCA DMS or a second document-ingestion queue. OCA components remain
   credible for independent DMS deployments, but are rejected here because
   they would create a second attachment/archive engine and competing binary or
   relationship authority. The current product already has the narrower bridge
   required by this distribution.

Presentation role is deliberately excluded from the composite metadata hash.
Promoting or demoting a link must update Odoo policy only; it must not upload a
new root or version and must not overwrite Paperless-managed title, tags,
correspondent, type, or other user metadata.

## Checkpoint plan and evidence matrix

| Checkpoint | Required outcome | Current status |
|---|---|---|
| A — policy engine | origin, mode, role, deterministic operation/link diagnostics, adapters, idempotent retry/backfill | validated 2026-08-24 |
| B — local hybrid search | exact Paperless image, Ollama BGE-M3, Paperless-owned vector index/API, scoped fusion and outage behavior | validated 2026-08-25 |
| C — search UX | search-first information architecture, background visibility, Keep in Documents, promotion/demotion, desktop/mobile | planned |
| D — Documents MCP | Odoo JSON-2 facade, `/documents/mcp`, unified `/mcp`, read-only authorization, Inspector/stack acceptance | isolated Worker running; endpoint planned |
| E — personal Gemini | encrypted per-user key, activation/revocation, no chat UI, no search/index/MCP dependency | planned |
| F — release cohort | migration role backfill, finalized indexes, coordinated bundle, independent/cross-architecture restore | planned |
| G — production candidate | full security/functional matrix, install/upgrades, boundary/accounting/docs gates, release identity | planned |

The final evidence set must account for every native attachment as archived
evidence, library, background, native-only on request, explicitly excluded, or
blocked failure. It must also prove zero unauthorized search/MCP results,
hybrid recall at 5 of at least 90%, no exact-identifier regression, no Gemini
call from search/index/MCP, independent restoration without OCR or embedding
rebuild, and clean Odoo and MCP worktrees.

## Checkpoint A evidence — Documents policy engine

Validated commits:

- `59657818445` — persisted origin, archive mode, policy/current role, reason,
  operation/link diagnostics, access-sensitive root prominence, ledger state,
  migration initialization, and business-context adapters;
- `17f0b7f58e9` — policy, idempotency, composite identity, adapter, forgery,
  exclusion, and permission-sensitive prominence tests.

The selected bridge keeps native uploads synchronous only to Odoo. Paperless,
OCR, embeddings, and optional generative providers remain outside the upload
transaction. Trusted chatter/portal origins are captured at the existing mail
controller and `message_post` boundaries without an Odoo core patch. Client
context cannot forge a trusted origin or write policy diagnostics.

Validation used only the scoped Compose project. The first combined run found
one ledger transition defect: a successfully queued on-request attachment
remained labelled native-only. The implementation was fixed centrally, the
exact regression passed, and the complete gate then passed with 184 tests,
zero failures, and zero errors:

```text
usl_documents                 123 tests
usl_expense_batch              15 tests
usl_platform_billing           33 tests
usl_tese_payroll               25 tests
combined gate                 184 tests, 0 failed, 0 errors
```

An additional focused negative test proves that a user cannot receive a
prominent Home/library classification from an evidence relationship whose
target record they cannot read. Clean module installation, update, repeated
update, scoped Ruff, Python compilation, XML parsing, and `git diff --check`
all exited successfully. Ruff's formatter check still reports legacy
whole-file formatting drift in four large pre-existing modules; it was not
applied because doing so would create unrelated formatting churn. The lint
check for all changed implementation files passes.

## Checkpoint B evidence — local hybrid search

Three implementation alternatives were compared. Paperless's native
similar-document helper is useful when an existing document is the query, but
it cannot retrieve arbitrary text; its document-chat path would introduce a
generative-provider dependency. A separate Odoo vector store could accept
arbitrary text, but would duplicate embeddings and bypass Paperless's index
lifecycle and permission boundary. The selected minimal Paperless 3.0.5
overlay exposes a read-only semantic endpoint backed by Paperless's own
embedding client and `llmindex.db`, with upstream-style tests and exact-source
hash guards.

For rank combination, raw-score normalization was rejected because Tantivy
and cosine scores are not calibrated to one another, while lexical-only search
failed most paraphrase cases. Reciprocal-rank fusion is selected because it is
deterministic across the two rank domains. Identifier-like queries retain the
complete lexical order before semantic-only additions to prevent exact-match
regressions.

Every Odoo-mediated lexical, custom-field, and semantic search carries the
current Odoo record-rule/company scope. Lexical scope is split into bounded
500-root request filters and equal ranks are interleaved across chunks. Empty
scope performs no Paperless request. The semantic endpoint also resolves
Paperless permissions and intersects the mandatory Odoo scope before vector
retrieval. Reserved request fields cannot be overwritten by facets. The
`odoo-integration` identity receives HTTP
403 without a scope, an empty scope touches neither index nor embedding
backend, facets narrow the scope first, and historical versions are admitted
only after their root is authorized. Query, result, scope, facet, and excerpt
bounds are enforced. Missing Ollama or index state returns a structured HTTP
503; Odoo hybrid search retains lexical results and a bounded warning.

The wholly synthetic French/English evaluation contained 21 records, five
questions per record, and four negative probes. Both 512- and 1024-token
candidates achieved hybrid recall@5 of 100%, hybrid MRR 0.9770, semantic
recall@5 100%, semantic MRR 0.9484, zero unauthorized results, and zero exact
identifier regressions. Their top-five rankings were identical across all 109
questions. Paperless's native overlap is 200 tokens. The selected 512-token
configuration produced 911 vectors under LLM-index schema 2, versus 264 for
1024; it had lower observed semantic latency and Ollama memory while preserving
quality. The timed selected rebuild completed in 448 seconds. Detailed public
methodology and metrics live under `deploy/documents/evaluation/`; private
per-query evidence remains outside Git with mode `0600`.

The Paperless API suite passed 7 tests in the exact, network-disabled derived
image. The Odoo hybrid scope, multi-company, fusion, exact-ranking, outage,
meaning-only, empty-scope, large lexical/semantic scope, reserved-facet, and
punctuation contracts passed in a focused 10-test run. The final complete
`usl_documents` run passed 130 tests with zero failures and zero errors (134
test entries). Two consecutive updates of `usl_documents` on the isolated QA
database passed, followed by healthy real-stack apostrophe, exact-reference,
and paraphrase queries with no warning.

Three harness failures were corrected without weakening a test. The production
Odoo image could not discover OCA tests because it intentionally lacks the
test-only `responses` package, so the purpose-built test image was used. Ruff's
first read-only run attempted to create its cache, then passed with `--no-cache`.
Two new tests initially attempted to patch immutable Odoo recordset methods;
they were rewritten to exercise the same contracts at the Paperless client
boundary and the exact gate then passed.

## Remaining gaps after Checkpoint B

- Existing links cannot be promoted or demoted independently of the Paperless
  root.
- Home/Recent does not yet suppress background-only roots.
- The Odoo MCP has no `/documents/mcp` endpoint or Documents tools.
- There is no per-user Gemini key boundary.
- Ollama and MCP are isolated for QA but are not yet members of a portable,
  digest-bound release cohort.
- The current QA overlay is intentionally partial and cannot satisfy source or
  release parity gates.
