# Document materialization differential review

Review date: 2026-08-30

Baseline: `07bc086` (`origin/19-usl`)

Scope: Paperless range overlay, Odoo grant lifecycle and public redemption
controller, MCP-facing document metadata changes, tests, and ingress examples.

## Executive summary

| Severity | Open findings |
|---|---:|
| Critical | 0 |
| High | 0 |
| Medium | 0 |
| Low | 0 |

Recommendation: approve after the deployment conformance checks in
`document-materialization.md` pass against the production reverse proxy. The
review treated the unauthenticated redemption route, token storage, user/company
context restoration, Paperless service-token boundary, and streaming response as
high risk.

Two issues found during review were corrected before this report:

- Paperless streams now verify the advertised `Content-Length` and terminate a
  truncated response instead of silently treating it as complete.
- Odoo validates stored MIME metadata before placing it in `Content-Type`; the
  existing Odoo filename utility remains authoritative for
  `Content-Disposition`.

## Trust boundaries and invariants

- The only public credential is a 43-character, 256-bit random capability. Only
  its SHA-256 hash is stored, and the raw value appears only in the explicit
  issuance result and external URL.
- Token lookup may use elevated access only to locate the grant. Document and
  version authorization runs as the active issuing user with `su=False` and the
  bound company context.
- Every request rechecks expiry, revocation, issuer state, company membership,
  record rules, linked-record access, version ownership, and binary identity
  before Paperless is opened.
- The fixed Odoo route never contains the token. The external path is rewritten
  by ingress into a stripped internal header and must be excluded from access
  logs.
- Only Odoo's private Paperless service identity reaches Paperless. Redirects
  are rejected and external cookies, authorization, host, origin, and referrer
  headers are never forwarded.
- One grant remains bound to one database, document, Odoo version, Paperless
  version, binary variant, company context, size, and checksum.

## Differential and blast-radius analysis

The existing browser download controller was the only pre-existing binary
download caller modified. Its normal record and archive-binary checks are now
centralized in `_authorized_binary_descriptor`; preview and thumbnail routes are
unchanged. The new descriptor has three production call sites: grant issuance,
grant redemption, and authenticated browser download. The new Paperless stream
operations have those same controller/issuance consumers; the older buffered API
client remains available to unrelated preview and thumbnail flows.

Git history attributes the archive-binary authorization boundary to
`9ce5cbb3c38` (`feat(documents): add governed MCP read facade`) and the
Distribution replay to `33e5aa556ad`. The refactor does not remove that check;
it calls it through the shared descriptor before every relevant external file
request. No security check removed by this diff was found.

Concrete adversarial paths reviewed included random-token enumeration, a token
holder after issuer deactivation or company removal, a changed linked-record
rule, a replaced current version, concurrent revocation while opening
Paperless, a redirecting/failed/truncated Paperless server, malformed Range and
header-injection values, an attacker-controlled Host header, and a client
spoofing the internal grant header. Automated coverage exists for the
application-level paths; ingress spoofing and access-log exclusion require the
deployment smoke test.

## Verification evidence

- Paperless overlay image `3.0.5-usl.7`: 5 focused range tests passed.
- Clean Odoo install: 13 focused tests passed before review hardening.
- Odoo module upgrade after hardening: 22 test methods passed with 0 failures;
  Odoo reported 28 `usl_documents` tests.
- MCP: TypeScript typecheck, 45 Vitest tests, build, and Node 24 container build
  passed.
- Python compileall and `git diff --check` passed.

## Remaining deployment verification

No live production ingress or real Paperless document was available in this
isolated worktree. Before rollout, operators must validate the exact Nginx or
Caddy configuration, confirm all relevant logging/tracing layers redact the
external request target, and run real PDF `HEAD`, closed/open/suffix Range,
revocation, user-disablement, and cross-company smoke tests through the public
HTTPS origin.
