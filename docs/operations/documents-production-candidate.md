# Documents production-candidate delivery

This runbook records the bounded delivery plan and evidence for the
Odoo–Paperless Documents production candidate. It is updated checkpoint by
checkpoint. A row marked **planned** or **partial** is not release evidence.

## Repository and release boundary

- Odoo development branch: `codex/fix-seamless-paperless-documents`.
- Odoo starting commit: `6d5ea36048bf2e4d352b2bb49995485fba61e168`.
- Odoo base originally replayed at
  `e3b64c209acf0c4f50baa1a9ee519d8eb2c9b621`; the final branch integrates
  current `origin/19-usl` at
  `65b9bd8827060a72cb42c10ef7875a4766a83f67`.
- Odoo MCP development branch: `codex/paperless-documents-mcp`.
- Odoo MCP starting commit and base:
  `fd4627afa7a2aa43ac2f06744d48bb76fe627fdc`.
- Odoo MCP current main and pushed feature tip:
  `394124bf1dc0e28b7faa1f0a9c78f31180534478` and
  `c22538006c521ab981c0d5ee3c3c5ff20e8e8b83`, respectively. Current main is
  an ancestor of the feature tip.
- Source dump: `/Users/roger/projects/odoo/usl-online-dump/dump.sql`, SHA-256
  `ad313e28586fafa27a4f6a266df57080456613dff1c8c2c6d7e012732bf633b1`.
- The Odoo branch is advanced only with ordinary scoped commits. Do not reset,
  rebase, merge, amend, force-push, or rewrite its validated history.
- The MCP branch remains independent from `main` and preserves unrelated MCP
  work.

No checkpoint pointer is created until its complete gate passes. Existing
checkpoint pointers are immutable.

## Isolated QA/demo environment

Status: **source-complete isolated QA with a deterministic synthetic Documents
overlay**. The native attachment source is fully classified and ingested as
recorded below. This mutable QA environment is directly usable for product
journeys and source-completion evidence, but it is not itself a sealed release
cohort or production-parity evidence.

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
| Odoo MCP development Worker | `http://127.0.0.1:19787/mcp`, started on demand |
| Focused Documents MCP | `http://127.0.0.1:19787/documents/mcp`, started on demand |
| MCP Inspector | loopback port `19788` |
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
`usl-paperless-ngx:3.0.5-usl.5`, with locally inspected repository digest
`sha256:43a3c471af24fe8241d6d6e47fef8f02f0f3b76094e64a4b40ec3b6225d502fc`,
ARM64 manifest
`sha256:153c89f7b88024e75422210fad00d97d08fa028eaf36aeba8e1fff205421ceb1`,
and config
`sha256:98f1b7848bc6b41bd81dfe5c9c7f9694c108126d851b284da1acd0f3d5b1e874`.
It is built from the exact base above and contains the documented
`scoped-lexical-search-v1+permission-vector-invariance-v1+semantic-search-api-v2+personal-gemini-v1`
overlay. The isolated Ollama service is exactly
`0.30.11` at image digest
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

During policy and retrieval implementation, full OCR was avoided and the
synthetic text documents were used for deterministic policy, search, and
authorization checks. The separately authorized source-completion run below
subsequently processed the complete eligible native attachment population.

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
| C — search UX | search-first information architecture, background visibility, Keep in Documents, promotion/demotion, desktop/mobile | automated desktop/mobile gates complete; final manual SSO browser tour pending |
| D — Documents MCP | Odoo JSON-2 facade, `/documents/mcp`, unified `/mcp`, read-only authorization, Inspector/stack acceptance | validated 2026-08-25 |
| E — personal Gemini | encrypted per-user key, activation/revocation, no Odoo chat UI, no search/index/MCP dependency | implementation and automated gates complete; manual profile browser evidence pending |
| F — release cohort | migration role backfill, finalized indexes, coordinated bundle, independent/cross-architecture restore | full ARM64 ledger and restore passed; AMD64 release qualification pending |
| G — production candidate | full security/functional matrix, install/upgrades, boundary/accounting/docs gates, release identity | branch closeout automated gates passed; manual tour and AMD64 release qualification pending |

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
- The Odoo MCP had no `/documents/mcp` endpoint or Documents tools; Checkpoint
  D resolves this gap.
- There is no per-user Gemini key boundary.
- Ollama and MCP are isolated for QA but are not yet members of a portable,
  digest-bound release cohort.
- The current QA overlay is intentionally partial and cannot satisfy source or
  release parity gates.

## Checkpoint C evidence — search-first Documents workspace

The implementation reduces the primary navigation to a governed working set.
Home contains only prominent library/evidence relationships that are starred,
recently opened, need attention, or were added recently. My library retains
all accessible library/evidence relationships. Business views follow, while
Inbox and All archived are manager-only. Archive search opens empty until the
user supplies text or a facet. Users can explicitly include, exclude, or show
only background archive material without changing the Odoo authorization
scope. The former Needs review and Recently added identities remain inactive
for saved-session compatibility.

Three credible presentation designs were compared:

1. Keep the native `ir.attachment` bridge, one Paperless root, and the existing
   `usl.document.link`, while storing a library/background/evidence role on the
   relationship and private star/recent state in Odoo. This is selected. It
   preserves immediate Odoo uploads, one checksum/version identity, the
   current queue and retry contract, and record-rule-aware per-user state.
2. Create a second library/favorites archive or re-upload a background file
   when the user chooses **Keep in Documents**. This was rejected because it
   duplicates ingestion, versions, checksums, retention, and business links;
   retries could then produce two competing archive roots.
3. Encode library membership and personal stars as Paperless tags or Saved
   Views. Shared tags remain credible for business classification, but they
   were rejected for presentation state because one user's preference would
   mutate shared metadata and could reveal or alter another user's working
   set. Paperless also cannot enforce the Odoo relationship/evidence rule that
   prevents required evidence from being demoted.

The selected bridge retains the native chatter attachment and one governed
archive identity. **Keep in Documents** is available when policy permits and
starts the idempotent asynchronous archive operation: matching content and
classification reuse a root, while later changed content on that same source
attachment becomes a version. The document-detail action is different: it only
promotes or demotes one accessible presentation link, and evidence remains
prominent. Neither path deletes the native message attachment or creates a
second competing archive root.

Restored QA databases may have all crons disabled by neutralization. The QA
bootstrap explicitly re-enables only the three Documents synchronization,
attachment-queue, and ingestion-poll schedulers. Product UI refuses a new
**Keep in Documents** request when either required worker is paused, instead of
showing an operation that can never progress. This is preferred to activating
the schedulers from a module migration: a migration would silently override an
administrator's intentional production pause on every update, whereas the QA
bootstrap is an explicit environment-recovery action and the product check
leaves the original attachment safe and explains the operational remedy.

