# Agent write-path reliability — 5 September 2026

## Staging integration

Reconciled PR #101 with staging `d1e37d629a15`, including upstream
`4d26b5cfe725`. The policy merge carries 54 exact changed entries from the PR
and preserves the preceding upstream classifications. Compiled protected-action
and Agent read/write lists are unchanged; their qualification digests are renewed
against the combined registry.

The combined 163-module registry passed its module upgrade and all 109
access-control tests, including the active-request precommit regression,
rescheduling boundaries and credential contention. All 444 repository tests
passed with one host-specific skip; source policy and product/migration source
boundary checks also passed. Runtime policy is revalidated before publication.
These checks do not constitute a production rollout.

## Confirmed cause

The earlier operation-scope fix covered model recordsets but not the HTTP
request environment. Odoo's HTML collaboration helper sends notifications via
`request.env["bus.bus"]`, not `record.env`. A task-description update therefore
queued an unscoped bus callback, which failed at precommit even though the task
write was authorized. ORM-only tests missed this because no HTTP request was
present.

The JSON-2 controller now propagates the already-authorized call context using
`request.update_env`. This also supplies Odoo's deferred-computation environment.
The request retains the Agent user and ordinary sudo state. Scope validation,
non-sudo application checks, identity protections and irreversible-action guards
are unchanged. Denied requests never receive a scope; read-only requests strip
client-supplied scope values and receive no mutation scope.

The shared `activity_reschedule` method also lacked the collaboration wrapper
already used for scheduling and completion. Its wrapper requires write access
to the business model and records before allowing native Activity maintenance.
It does not grant direct `mail.activity` or other technical-model mutation rights.

## Authentication contention

Concurrent requests could contend on the credential's `last_used_at` timestamp.
The production log at 13:26:46 UTC shows a serialization failure, a 0.3906-second
retry delay, and a successful HTTP 200 at 13:26:46.800. This is evidence of
avoidable contention, not proof that this request caused a reported 502.

The private timestamp helper preserves the one-minute throttle, skips contended
rows, and contains stale-snapshot serialization failures in a savepoint. All
credential, Agent-status and transport checks still run first. Other database
errors propagate. The SQL updates only usage and ORM audit timestamps, never
credential validity or delegated permissions.

## Qualification and release pairing

MCP PR #162 is merged as `77b82be9ef8b17c7a25cbf6a67ebc09d9674ed2a`.
It makes Activity scheduling visible by default, reports cold upstream failures
as retryable HTTP 503, and preserves structured unknown outcomes for ambiguous
mutation failures. It adds no mutation retries.

Its published Linux/amd64 image is:

`ghcr.io/unstaticlabs/odoo-mcp@sha256:4d5272cb4b1226d0e7774ac280e0c218148083f70b3eca6a9c37b52c628c9138`

An isolated database and Project-only Agent were used for a real MCP/JSON-2
smoke test. Task creation, description update, public `write`, Chatter note,
specialized and public Activity scheduling, and rescheduling succeeded. Read-back
confirmed the changes. Direct `bus.bus.create` remained denied with
`outcome: not_applied`. The three opt-in MCP integration tests also passed.

Final qualification passed 109 access-control tests on the 162-module product
registry, 29 action-risk tooling tests, source and runtime policy checks,
product/migration boundaries, and compilation of 36 asset bundles. The earlier
clean-install suite also passed before optional auto-installed applications were
removed by the supported product-scope helper.

The permanent request-environment regression flushes precommit hooks with an
active request; it checks HTML notifications, denial of direct bus creation,
read-only scope stripping and audit attribution. Separate tests cover
cross-company and read-only rescheduling denials, timestamp contention and
savepoint recovery. The reviewed runtime inventory contains 50 models inheriting
the Activity rescheduling wrapper.

These results qualify source changes, not a production rollout. The reported
production business records were not modified. A generic gateway 502 remains an
unknown mutation outcome unless independent read-back or server evidence proves
otherwise. Do not treat a connector refresh or a schema inspection as proof that
the paired Odoo implementation is running.

## Variant-search boundary

The grounded search was for `request.env` notification paths and deferred bus
creation, then callers of `handle_history_divergence` and shared Activity methods.
The two HTML-editor notification branches use the same request environment and
are covered by the controller fix. Independent web controllers are not JSON-2
Agent entry points and do not receive this scope. No technical-model ACL
allowlist or transaction-wide unrestricted bypass was added.
