# Linked expense receipt retrieval

Linked receipt retrieval turns a PDF link in an incoming expense email into a
validated Odoo attachment. It is disabled by default and applies only to
emails that create `hr.expense` records.

## Security and data boundaries

Odoo extracts at most ten HTTPS candidates and stores only normalized features
and SHA-256 URL fingerprints. The signed URL remains in the original
`mail.message`; the job recovers it immediately before a fetch. Do not copy a
signed URL into a ticket, log, queue description, metric, or screenshot.

Odoo reaches the fetcher through mTLS over the generation-owned
`receipt-control` Unix-socket volume. The fetcher has no Docker network in
common with Odoo, PostgreSQL, Paperless, or the default application network.
Its only network is the internal `receipt-proxy` network. Smokescreen alone
bridges that network to `receipt-public`, rejects non-public destinations and
enforces destination port 443.

The fetcher first streams HTTP through Smokescreen. HTML responses receive one
fresh, non-persistent Chromium context. The browser does not authenticate,
submit forms, reuse user cookies, or make non-GET/HEAD requests. It may use
provider cookies created inside that disposable context, then destroys them.
qpdf and pikepdf must accept the resulting PDF before it returns to Odoo.

## Prepare credentials

For local development, create the ignored credentials once:

```bash
scripts/generate-receipt-fetcher-certs
```

The script refuses partial or existing credential replacement. Production
uses the deployment secret store and separate fetcher and Odoo directories:

- fetcher: `ca.crt`, `server.crt`, `server.key`;
- Odoo: `ca.crt`, `odoo.crt`, `odoo.key`.

Rotate both leaf certificates together, restart Odoo and the fetcher, then
repeat admission. Never place the CA private key in a container.

## Queue contract

`queue_job` runs `_job_fetch_receipt` on `root.receipt_fetch` with capacity 2.
The server-wide modules must be `web,queue_job`, the root capacity must cover
four workers, and `ODOO_QUEUE_JOB_CHANNELS` must include:

```text
root:4,root.receipt_fetch:2
```

Each generation has identity
`receipt-fetch:<retrieval-id>:<generation>`. Jobs contain the retrieval
recordset and no URL. The first attempt runs immediately; retryable failures
run again after 1, 5, and 15 minutes, for four attempts in total. The
user-facing retrieval and technical queue job remain separate audit records.

## Admission

Keep `USL_LINKED_PDF_DOWNLOAD_ENABLED=0` and
`USL_LINKED_PDF_DOWNLOAD_ADMITTED=0` while qualifying a release. Admission
gates both discovery and execution, so a disabled environment does not create
new employee prompts. It requires all of the following:

1. The release manifest contains exact digest references for `distribution`,
   `receipt-fetcher`, and `receipt-egress`, and the running identities match.
2. Odoo has at least four workers, `queue_job` is loaded server-wide, and the
   receipt channel has capacity 2.
3. Odoo reaches `/healthz` through the Unix socket with its client certificate;
   a client without that certificate is rejected.
4. The fetcher container is non-root, read-only, drops every Linux capability
   except `SYS_CHROOT` required by Chromium's namespace sandbox, uses the
   checked-in seccomp profile, and has only `receipt-proxy` attached.
5. The fetcher cannot resolve or connect to Odoo, PostgreSQL, Paperless,
   metadata endpoints, RFC1918 space, loopback, or a public address directly.
6. Smokescreen is the fetcher's only outbound route and rejects private,
   reserved, mixed-DNS, rebinding, and non-443 destinations.
7. Offline direct-PDF, public redirect-chain, and JavaScript-download fixtures
   pass without signed URLs appearing in container or Odoo logs.
8. qpdf/pikepdf rejection fixtures cover oversize, truncated, encrypted,
   active-content, embedded-file, parser-timeout, and parser-crash cases.
9. A restored or neutralized database cannot enqueue or execute a fetch.

Use these read-only probes after the services start:

```bash
docker compose exec -T odoo python -c '
import httpx, ssl
c = ssl.create_default_context(cafile="/run/secrets/receipt-fetcher/ca.crt")
c.load_cert_chain("/run/secrets/receipt-fetcher/odoo.crt", "/run/secrets/receipt-fetcher/odoo.key")
t = httpx.HTTPTransport(uds="/run/receipt-control/fetcher.sock", verify=c)
r = httpx.Client(transport=t, trust_env=False).get("https://usl-receipt-fetcher/healthz")
assert r.status_code == 200
'
docker inspect "$(docker compose ps -q usl-receipt-fetcher)" \
  --format '{{json .NetworkSettings.Networks}}'
```

Record the evidence in the protected release workflow. Only CI may set both
production gates to `1` after admission.

## Employee and manager recovery

When confidence is insufficient, the employee chooses a sanitized candidate
on the expense. That choice records one positive example and bounded negatives
for the other candidates in the same email. A valid fetched PDF activates the
host and learned pattern instance-wide.

Transient failures stay in the queue. A terminal failure preserves the email
and expense and offers **Retry**, **Teach another link**, **Ignore**, and the
native attachment action. A manual main receipt supersedes outstanding
generations. Two consecutive terminal pattern failures pause that pattern.

An authentication-required outcome offers the expense owner a manual browser
handoff. The GET interstitial contains only the normalized starting host. Its
CSRF-protected POST locks and rechecks the retrieval, recovers the signed URL
from the source email in memory, and returns a no-referrer `303` redirect. The
external URL necessarily appears in the employee's provider tab, but never in
Odoo HTML, internal URLs, models, chatter, errors, metrics, or request logs.
Only a safe count, time, and initiating user are retained for audit. Accounting
Managers cannot open an employee's provider link unless they are also that
expense's owner.

The handoff never proxies a provider login and never stores passwords, MFA
values, OAuth tokens, cookies, profiles, or sessions. A restored or neutralized
database, a blocked host or pattern, a stale generation, a non-draft expense,
or an existing manual receipt disables the handoff. Do not replace this path
with persistent authenticated browser automation without separate provider
authorization, legal review, and a new security design.

Accounting Managers govern global learning under **Accounting >
Configuration > Linked Receipts**. Blocking a host or pattern is
instance-wide. Restoring a host does not automatically reactivate a paused or
blocked pattern. Restoring a host or pattern that has never completed a valid
PDF keeps it provisional or learning; governance cannot bypass validation.

## Monitoring

Alert on receipt-channel depth, oldest runnable-job age, repeated retrying
records, terminal outcome spikes, and sidecar health. Aggregate only by queue
state, typed outcome, fetch mode, pattern state, or exact hostname. Never use a
URL, path, query value, fingerprint, attachment name, or message text as a
metric label.

The manager views expose retrieval state and outcome, HTTP/browser mode,
pattern confidence changes, and host success/failure counts. Operators may use
read-only PostgreSQL aggregates when diagnosing queue delay:

```sql
SELECT state, count(*)
FROM queue_job
WHERE channel = 'root.receipt_fetch'
GROUP BY state;

SELECT state, count(*), min(create_date) AS oldest
FROM usl_mail_pdf_retrieval
GROUP BY state;
```

Inspect an individual failure through its retrieval record. Do not print its
source message body or reconstruct the selected URL.

## Neutralization and rollback

Database neutralization increments the generation, moves runnable retrievals
to **Needs attention**, and cancels their runnable `queue.job` records without
deleting audit evidence. The job rechecks neutralization and both feature gates
before every attempt.

To roll back, set `USL_LINKED_PDF_DOWNLOAD_ENABLED=0`, stop accepting work,
and let running requests reach their 35-second deadline. Cancel remaining
runnable jobs in the receipt channel through the queue operator view. Leave
source emails, learned evidence, retrieval records, and successful attachments
intact. Removing the sidecars or their release identities is a later release
change, not the first rollback step.