The final caught-up tree contains 172 Python test methods. Odoo executed 174
post-test wrappers and reported 180 `usl_documents` entries, with zero
failures and zero errors, after focused regressions for private
state, read-only accounting access, manager-only views, empty archive search,
background visibility, multi-company scope, attachment promotion,
root/version identity, unchanged policy/synchronization writes, and trusted
generated-final provenance, observable attachment ingestion, exact-document
opening, paused-worker handling, and both direct and archive-context
multi-company polling. Frontend validation passed 35 desktop tests with 246
assertions and 30 mobile tests with 229 assertions, with zero Odoo failures or
errors. Two consecutive scoped module updates installed
`usl_documents saas~19.3.1.7.10`; Python, XML, JavaScript
asset, translation, manifest, production-model Ruff, shell-syntax, static, and
product/migration source and database checks passed.

On the caught-up 872-root ARM64 QA archive, native Paperless lexical search
had an 88.5 ms median (524.2 ms first call), while its bounded scoped endpoint
had a 23.0 ms median. Odoo **Everywhere** exact results took 162.8 ms cold and
44.3 ms median warm; the cold hybrid server path took 2.95 seconds and its
briefly cached repeats took 39.0–44.6 ms. Direct semantic retrieval remained
the slow component at a 3.87 second median. The UI therefore keeps the exact
request and visible results independent, then refines them semantically with
the explicit in-progress banner.

The reported QA attachment was recovered without replacement: Odoo attachment
1922 remains on project task 671, operation 1768 completed, and it points to
one accessible Documents root (Odoo 928 / Paperless 880) with one active task
relationship and no competing current-checksum root. A Valentin-context probe
returned the completed status and an exact-document workspace action. The
restored attachment backfill is complete, all three Documents schedulers are
active, and no ingestion operation remains active.

The final base catch-up integrates `19-usl` at
`65b9bd8827060a72cb42c10ef7875a4766a83f67` with a merge commit. Rebasing the
feature's existing product-history commits was also considered, but rejected
because it would rewrite already reviewed history and increase replay risk
without changing the review diff. Conflict resolution retained the feature's
newer asynchronous attachment queue, `usl_documents` 1.7.10, scoped semantic
Paperless image, metadata-cache reuse, and exact-document UI. Mainline's newer
route cleanup, bank-statement Documents integration and 1.4.1 accounting
bridge, explicit Python reconstruction importer, and associated safety tests
were retained. Both multi-company ingestion tests were combined. The first
caught-up full gate exposed that switching from a broad scheduler user to a
single-company submitter retained the scheduler's `allowed_company_ids`.
Record validation now explicitly uses the operation company in both the
legacy direct-link and current archive-context paths, and both regressions
pass. Mainline's
older synchronous attachment upload, 1.3.6 Documents manifest, and unpatched
Paperless image were dropped because the branch implementations supersede
them; the feature's older reconstruction entrypoint form and older accounting
bridge version were dropped because current mainline supersedes them.

The exact authoritative dump was reconstructed in the isolated project with
the deterministic `documents-smoke` profile. Eight selected source identity
groups produced seven live Paperless roots, with eight checksum validations,
permission write/readback, HR restriction, and cross-company supersession all
passing. The sealed restore evidence SHA-256 is
`dd955252deedc444414b7d31764c751ce93e7643c5de1a966320be9c8153945e`.
The final caught-up database boundary passed with 14 product modules and no migration
registry, schema, menu, field, model, or XML-ID residue. This partial profile
is deterministic QA evidence only; it is not complete source or release
parity.

The first caught-up database-boundary run failed honestly because the older QA
database did not yet have mainline's new `usl_b2c` and `usl_documents_b2c`
product modules. Their install and unchanged repeated update passed, after
which the 14-module boundary passed. Two exact stale Documents-only disposable
test databases were then removed because their unconfigured cron workers were
polluting logs; `odoo_dev` was never a deletion target. One rebuild invocation
also defaulted to the repository-wide Odoo image tag. It was interrupted during
preflight before database mutation and rerun with the explicit
`usl-odoo-paperless-193-0824-odoo:latest` tag; the running QA container uses
that isolated image.

Post-reconstruction bootstrap found that Compose rendered
`POCKET_ID_EXTRA_USERS_JSON` as YAML flow syntax and removed its JSON quotes.
Both Odoo service declarations now use block scalars. The private env file,
rendered Compose configuration, and recreated runtime each parse the same
eight-user JSON list, and the focused 18-test Pocket/Compose suite passes. The
final idempotent bootstrap synchronized seven roots, retained one trashed root,
and produced 18 Odoo document roots, 26 active links, 19 versions, two Trash
items, and two Needs-attention items.

Four real-service attempts then reached the unchanged three-minute operation
limit. Live probes showed that every policy ensure issued an unconditional
Workflow API `PUT`, and that an unchanged full cache refresh invalidated every
document's already synchronized permission state. Paperless consequently
scheduled redundant bulk re-indexing, including the 334,457-character source
document, ahead of the acceptance upload. Three alternatives were compared:

1. Compare the exact supported owned workflow fields before writing, and mark
   unchanged cache writes so they do not invalidate permissions. This was
   selected because it removes the work at its source while still repairing
   actual policy drift. Live before/after probes produced zero remote
   permission calls and zero new tasks when state already matched.
2. Raise Paperless worker or Ollama parallelism. This was tested and rejected:
   it changes the release resource assumption, can amplify memory contention,
   and merely processes needless writes faster. The final pass uses three
   ordinary task workers from the reconstruction profile and no
   `OLLAMA_NUM_PARALLEL` override.
3. Move the catalog probe later or extend the operation timeout. This was
   rejected after an empty queue proved the catalog creates no global index
   work. Either change would conceal the idempotency defect while preserving
   avoidable archive churn.

The same acceptance exposed that a final generated accounting attachment was
initially queued with generic attachment provenance. The retained correction
captures the trusted `generated_final` origin as `odoo_generated`, commits the
durable operation, and invokes the normal archive worker; relabeling the result
after ingestion or bypassing the queue was rejected because either would make
retry and provenance evidence untrustworthy.

With the original catalog-first order, the default Ollama runtime, and the
pinned `usl-bge-m3:documents-20260824-rc1` alias, the complete real-service
acceptance passed in 10 seconds against Paperless 3.0.5/API v10. It ended with
`document_id=23`, `paperless_id=24`, two versions, one active relationship,
and an integrity-clean manifest. Direct identity checks saw 42 roots as archive
admin, 9 as ordinary Documents user, 5 as accountant, 9 as HR, and 0 as the
restricted user. Every identity saw the three shared views and every ordinary
identity received HTTP 403 on shared-view mutation. The isolated outage test
persisted resume page 1 while Odoo business data stayed available; recovery
preserved all 44 identities and returned synchronization to healthy.

No task is active. Tantivy reports that its index is current. The local vector
index has schema 2, 1,024 dimensions, the pinned model identity, 948 chunks,
and 46 root metadata rows. Two historical task failures remain visible rather
than being deleted or acknowledged: task 26 is the earlier Ollama EOF from a
diagnostic load run, and task 50 is the already classified ephemeral upload
whose temporary file disappeared during container recreation. Both predate
the final clean acceptance and recovery evidence.

Manual desktop/mobile SSO browser evidence is still required before creating
`codex/checkpoint/documents-search-ux`. A local one-time Pocket ID link is a
sensitive authentication action and is not generated or entered without
action-time user confirmation.

## Checkpoint D evidence — governed Documents MCP

Validated Odoo/Paperless commits are:

