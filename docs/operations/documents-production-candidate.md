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
The isolated Ollama service is exactly `0.30.11` at image digest
`sha256:c484b703176aa19dfc0a54cbfb60ab8094b38faa04283fb77eba1d33319e5eca`.
Its application-facing model is `usl-bge-m3:documents-20260824-rc1`, model
digest `7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab`,
with 1,024-dimensional embeddings. The mutable model volume is isolated; the
standalone container must be converted to tracked Compose configuration before
the release-cohort checkpoint.

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
| B — local hybrid search | exact Paperless image, Ollama BGE-M3, Paperless-owned vector index/API, scoped fusion and outage behavior | Ollama qualified; Paperless API/index planned |
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

## Remaining gaps after Checkpoint A

- Existing links cannot be promoted or demoted independently of the Paperless
  root.
- Home/Recent does not yet suppress background-only roots.
- Search is Paperless lexical plus authorized Odoo labels; it has no supported
  Paperless semantic API or hybrid rank fusion.
- The Odoo MCP has no `/documents/mcp` endpoint or Documents tools.
- There is no per-user Gemini key boundary.
- Ollama and MCP are isolated for QA but are not yet members of a portable,
  digest-bound release cohort.
- The current QA overlay is intentionally partial and cannot satisfy source or
  release parity gates.
