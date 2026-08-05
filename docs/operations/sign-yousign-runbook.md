# Sign and Yousign operations runbook

## Safety boundary

Odoo owns templates, requests, lifecycle, reminders, business links and
retained evidence. Yousign performs the authentication and cryptographic
ceremony. Development and migration use the Yousign sandbox only. Never copy a
production API key or webhook secret into a database, fixture, log or commit.

The application refuses production submissions unless all of the following
are true for the active company: provider enabled, API key present, webhook
secret present, production environment selected, and
`USL_SIGN_LIVE_ENABLED=1`. Enabling Sign does not enable French electronic
invoicing or e-reporting; their live flags remain `0` outside their own approved
runbooks.

## Environment configuration

Set secrets in the deployment environment and restart Odoo so worker caches
cannot retain an old value:

```text
USL_YOUSIGN_SANDBOX_API_KEY=<secret>
USL_YOUSIGN_SANDBOX_WEBHOOK_SECRET=<secret>
USL_YOUSIGN_PRODUCTION_API_KEY=<secret>
USL_YOUSIGN_PRODUCTION_WEBHOOK_SECRET=<secret>
USL_SIGN_LIVE_ENABLED=0
```

Configure only the credentials for the environment being exercised. Select
Sandbox or Production and enable the provider for each company in Sign →
Configuration → Settings. The adapter selects the corresponding official API
v3 endpoint. Confirm that Provider readiness is green; the settings page
reports presence/readiness, never the secret value.

Register one HTTPS webhook endpoint in the matching provider environment:

```text
https://<odoo-host>/sign/webhooks/yousign/<company_id>
```

Subscribe to `signature_request.activated`, `.done`, `.declined`, `.expired`
and `.canceled`, plus `signer.notified`, `.link_opened`, `.done`, `.declined`,
`.error`, `.identification_failed`, `.identification_blocked`,
`.identification_expired`, `.notification_delivery_failed` and
`.sender_contacted`. Yousign must
sign the raw request body with the shared webhook secret. Odoo verifies
`X-Yousign-Signature-256`, records the provider event identifier, and processes
an exact identifier only once. A successful HTTP response means the event was
authenticated and accepted; request completion still waits for final-document
retrieval and the immutable per-signer JSON audit trail.

## Sandbox acceptance

Use a synthetic PDF and non-production identities. Exercise all supported
fields, multiple pages, multiple signers, signer order, reminders, decline,
expiry, cancellation, Standard/Verified embedded signing and the Qualified
handoff. Confirm that:

1. retries reuse one provider transaction;
2. webhook replay changes nothing;
3. a provider “done” state without final PDF or audit trails becomes Action
   required, never Completed;
4. final/original hashes and signer audit trails are stored;
5. achieved assurance and authentication are derived from each preserved
   signer audit trail, and an unknown level prevents completion;
6. malformed or encrypted PDFs fail before submission;
7. a reusable link creates a fresh independent request and does not expose
   prior identity data; and
8. cancelled, declined, expired and historical requests cannot be reopened or
   sent.

Record the sandbox transaction identifiers in private release evidence, not in
Git. If sandbox credentials are unavailable, mocked tests are necessary but do
not satisfy this acceptance step.

## Production activation

Obtain product-owner approval, provider subscription/eligibility confirmation,
the production API key, a separately generated webhook secret and an HTTPS
callback reachable by Yousign. Back up the database and confirm worker time,
mail delivery and outbound TLS. Configure production credentials while
`USL_SIGN_LIVE_ENABLED=0`, validate webhook authentication with a provider
test event, then set the flag to `1` and restart Odoo in the approved window.

Send one synthetic, non-confidential first request. Confirm invitation,
embedded/handoff flow, webhook processing, final PDF, audit trails and portal
download before approving business use. Roll back by returning the live flag
to `0` and restarting. Existing ceremonies remain readable in Odoo, but
provider reconciliation and cancellation remain disabled until the flag is
re-enabled in an approved recovery window.

## Routine monitoring and recovery

Review Action required requests and provider events daily. The request shows a
safe recovery instruction. Reconcile provider status before retrying creation;
the idempotency key prevents parallel provider transactions. Retrieve pending
evidence again when the ceremony exists but a document/audit download failed.
Do not edit hashes, replace immutable evidence, manually mark a request
Completed or create a second request merely to hide an operational error.

Odoo owns reminder jobs and request expiry. Disable provider-native reminders
for transactions created by this adapter. Provider rate limits and transient
5xx/network errors use bounded retries; authentication, schema and ambiguous
state errors require an administrator. Keep application logs free of API keys,
signing URLs and webhook bodies containing personal data.

## Evidence, privacy and retention

Originals, final PDFs, per-signer JSON audit trails and terminal-event JSON are
company-scoped immutable records. The adapter does not depend on Yousign's
restricted aggregate audit-trail PDF endpoint. Treat signing links, phone
numbers, IP-related evidence and identity results as personal/confidential
data. Export only the evidence needed for an authorized case and preserve its
SHA-256. Each completed request freezes its company retention horizon; the
request shows Active, Due, Indefinite or Legal hold status. Retention deletion
is not an ordinary Sign operation and is never automatic; follow the company
retention/legal-hold policy and approved database/attachment process.
Provider deletion must never precede verified Odoo evidence retrieval and
backup.

For Odoo Online history, run `scripts/sign-restore all` only inside the canonical
reconstruction sequence or an explicitly disposable target. It verifies source
filestore checksums, supports idempotent reruns, validates the 8-request source
perimeter, uninstalls the temporary module and checks the delivered registry.
Never point target Odoo code at the read-only source database.