- `03ef6297d59` — versioned Paperless similar-document retrieval with a
  mandatory authorized candidate scope;
- `9ce5cbb3c38` — the current-user Odoo Documents MCP facade and its security,
  output-bound, outage, company, and guessed-ID tests;
- `d5101a44b60` — semantic chunk source-identity correction and isolated
  unsynchronized-root regression, released as
  `usl_documents saas~19.3.1.7.1`.

The independent Odoo MCP branch contains:

- `eaaabbc834c34787799fe99b281f2fa075c18dec` — nine read-only Documents tools,
  `/documents/mcp`, full-surface composition, `DocumentsAgent` Durable Object
  migration `v3`, and `SERVER_VERSION=0.20.0`;
- `b9931e9fd12ff51ba29e6b886edd7eb780a2be47` — the accepted security,
  disclosure, capacity, deployment, client-refresh, and rollback boundary.
- `324db9deec20951e8a69d0baa351da070fb49053` — accessible shared/personal
  saved views and scoped replay across Documents retrieval.
- `c22538006c521ab981c0d5ee3c3c5ff20e8e8b83` — the complete ten-tool
  contract and current qualification/deployment documentation.

Three credible authorization designs were compared:

1. Explicit public `usl.document.mcp_*` methods plus a dedicated Documents
   tool module and fourth Durable Object are selected. Each call executes as
   the current Odoo user; record rules, allowed companies, linked-record ACLs,
   live archive state, and synchronized binary permission are resolved before
   Paperless. Missing, guessed, and inaccessible IDs share one denial.
2. Reusing generic `call_model_method` or raw read tools was rejected. It would
   expose implementation methods, make OCR/output bounds optional, and blur
   the deliberately indistinguishable document-denial contract.
3. Calling Paperless directly from the Worker was rejected. A Worker-held
   service token would create a second authorization plane, bypass current
   Odoo company/record rules, and expose a credential that the existing BYO
   Odoo connection does not need.

For similar-document retrieval, the selected `semantic-search-api-v2` overlay
accepts exactly one of a text query or an authorized source document, derives
the source text inside Paperless, and intersects candidate roots before vector
retrieval. Paperless's public `more_like_id` path remained a credible lexical
alternative but was not used because its native helper has no service-scope
contract, no distribution API schema, and no bounded semantic excerpt
response. A second Odoo vector store was also rejected because it would split
embedding/index lifecycle from the Paperless-owned archive.

The exact final Paperless image passed 12 API tests in a network-disabled
container. It runs healthy with image ID
`sha256:9bac20850d764127a3f8dad98b8885ace9e62559cd47c0752c79d858832dae5f`,
base-digest label
`sha256:65a4cabf0169ea7fbd90ab7bb28ba3f8b5909613635acda1a03ad606f34b456b`,
and overlay label `semantic-search-api-v2`. The final focused Odoo MCP gate
passed 10 methods, 14 reported entries, 771 queries, zero failures, and zero
errors. The repeated fresh installation passed 149 methods; Odoo reported 155
`usl_documents` entries and 9,033 queries, plus 31 desktop tests/215
assertions and 28 mobile tests/207 assertions. Two consecutive upgrades to
`1.7.1`, scoped Ruff, Python compilation, and diff checks passed.

The caught-up MCP repository passed TypeScript typecheck and 1,211 tests. Two
consecutive Wrangler dry-runs produced the same compiled `index.js` SHA-256:
`3e65c76922223561d799cbe9ae67b4a1c923745c59b05a58786e831b5ecbf0ed`.
The pushed MCP tip is
`c22538006c521ab981c0d5ee3c3c5ff20e8e8b83`, based on current main
`394124bf1dc0e28b7faa1f0a9c78f31180534478`, with server version `0.22.0`.
It exposes ten read-only Documents tools, including accessible shared/personal
saved-view discovery and scoped replay across browse, exact, hybrid, semantic,
and similarity retrieval. Local readiness reached
`/mcp` and `/documents/mcp`, which correctly returned HTTP 401 without a user
credential.

Real MCP Inspector acceptance listed the governed `documents.*` tools on
the focused endpoint. A short-lived API key for the existing QA Documents user
completed hybrid search, metadata, an 80-character OCR page, local
similar-document retrieval, versions without checksums, tag catalog, and
governed links. The hybrid result carried lexical/semantic provenance, bounded
500-character excerpts, and clickable Odoo URLs. The key was removed after the
run and was never printed, persisted, or included in evidence. Automated Odoo
tests cover restricted-user, other-company, guessed-ID, unsynchronized-root,
output-size, Paperless outage, and embedding-degradation paths.

The first complete fresh Odoo gate found two defects rather than hiding them:
semantic result merging shadowed the source variable and widened the second
10,000-root chunk from `[10001]` to `[1, 10001]`, while one negative test
silently depended on an already populated QA database. Production merging now
uses a distinct candidate ID and the test creates its own synchronized control
root with exact ID comparisons. Focused regressions and the complete clean gate
then passed. Earlier harness failures—Redis-dependent Paperless tests, an
incorrect effective-content method name, absent Ruff in the runtime image,
Wrangler sandbox cache writes, and a reserved shell variable in the Inspector
harness—were corrected at their respective boundaries without weakening a
test. Temporary Inspector keys were explicitly removed.

`deploy/documents/qa.env` and the pre-production template now carry only
non-secret MCP release settings. `scripts/documents-mcp` verifies the
independent clean checkout, tests and bundles the Cloudflare Worker, writes the
non-secret artifact identity, starts isolated Wrangler state, and validates
readiness. The deployment/rollback, capacity, content disclosure, Inspector,
and client refresh procedure is maintained in
`docs/operations/documents-mcp-runbook.md`. The Worker remains outside Compose
and the Paperless service credential remains behind Odoo.

## Checkpoint E evidence — personal Gemini opt-in

Three configuration designs were compared. Paperless's native global LLM
configuration was rejected because one administrator-controlled key would be
shared across users and could not prove personal consent, ownership, or
independent revocation. An Odoo-hosted key vault and generation proxy was
rejected because it would create a second secret/authorization plane, couple
Paperless generation to Odoo, and invite the explicitly excluded Odoo chat UI.
The selected design is a supported Paperless Django app plus a compiled,
exact-source Angular overlay. Runtime DOM injection remained technically
possible but was rejected because it bypasses component tests and
localization, and would be brittle across Paperless upgrades.

Every active governed Pocket-mapped internal user—including ordinary and
read-only identities—may configure only their own profile. Anonymous, portal,
inactive, unmapped, and service identities are excluded. Both metadata
suggestions and Paperless document chat default to off and have independent
toggles. The provider is fixed to Gemini, the endpoint is the official
OpenAI-compatible Google endpoint, and only stable `gemini-3.7-flash` and
`gemini-3.6-flash` identifiers are accepted. Native global generative fields
are cleared by migration, read-only in the API, hidden from global settings,
and rejected by the release check.

User API keys use AES-256-GCM envelope encryption with a randomized data key
and a versioned master-key wrap. Associated data binds the credential to its
Paperless user and revision. The master-key ring is mounted as a read-only
Docker secret and only its path is present in process environment. The key is
write-only and is never returned, redisplayed, placed in browser storage,
exported, copied to Odoo/MCP, or chained through a provider exception. Disable
and delete are immediate; old master-key versions are lazily rewrapped to the
active version.

