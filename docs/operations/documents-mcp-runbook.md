# Documents MCP deployment and rollback

The Odoo MCP is a first-class member of the Documents release cohort, but it
remains a Cloudflare Worker. It is not a Compose container and it never holds
the Paperless integration token. Every Documents tool calls the explicit Odoo
`usl.document.mcp_*` facade as the connected Odoo user; Odoo performs company,
record-rule, linked-record, archive-binary, and synchronized-permission checks
before it calls Paperless.

## Qualified identity and endpoints

The non-secret target settings live in `deploy/documents/qa.env` and the
pre-production template. They pin:

- Odoo MCP Git commit and `SERVER_VERSION`;
- the compiled `index.js` SHA-256;
- the full `/mcp` and focused `/documents/mcp` URLs;
- isolated local Wrangler and Inspector ports;
- a task-scoped local Durable Object state directory.

The Odoo MCP checkout is independent. Point the release helper to it without
copying source into this repository:

```bash
USL_DOCUMENTS_MCP_REPOSITORY=/path/to/odoo-mcp \
  scripts/documents-mcp qa verify
USL_DOCUMENTS_MCP_REPOSITORY=/path/to/odoo-mcp \
  scripts/documents-mcp qa test
USL_DOCUMENTS_MCP_REPOSITORY=/path/to/odoo-mcp \
  scripts/documents-mcp qa bundle
```

`bundle` performs a Wrangler deploy dry run, refuses a dirty or mismatched MCP
checkout, verifies the compiled Worker digest, and writes the bundle plus a
non-secret identity to `artifacts/release/odoo-mcp/`. The identity contains the
MCP commit, server version, artifact digests, and both endpoint URLs. Checkpoint
F embeds this identity in the coordinated portable release cohort.

## Isolated local runtime and readiness

Start the Worker in a dedicated terminal:

```bash
USL_DOCUMENTS_MCP_REPOSITORY=/path/to/odoo-mcp \
  scripts/documents-mcp qa dev
```

The helper uses the configured loopback port and task-scoped Wrangler state.
It does not start, stop, or inspect any Docker project. In a second terminal:

```bash
scripts/documents-mcp qa readiness
```

Readiness checks both `/mcp` and `/documents/mcp`; an unauthenticated POST must
reach the Worker and fail with HTTP 401. Functional acceptance then uses a
short-lived Odoo API key for the intended QA user. Never print, commit, or place
that key in the release identity or Wrangler state.

## MCP Inspector acceptance

Run MCP Inspector on the configured loopback Inspector port and connect it to
`http://127.0.0.1:19787/documents/mcp` with the three BYO Odoo headers. List
tools first: the focused endpoint must expose exactly the nine `documents.*`
tools; `/mcp` must include the same tools, and accounting/projects must not.

Exercise, in order:

1. `documents.search` with a bounded hybrid query;
2. `documents.get` on one returned ID;
3. `documents.get_content` with a deliberately small page;
4. `documents.find_similar` on an authorized source;
5. versions, tags, correspondents, types, and governed links;
6. restricted-user, other-company, and guessed-ID denials;
7. Paperless and Ollama outage behavior.

Search must return only bounded excerpts. More OCR requires the explicit
paginated content tool. An external MCP client or its model provider receives
the excerpts returned by these calls, so users must connect only an approved
client and minimize content retrieval.

Delete the temporary Odoo API key immediately after the run. A stale client
tool cache is not acceptance evidence: after any surface change, bump
`SERVER_VERSION`, redeploy, and refresh or reconnect the client.

## Deployment

Before deployment, require a clean MCP checkout at the pinned commit, green
typecheck and complete tests, a matching dry-run artifact digest, and the real
QA Inspector journey. Deploy from the MCP repository with the approved
Cloudflare account selected. Wrangler applies the `DocumentsAgent` Durable
Object migration; do not edit or reuse another environment's Durable Object or
KV namespace.

After deployment, repeat readiness and tools/list against the HTTPS target,
then run a minimal authorized search and a restricted-user negative probe.
Odoo and Paperless credentials remain user/integration secrets respectively;
neither is a Worker release setting.

## Capacity and rate limits

The Worker serializes calls per Odoo origin at roughly one request per second.
Wide OCR reads must use pages of at most 8,000 characters; searches return at
most 25 records and a window of 50. Paperless semantic scopes are chunked
server-side without issuing an unscoped query. Capacity planning must count
Odoo calls, Worker/Durable Object requests, Paperless query latency, and local
Ollama latency separately. Do not raise concurrency to mask retry, permission,
or idempotency defects.

## Rollback

Rollback is a Worker deployment action, not an Odoo database rollback:

1. preserve the failed Worker version and its logs;
2. select the immediately preceding qualified MCP commit and artifact identity;
3. deploy that Worker revision to the same Cloudflare environment;
4. retain the `DocumentsAgent` Durable Object migration—never delete its class,
   storage, OAuth KV, or grants as part of rollback;
5. confirm `/mcp` and the prior endpoint set, then refresh affected clients;
6. if `/documents/mcp` must be disabled, remove the connector from clients or
   route it closed at the edge while preserving Odoo/Paperless data.

Never roll back Odoo or Paperless merely to compensate for a Worker-only
failure. If the Odoo facade contract itself changed incompatibly, deploy the
previous coordinated cohort and follow the database backup/recovery runbook.