The first live startup exposed that Paperless interprets any environment name
ending in `_FILE` and copies that file's contents into process environment.
No value was logged, but the design boundary was violated. The variable was
renamed to `USL_PERSONAL_AI_MASTER_KEYS_PATH`, an automated release check now
rejects every legacy inline form, and the container was rebuilt and recreated.
Its startup now reports `No *_FILE environment found`; a value-free in-process
assertion proved the inline variables absent.

The exact final image is
`sha256:3ba338b00385a203a07296dac9be41aacbed2bcce4b317a7be206c570c1f05c5`
with upstream revision `8fb73b2709e4c38180a7632edf32f32fe2315961`, base
digest `sha256:65a4cabf0169ea7fbd90ab7bb28ba3f8b5909613635acda1a03ad606f34b456b`,
and overlay label `semantic-search-api-v2+personal-gemini-v1`. The source
archive and Node builder are checksum/digest pinned, and every modified
upstream backend/frontend file has a SHA-256 guard.

The final Django gate passed 20 tests. It covers default-off and independent
activation, mapped/unmapped/service eligibility, cross-user and admin
isolation, fixed model/endpoint behavior, write-only responses, randomized
envelope encryption, user/revision binding, master-key rotation, immediate
disable/delete, read-only document use, pre-stream authorization, global
fallback removal, credential-free provider failures, and rejection of inline
master-key environment. The targeted Angular Jest suite passed two tests for
default-off/no-secret state and replacement-key clearing. The all-locale
production build passed; the French bundle contains the maintained settings,
placeholder, action, and privacy strings. Strict Ruff, Python compilation,
shell syntax, XML parsing, Compose rendering, migration drift, health, and
release-boundary checks passed.

The isolated QA database migration applied cleanly and the service remained
healthy. Its recoverable pre-migration dump is
`/private/tmp/usl-paperless-pre-personal-ai-20260825.dump`, SHA-256
`0e040574ada49d6ca80c9cd5acf3ba81b12bb0a2cfc98bddfce27a58b1ac1e08`.
No personal or master key was printed or added to Git. No real Gemini API key
was supplied, so the provider request itself was validated with a mocked
models-only call; live document generation is deliberately not claimed.
Manual desktop/mobile **My profile** evidence remains required before creating
`codex/checkpoint/personal-gemini-opt-in`.

## Checkpoint F evidence — portable release cohort

Checkpoint F is **partial** and its pointer has not been created.

The migration role backfill assigns presentation roles from durable Odoo
business relationships while preserving the Paperless root, version chain and
user-managed metadata. Its first live pass changed 19 relationships. Emitted
ledger review caught one evidence relationship that a single-link query would
have demoted; the corrected monotonic/all-active-link resolver changed two
records and an unchanged repeat changed zero. The focused migration suite
passed 55 tests.

Two release designs were rejected in favor of the coordinated cohort. The
official Paperless exporter remains useful but cannot preserve live Tantivy,
vector or Ollama state by itself. A raw database plus isolated
`llmindex.db` copy cannot prove a coordinated SQLite/WAL snapshot and
would require an unsafe production rebuild. The selected workflow quiesces the
source, snapshots both databases and every authoritative volume, includes the
compiled MCP Worker, sanitizes database clones, records exact identities and
seals all artifacts.

Four failed capture IDs remain diagnostic evidence:

- the first found three official-export social-account records and failed
  closed before the export was sanitized;
- r2 found that Odoo OIDC identities could not be removed before their
  Paperless mapping foreign keys;
- r3 exposed that the Paperless service init ignored the requested Django
  shell and launched a worker;
- r4 exposed a literal `\n` suffix after extracted JSON.

Each defect was fixed forward with a regression test. No failed cohort was
renamed or accepted. r5 then built and restored locally; its old accounting
serialization was deliberately rejected by the new parity checker.

The current diagnostic cohort is
`usl-documents-20260825-partial-arm64-r6`. Its initial manifest was
`ad33ed297de2a8f66429abfbf7d2a6a1cdf6055c990cad51dd2263c8e799703e`.
An independent project restored Odoo, Paperless, Tantivy, the complete vector
database and the BGE volume. All Odoo Documents/accounting counts, Paperless
stable IDs, vector counts/digest, BGE manifest and Tantivy no-op output matched.
No OCR, re-ingestion, vector rebuild or model download ran. The recovery
evidence passed and the final resealed manifest is
`c3b811e90840b3bc1d69866e80140fd30d7e97393ea604988c034fb9b7501134`.
The disposable restore containers, network, volumes and copied credential file
were removed.

The acceptance gate still rejects the cohort with exact source evidence:

- 840 eligible attachments pending and 536 unresolved;
- one failed, 180 pending and 23 processing Odoo archive operations;
- missing C and E manual browser checkpoint pointers;
- missing F/G and MCP production-candidate pointers;
- arm64 images for a declared `linux/amd64` target.

Paperless itself has 46 stable document identities (IDs 1–47), 44 live and two
in Trash, with no active tasks and no personal Gemini profiles. The vector
index has schema 2, 948 chunks/vectors, 46 document metadata rows, dimension
1,024, chunk size 512 and overlap 200. The BGE alias manifest digest is
`7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab`.
Odoo has 44 roots, 29 active links and 46 versions. Accounting has 5,258
posted moves and 12,991 move lines; posted debit and credit both equal
2,900,936.82.

Full reconciliation/OCR was not run merely to make the counters disappear.
The active errors require source-wide classification and a deliberate bounded
archive run. The amd64 gate requires target-platform artifacts and a real
target-architecture restore. Until both are complete, this local cohort is
useful recovery-mechanism evidence but must not be published or described as
production-ready. The detailed procedure is in
[Documents release cohort](documents-release-cohort.md).

## Source-completion follow-up after diagnostic cohort r6

The operator subsequently authorized the bounded full native-attachment run in
the same isolated project. This does not amend or relabel diagnostic cohort
`usl-documents-20260825-partial-arm64-r6`; that cohort remains an immutable
record of its earlier partial source state and a valid recovery-mechanism
rehearsal only.

The final bridge checkpoint completed all nine Paperless archive pages and
reported 1,623 scanned native attachments. Of these, 837 were eligible and all
837 reached an archived final ledger state: 794 evidence and 43 background.
The other 786 are explicitly classified exclusions: 757 binary/image-field
storage records, 14 inline or placeholder images, six inline message images,
and nine unsupported archive formats. There are no unaccounted attachment
IDs, accepted Trash conflicts, pending eligible attachments, unresolved
eligible attachments, pending operations, processing operations, or
permission failures.

Odoo retains 867 governed archive roots, 869 versions and 917 active business
links. The 869 Paperless document rows consist of the same 867 roots plus two
native Paperless version-history rows: document 17 is version 1 of root 12 and
document 44 is version 1 of root 24. A direct identity-set comparison found no
Odoo root missing from Paperless. The apparent two-row difference is therefore
version history, not an orphan or duplicate root.

Four raw failed Odoo operation rows remain visible as audit history:

- operation 908 is the deliberate `qa-corrupted-upload.pdf` synthetic failure;
  it has no source attachment and remains the single qualification blocker;
- operations 1662 and 1663 are the two historical Terms of Service HTML
  attachments, both finally classified `unsupported_archive_format`;
- operation 1694 is the 1,407-byte, 68×69 `Instagram.png` placeholder, finally
  classified `inline_or_placeholder_image`.

Paperless likewise retains five historical task failures. Task 26 records the
initial local embedding-service EOF before the model was ready. Task 50 records
an interrupted synthetic supplier upload whose temporary file disappeared on
service recreation; its exact checksum and metadata were later found as stable
archive root 9 and the stale Odoo operation was reconciled. Task 1173 is the
same explicitly excluded Instagram placeholder. Tasks 2324 and 2325 are
system-owned bulk-index jobs for documents 845 and 846 that reached the
1,800-second hard limit while two workers redundantly embedded the same
254,756-character spreadsheet and a third job waited for the model. The final
supported incremental update brought both documents' index modified times to
an exact match with PostgreSQL, and the complete vector inventory passes.
None of these rows was deleted, rewritten or marked successful to make a gate
pass.

The remediation decisions compared credible alternatives individually:

1. Converting HTML to PDF would make the Terms of Service files OCRable, but it
   would change their legal bytes. Exact-byte retention with an explicit
   unsupported-format classification was selected.
2. OCR or image conversion could force every tiny icon through Paperless, but
   the audited sample is presentation noise. A bounded 4 KiB placeholder rule
   was selected while retaining the native Odoo bytes.
3. A global ACL bypass or migration-owned authorship would simplify backfill,
   but would weaken runtime authority and provenance. A sudo-only trusted
   backfill context was selected; access is derived from the linked business
   record while the real submitter remains the author.
4. Relying on Odoo's implicit attachment-domain context is concise but hides
   field-backed attachments. Two explicit public domains—`res_field = False`
   and `res_field != False`—were selected so every native row is accounted for.
5. Polling newest operations first favors recent uploads but can starve old
   failures. Oldest-first polling was selected.
6. Blindly reusing any later archive could join unrelated records. Reuse is
   limited to an exact checksum and metadata fingerprint; otherwise the
   historical failure remains unresolved.
7. Acknowledging or deleting raw failed rows would make reporting green but
   erase evidence. The release inventory keeps the raw total while qualifying
   only failures whose final source ledger is neither archived nor explicitly
   excluded.
8. Purging or manually deduplicating the Celery queue would finish faster but
   discard supported index lifecycle work. The supported workers are allowed
   to drain naturally, followed by the native Tantivy and LLM-index no-op/
   consistency commands.

The authoritative input was re-hashed immediately before evidence capture:
`/Users/roger/projects/odoo/usl-online-dump/dump.sql` is
`ad313e28586fafa27a4f6a266df57080456613dff1c8c2c6d7e012732bf633b1`.
The source-complete QA database remains **partial** for release qualification
because the deliberate corrupted-upload operation is still a real blocker,
manual C/E browser checkpoints are absent, and the target `linux/amd64`
recovery rehearsal is still required. Source completion does not waive any of
those independent gates.

### Source-completion validation

The complete fresh Odoo server gate passed 151 post-test methods; Odoo reported
159 tagged entries, zero failures and zero errors. The explicit migration
archive suite passed 71 tests, and the focused runner-safety suite passed 18.
A fresh browser database passed 28 desktop tests with 207 assertions and 28
mobile tests with 207 assertions. The focused multi-company gate passed all
nine selected methods—13 reported entries—including company access loss,
cross-company rejection, Trash identity, ingestion boundaries and MCP search
pre-scoping.

Clean installation, update and repeated update completed. XML parsing,
manifest/version parsing, Python compilation, JavaScript/static checks, shell
syntax, scoped Ruff, `git diff --check`, the product/migration source boundary
and the product-database boundary all passed. The database boundary found 12
product modules and no migration registry, schema, menu, field, model or XML-ID
residue. The existing deterministic `documents-smoke` reconstruction remains
sealed at
`dd955252deedc444414b7d31764c751ce93e7643c5de1a966320be9c8153945e`;
it was not rerun because doing so would reset the newly source-complete isolated
database. The earlier r6 independent restore remains recovery-mechanism
evidence, not final source-complete or target-architecture evidence.

The first post-ingestion live acceptance attempt passed every existing-fixture
step but timed out waiting for its deliberately ephemeral matching-rule upload
behind the index backlog. Its Paperless task 2379 later completed successfully
as document 872 and that exact synthetic document was cleaned through the
normal Trash/permanent-delete API. After true broker/active/reserved
quiescence, the unchanged acceptance passed end to end, including live search,
version pinning, automatic matching, Trash/restore, permissions,
multi-company isolation and cross-system integrity.

The outage test stopped only the isolated Paperless webserver. Odoo business
data stayed available, synchronization failed closed and retained resume page
1. After Paperless returned healthy, recovery preserved all 867 root
identities, synchronized the resumed page, cleared the cursor and returned sync
status to healthy.

Paperless's sanity checker exited successfully with 14 informational no-OCR
records, accounting exactly for 855 searchable rows among 869 document/version
rows. Tantivy reported `Search index is up to date`; optimize was its documented
no-op. Vector migration passed, the first incremental update reconciled real
modified-time drift, a second update completed in four seconds with no
remaining drift, and compaction retained 4,339 live rows. Final Paperless
inventory is passed with zero active tasks, 865 live roots, two Trash roots,
two version-history rows, 869 document/version rows and five preserved
historical failures. Final vector inventory is passed with 4,339
chunks/vectors, 869 indexed documents, schema 2,
dimension 1,024, chunk size 512, overlap 200, pinned model
`usl-bge-m3:documents-20260824-rc1`, and index SHA-256
`3145e8d1eb757a79100c692b648e9dd545c4c6baa89cef3aa6580306d78c907e`.

The final integrity manifest reports `integrity_ok=true` and healthy sync with
no missing document IDs, checksum mismatch, orphaned relationship, permission
failure, unmirrored Paperless ID or permanent-deletion tombstone. Relationship
counts are six assets, 444 accounting moves, seven employees, 358 expenses, 47
projects, 45 tasks, two companies and eight partners.

One operational incident is retained in the run record. Interrupting two
one-shot bridge commands detached their containers instead of stopping them;
both exact feature-project containers were identified and stopped. One
concurrent insert then reached the unique constraint for Paperless ID 349; the
constraint prevented duplicate state, and the final single-worker bridge and
integrity manifest passed. No container, volume, database or port outside
`usl-odoo-paperless-193-0824` was used.

### 2026-08-26 QA identity and permission follow-up

The source-complete database did not initially contain governed Pocket
identities for every manual persona. Six synthetic identities—manager, ordinary
user, read-only reviewer, restricted user, HR user and two-company user—were
provisioned in the existing isolated Pocket tenant, mapped through the official
Odoo/Paperless identity workflow and synchronized without changing source
business records. The final identity boundary covers all nine governed human
users, with zero unverified mappings and zero unsynchronized visible roots.
Personal Gemini remains opt-in and has zero configured profiles before manual
QA.

That first source-wide permission synchronization also exposed unnecessary
upstream task fan-out. Paperless's standard `set_permissions` bulk-edit method
always schedules its supported search/vector refresh. Calling it once for each
of 865 live roots therefore created 865 tasks even though most roots shared an
identical ACL. Three alternatives were compared:

1. Keep the one-root standard API calls. This is correct but produces avoidable
   queue and embedding work, so it was superseded.
2. Group roots with an identical Odoo-derived ACL and send bounded 100-root
   calls through the same standard Paperless endpoint. This is selected. It
   retains supported lifecycle behavior, deterministic Odoo authorization,
   fail-closed error state for every root in a failed batch and a small request
   bound.
3. Add a custom permission-only Paperless endpoint that skips index refresh.
   This could avoid more work, but it was rejected because it would introduce
   a second security-sensitive write contract and bypass Paperless's normal
   lifecycle.

While the pre-existing per-root queue was draining, nine tasks—Paperless task
rows 2420–2427 and 2429—failed with `attempt to write a readonly database`.
The vector database and its WAL/SHM files were owned by root because earlier
release management commands had used the container default user; the worker
runs as `paperless`. Deleting or rebuilding the index would discard transferable
index state and was rejected. Ownership was corrected only on those three
files, the supported queue was allowed to drain, and all release, recovery,
seed and QA importer commands that write Paperless volumes now run explicitly
as the `paperless` runtime user. The nine failed audit rows remain failed; a
final supported incremental vector update reconciles their document state
without rewriting task history.

Odoo module `usl_documents` is deployed as `saas~19.3.1.7.2` in the isolated
QA database. A fresh full backend run reports 153 post-test methods and 161
tagged entries with zero failures or errors; the bounded batching regression
also passes independently. Installation, update, repeated update, the 19-test
runner-safety suite, scoped Ruff, compilation, shell syntax, source boundary
and 12-module product-database boundary all pass. The manual tour and current
SSO persona paths are maintained in
[Try the Documents application](../users/guides/test-paperless-documents.md).

### 2026-08-26 current-mainline integration and final index evidence

Current `19-usl` advanced by fast-forward from `e3b64c209acf` to
`f302ae6cdb43`. Merge `8658e0bec4a` integrates that exact successor without
rewriting the completed Documents replay or importing the archived saas~19.2
lineage. The complete fresh Documents gate then passed 156 post-test methods
with zero failures or errors (162 Documents entries), including 31 desktop
tests/215 assertions and 28 mobile tests/207 assertions. The expanded
migration, recovery, seed, cutover and runner-safety suite passed 99 tests.
Manifests, XML, maintained French catalogs, Python compilation, shell syntax,
scoped Ruff and the product/migration source boundary also pass.

The supported Paperless queue drained to true quiescence: broker depth zero,
zero nonterminal `PaperlessTask` rows, and zero active or reserved Celery work.
The final native sanity check reports only the expected 14 informational
no-OCR records; Tantivy is current and its optimize command is the documented
no-op. A full LLM-index update, compaction and second no-drift update were run
as the `paperless` runtime user. Final inventory is passed with 869 total
document/version rows, 867 live rows, two Trash rows, 855 searchable rows,
zero active tasks and zero Personal Gemini profiles. Vector inventory is passed
with 4,339 chunks/vectors for all 869 rows, schema 2, dimension 1,024, chunk
size 512, overlap 200, model `usl-bge-m3:documents-20260824-rc1`, and SHA-256
`9141f60a17f51bc5f9d60793ffee063772c74014778f211ca88ee2eed317d20b`.
The SQLite database, WAL and shared-memory files are all owned by
`paperless:paperless` with mode `0644`. Personal Gemini's release boundary
passes as `qa-personal-ai-2026-08:1`.

Fifteen Paperless failure rows remain as immutable audit evidence. The five
original failures and nine read-only-index failures are documented above. The
additional task 2850 was an already-running bulk update whose Ollama HTTP
connection closed when the QA SSO runtime repair recreated Ollama at 22:28 UTC.
It failed with `RemoteProtocolError: Server disconnected without sending a
response`; no document, permission or vector row is missing, and the supported
final update reconciled its state. The row was not acknowledged, deleted or
rewritten.

The source module version is now `saas~19.3.1.7.3`. The retained Documents
adapter in `usl_expense_batch` inherits `usl.document.link.mixin`, so its
manifest must declare `usl_documents`. Retaining mainline's whole manifest
without that feature dependency caused the registry to fail closed before any
module update. After explicit operator approval, the missing dependency was
restored on top of mainline's Expense assets, security, translations and
version history; `usl_expense_batch` is now `saas~19.3.1.2.6`. Moving the
adapter into a new optional bridge module was rejected because it would create
a new delivered module and data-ownership transition during a bounded replay.

### 2026-08-26 final deployment, test and recovery evidence

The dependency correction was deployed first to `usl_expense_batch`, then to
`usl_locale`, `usl_documents` and `usl_documents_accounting`, followed by an
unchanged repeated combined update. All three updates loaded the complete
100-module registry and returned a healthy runtime. Installed versions are
`usl_documents saas~19.3.1.7.3`, `usl_documents_accounting
saas~19.3.1.2.1`, `usl_expense_batch saas~19.3.1.2.6`, `usl_locale
saas~19.3.1.3.0` and `usl_pocketid saas~19.3.1.1.0`.

A focused backend invocation against the persistent QA database was invalid:
the suite creates `documents-user`, which that database already contains, so
PostgreSQL rejected the duplicate login before the selected tests could run.
The tests and QA users were not changed. The complete module gate was rerun in
the disposable `odoo_documents_final_20260826` database and automatically
cleaned afterward. It passed 156 post-tests with zero failures or errors and
162 Documents entries, including the multi-company cases, 31 desktop browser
tests/215 assertions and 28 mobile tests/207 assertions. Runner safety now
passes 24 tests. Manifest parsing, XML parsing, Python compilation, shell
syntax, changed-file Ruff, `git diff --check`, source boundary and the live
12-module product-database boundary also pass. A broad informational Ruff run
reported 23 pre-existing findings only in untouched files; they were not
rewritten as unrelated formatting churn.

The final recovery rehearsal exposed and corrected two fail-closed tooling
gaps. First, an isolated ARM64 development project must use its mounted add-ons
while a true `usl-odoo-preprod-*` project must retain the immutable-image
overlay. The local path now requires both explicit `odoo_dev` recovery flags
and a `usl-odoo-paperless-*` project name. Second, the coordinated backup now
includes the pinned Ollama model volume; downloading a model during recovery
was rejected as network-dependent and weaker proof. The completed rehearsal
restored Odoo independently before Paperless, then passed cross-system
acceptance with 867 document roots, 917 relationships, three companies, 11
active internal users, 5,428 accounting moves, 12,991 move lines, balanced
posted debit/credit of `2900936.82`, a checksum-verified Paperless binary and
preview, source/restored integrity true and zero permission-sync failures. Its
temporary project, volumes and artifacts were deleted automatically, and the
source QA stack returned healthy.

The final live identity gate covers all nine governed human users with zero
unsynchronized visible roots. Odoo inventory remains deliberately partial only
because operation 908, `qa-corrupted-upload.pdf`, is still failed,
unacknowledged and has no source attachment; it was not hidden or weakened.
All archive, relationship, checksum, permission, migration-boundary, queue and
vector checks otherwise pass. Manual browser checkpoints and later
`linux/amd64` production qualification remain operator-owned; local development
and this rehearsal are intentionally ARM64. The sealed deterministic
`documents-smoke` digest remains
`dd955252deedc444414b7d31764c751ce93e7643c5de1a966320be9c8153945e`.

### 2026-08-26 scoped lexical and progressive search qualification

Documents search now presents exactly two meaning-based paths. **Everywhere**
returns the exact lexical result set first and
then appends new BGE-M3 matches without changing the lexical order. **Meaning
(Semantic)** goes directly to the local BGE-M3 index. The French labels are
`Partout` and `Sens (sémantique)`. Specific title, OCR/archive, tag,
correspondent, document-type and custom-field suggestions remain lexical; no
Gemini or other generative model participates in search.

Two credible Paperless resolution strategies were compared. Concurrent calls
to the existing document-list GET endpoint would reduce wall time but would
still execute one Tantivy query per custom field, repeatedly parse the same
authorization scope and split large ID lists across URLs. It was superseded by
one authenticated `POST /api/documents/scoped_search/`: the endpoint
intersects at most 50,000 supplied root IDs with Paperless permissions, applies
an optional structured custom-field filter, and runs one native Tantivy query
over the selected indexed companion fields. A custom standalone search engine
or an Odoo-side text mirror was also rejected because it would duplicate
Paperless indexing, ACL and lifecycle state. MCP may ask the same POST for at
most 50 bounded OCR excerpts; the normal UI response remains ID/rank only.

Two ranking strategies were compared. Reciprocal-rank fusion was rejected
because a high semantic score could move an approximate result above an exact
match. The selected merge retains lexical order as a strict prefix and appends
only semantic IDs not already present. The web client publishes that exact
prefix immediately with a visible refinement banner, ignores stale async
responses, and keeps exact results with a warning if semantic retrieval fails.

Odoo computes the accessible root set once per request and reuses it for
lexical and semantic calls. Paperless receives the lexical scope once in the
POST body. Invariant workspace catalogs and facets are omitted from refinement
RPCs after the initial load. Identical scoped requests use a process-local,
authorization-sensitive five-second cache with a 128-entry bound; the cache
key includes the Paperless URL, token hash, full request body and document
scope, and failed requests are never cached.

The rebuilt Paperless 3.0.5 overlay upgraded only its native Tantivy schema
from 1 to 2, adding indexed archive-metadata and custom-field companion text;
it did not rerun OCR. On the 874-root ARM64 QA archive, the bounded lexical
endpoint returned the exact `INV-QA-2026-0042` document in approximately
394–398 ms from a cold process, 19–20 ms warm, and 10.6 ms for a custom-field
query. Browser qualification displayed that exact invoice first in 855 ms,
then completed semantic refinement while retaining it. A semantic-only
paraphrase returned the intended invoice in 1.081 seconds.

The complete final backend gate passed 161 post-test methods (169 tagged
entries) with zero failures or errors; desktop passed 32 tests/223 assertions
and mobile passed 29 tests/215 assertions. Twenty Paperless endpoint and
permission-vector tests,
module install, module update, unchanged repeated update, manifest/XML/Python/
JavaScript/shell/static checks, the 12-module product/migration boundary and
the live cross-system acceptance all passed. The latter created and recovered
a deterministic document, metadata, custom field, duplicate, version, Trash,
relationship, checksum, permission and multi-company scenario and ended with
874 Paperless roots, two versions and one relationship for its marker. The
full isolated recovery rehearsal also passed with 871 restored roots, 920
relationships, three companies, 11 users, 5,428 accounting moves, 12,991 move
lines, balanced posted debit/credit of `2900936.82`, zero permission failures
and automatic cleanup. The sealed deterministic smoke digest above remains
the reconstruction baseline because neither migration nor reconstruction
source changed.

The final ARM64 Paperless image is `3.0.5-usl.5`, with locally inspected image
and repository digest
`sha256:43a3c471af24fe8241d6d6e47fef8f02f0f3b76094e64a4b40ec3b6225d502fc`
(ARM64 manifest `sha256:153c89f7b88024e75422210fad00d97d08fa028eaf36aeba8e1fff205421ceb1`,
config `sha256:98f1b7848bc6b41bd81dfe5c9c7f9694c108126d851b284da1acd0f3d5b1e874`).
It was recreated in the isolated QA stack, followed by a successful module
update and unchanged repeated update of the 100-module registry. A live MCP
exact search then returned one result with a 182-character bounded lexical
excerpt, and the final source/database product boundary passed with 12 product
modules and no migration registry or schema residue.

Known validation noise is retained: the first translation and Ruff commands
lacked their container dependencies and passed when rerun in the development
image; the first frontend attempt correctly failed because persistent QA uses
Pocket SSO rather than `admin/admin`, after which the runner was corrected to
use a disposable database and passed; two migration-boundary invocations were
rejected by their project-scope guard before the corrected isolated invocation
passed; and a PDF acceptance step emitted a `wkhtmltopdf` host-resolution
warning while still producing and verifying the report. A repository-wide
format check still reports eight pre-existing large-file formatting drifts, so
those untouched areas were not rewritten.

Remaining risks are bounded and explicit. The 50,000-ID lexical scope is ample
for the current archive but must be revisited before that population is
approached. The five-second cache is per Odoo worker rather than shared.
The exact Paperless overlay now also builds for `linux/amd64` at manifest
digest
`sha256:a30e826e471f097df1cb941b69d7379ebb800f4bf07a1daff45f2359d5cb079d`
with version `3.0.5-usl.5` and the expected USL patch label. Its all-locale
frontend build passed and the compiled French bundle contains the maintained
Personal Gemini strings. This is cross-architecture image-build evidence, not
the still-required full AMD64 release-cohort restore. A warm semantic response
can make the progress banner brief, and an unrelated Pocket profile-page console exception
was observed immediately after one-time-link login, with no Documents-page
error observed.

### 2026-08-26 Odoo search hot-path optimization

The live 871-document, 920-link ARM64 QA database showed that Paperless was
not the exact-search bottleneck. For the same `facture` query, native
Paperless measured 83.6 ms warm and its scoped Tantivy endpoint measured
23.4 ms inside Paperless. Odoo exact search measured 214.0 ms warm and
813.2 ms on its first call because Home and Library role filters checked
linked business records one at a time.

Two authorization optimizations were compared. A short-lived per-user cache
of visible link IDs was rejected because target record rules, selected
companies, or link state could change during the cache lifetime. Denormalizing
per-user visibility onto Documents was also rejected because it would create
a second authorization truth requiring broad invalidation. The selected
implementation groups active links by target model and applies Odoo's native
batch access filter to the exact target IDs. It preserves current record rules
on every request, remains fail-closed, and retains inactive-but-readable
business records. The same helper now serves prominent, library, project,
linked-record, and local linked-label searches.

After that change, the same Odoo exact search measured 44.0 ms warm and
134.0 ms on its first call; a plain Home load measured 51.7 ms warm. Native
Paperless semantic search measured 5.10 seconds and Odoo hybrid refinement
4.02 seconds on the ARM64 embedding runtime, confirming that remaining first-
query meaning latency is model inference rather than Odoo overhead. A bounded
30-second semantic result cache was added for identical URL, token, query,
scope, limit, and facet payloads. Scope changes cannot hit the old entry,
failed requests are not cached, and exact results still render before this
background refinement.

### 2026-08-26 permission-reconciliation hardening

Final deployment inspection found three historical permission-refresh tasks
that had reached Paperless's 30-minute hard limit and three interrupted tasks
left by the isolated worker recreation. The root cause was upstream
`set_permissions`: after correctly writing ownership and object grants, its
generic bulk refresh also recomputed BGE-M3 vectors for every document even
though neither permission nor ownership is an embedding input.

Three credible resolutions were compared. Smaller Odoo batches were rejected
because they would still recompute every unchanged vector, multiply task
overhead and retain timeout risk. Temporarily disabling AI during identity
sync was rejected as a stateful operational workaround. A separate custom
permission endpoint that bypassed the native lifecycle was also rejected. The
selected exact-source patch keeps the supported Paperless permission API and
bulk task, including Tantivy, cache and signal updates, but passes an explicit
`skip_llm_index` marker only from `set_permissions`. The default path is
covered separately so ordinary metadata/content bulk edits still refresh the
vector index.

The corrected image passed 20 Django/DRF tests. A complete official identity
reconciliation then synchronized 869 visible roots for Valentin in 14 bounded
permission tasks; all 14 succeeded in 0.20–2.09 seconds and made zero Ollama
embedding requests during their 16:50:04–16:50:17 UTC execution window.
Valentin is active, all nine governed identities pass with zero unsynchronized
visible roots, the broker and Paperless nonterminal task count are zero, and a
deliberate semantic query afterward still returned the intended Alpine invoice
first. Final Paperless inventory is passed with 874 document/version rows, 872
live rows, two Trash rows and 860 searchable rows. Vector inventory is passed
with all 874 rows indexed and 4,344 chunks/vectors; its current SHA-256 is
`f92b6a047a3c08f9d52f1b5a130a2e6d426e35e434866cca341851a8fc4df110`.

Task history was not hidden. The three 30-minute failures (3421, 3423 and
3424) remain immutable. Tasks 3430–3432 record that their worker was restarted
and their permission reconciliation rescheduled; redelivered task 3433
completed successfully. Fourteen redundant pre-patch queued refreshes
(3465–3478) were explicitly revoked before the corrected reconciliation.
There are 21 historical failure rows in total, 66 revoked rows and 3,413
successful rows. Two scheduled mail polls were revoked during the worker
transition; subsequent scheduled mail polls succeeded. No failure was
acknowledged, deleted or relabeled to make the gate pass.

### 2026-08-27 branch closeout evidence

The closeout correction publishes `usl_documents saas~19.3.1.7.16`. Fresh
installs retain the one-minute attachment/ingestion pollers and five-minute
Paperless catalog/identity synchronization. Classification and TESE
relationship reconciliation are immediate on their business events, with
unbounded twelve-hour sweeps only as recovery safety nets. The `1.7.15`
migration repairs the briefly published `1.7.14` fresh-install schedule only
when it still has those exact defaults, and the `1.7.16` migration retires an
old failed/duplicate operation only when its source attachment is now
deterministically `never` archived. Real failures remain visible.

That last rule was exercised against every remaining QA failure. Operations
1662 and 1663 are two `Terms_of_Service_fr_fr.html` files already classified
as `unsupported_archive_format`; operation 1694 is a 1,407-byte
`Instagram.png` already classified as `inline_or_placeholder_image`. Their
obsolete messages are now acknowledged while their native expense attachments
remain intact. Operation 908, `qa-corrupted-upload.pdf`, has no source
attachment and remains the single active synthetic failure scenario. The four
remaining **Needs review** roots are deliberately unlinked external intake
fixtures with no company: Paperless IDs 7, 18, 45 and 877. No failure or review
state was rewritten merely to satisfy a counter.

Final clean-database Odoo validation passed:

- `usl_documents`: 181 post-tests, zero failures/errors; 39 desktop tests with
  268 assertions and 33 mobile tests with 248 assertions;
- `usl_documents_accounting`: 13 post-tests, zero failures/errors;
- `usl_expense_batch`: 30 post-tests, zero failures/errors, including four
  desktop and four mobile tests;
- `usl_tese_payroll`: 27 post-tests, zero failures/errors;
- `usl_platform_billing`: 32 post-tests, zero failures/errors.

The five modules were updated together on `odoo_dev` and updated identically a
second time. The final Documents-only `1.7.16` update and repeat also passed.
Installed versions are `usl_documents 1.7.16`, `usl_documents_accounting
1.4.1`, `usl_expense_batch 1.2.6`, `usl_platform_billing 1.3.0` and
`usl_tese_payroll 1.4.4`. Repeated live reconciliation returned zero newly
classified roots and zero TESE work. The full archive has 872 Odoo roots and
942 active relationships; 10/10 TESE payslips with attachments are linked.

Paperless validation passed 20 scoped lexical/semantic and
permission-vector-invariance Django/DRF tests, 20 Personal Gemini backend
tests, and the two dedicated Personal AI Jest cases. Both non-localized and
all-locale production frontend builds were cache-replayed from the pinned
source and dependency lock. The 106 migration/release/recovery safety tests,
Python compilation, manifest import during clean installation, XML parsing,
shell syntax, maintained French catalogs, changed-file Ruff, diff hygiene,
source boundary and live product-database boundary passed. The product
boundary found 14 product modules and no migration registry/schema residue.
A broad informational Ruff scan still reports 15 pre-existing findings in
older migration and initializer files; the closeout files pass the scoped
gate, and unrelated style churn was not introduced.

The final independent recovery rehearsal exported 875 Paperless documents,
restored Odoo and Paperless from coordinated backups, and auto-deleted its
project, volumes and artifacts. Acceptance passed with 872 Odoo roots, 942
active relationships, three companies, 11 active internal users, 5,428 moves,
12,991 move lines, posted debit and credit both `2900936.82`, a 16,785-byte
preview, source/restored integrity true and zero permission-sync failures. The
source QA stack was restored healthy. The deterministic migration baseline
remains
`dd955252deedc444414b7d31764c751ce93e7643c5de1a966320be9c8153945e`;
the closeout changes do not alter reconstruction inputs or selection.

An automated live Chrome smoke reached Pocket ID with the existing local
identity, but Pocket ID's final **Sign in** action remained in a loading state
on two fresh interactions without a browser console error. No one-time token
was injected into browser automation. The complete disposable-database
desktop/mobile suites passed; a human should still complete the short local
SSO tour before merge. The AMD64 overlay build passes, but production remains
unqualified until the complete runtime-bound candidate cohort is restored and
accepted on AMD64. At target `origin/19-usl`
`65b9bd8827060a72cb42c10ef7875a4766a83f67`, Native Sign is not present; its
future Documents integration must be reviewed when that branch is merged.
