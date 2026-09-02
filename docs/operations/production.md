# Production operations

The VPS database is authoritative. Never recreate it from the frozen Odoo
Online export. Production changes arrive through immutable releases; persistent
data moves only through the coordinated backup and recovery contract.

## Runtime topology

Production and staging each run isolated Odoo, PostgreSQL, Paperless, broker,
MCP, Sign, and renderer services. Both use the protected shared Ollama service
and its qualified BGE-M3 model. They use separate volumes, databases, OAuth
state, ports, ingress aliases, and external-side-effect policies.

- Production: `https://odoo.unstaticlabs.com`
- Staging: `https://odoo-staging.unstaticlabs.com`
- Pocket ID: `https://auth.unstaticlabs.com`

Cloudflare routes production to `odoo:8069` and staging to
`odoo-staging:8069`; websocket routes use port `8072`. Never reuse the ingress
alias across environments.

Odoo enables `proxy_mode` only in production-like targets. Cloudflare is the
trusted edge for those targets and must preserve `Host`, `X-Forwarded-For`, and
`X-Forwarded-Proto`. The public `/websocket` route must reach Odoo's gevent
port and complete an HTTP 101 upgrade. Runtime health fails when the effective
Odoo configuration, HTTPS endpoint, or WebSocket route differs from this
contract. Local direct-access stacks keep proxy mode disabled.

This trust boundary does not yet enforce an HTTP-to-HTTPS redirect or add
`Secure` and `SameSite=Lax` to Odoo's session cookie. Those are separate
hardening items. Do not expose ports 8069 or 8072 beyond the controlled Docker
networks while they remain open.

The 4-vCPU/8-GiB VPS uses versioned resource overlays. Production services
receive higher CPU shares, memory reservations, and lower OOM scores. Staging
has lower CPU, memory, and PID ceilings, cannot consume swap, and is selected
before production if the host reaches memory pressure. Every restored
generation records the applicable overlay in its Compose provenance. Do not
start either VPS stack without its environment-specific resource overlay.

## Release contract

Every release manifest binds:

- the Distribution source commit;
- immutable Odoo, backup-tool, Paperless, Sign, MCP, and renderer images;
- the MCP compatibility contract;
- OCA and action-risk-policy identities;
- the Ollama image, BGE model digest, and embedding dimension.

CI publishes content-addressed images. Unchanged components are reused from
GHCR; digest references—not branch names or `latest`—are deployable identities.
The runtime release manifest must match the images actually running. Backup
refuses a mismatched manifest.

Production deployment belongs to protected CI/GitOps. A release workflow must:

1. freeze user writes;
2. create and qualify a coordinated backup;
3. deploy exact image digests and run required module upgrades;
4. run health and business smoke checks;
5. unfreeze and notify on success;
6. restore the pre-release snapshot and report clearly on failure;
7. recreate staging from the successful production recovery point.

## Backup and recovery

Use [`backup-and-recovery.md`](backup-and-recovery.md). The normal interface is:

```bash
scripts/usl-stack health --target production
scripts/usl-stack smoke --target production
scripts/usl-stack backup create --target production --json
scripts/usl-stack restore run \
  --source production --target staging \
  --snapshot <64-character-qualified-snapshot-id> --json
```

One independent production-to-staging restore must pass after every successful
production release. Keep the active staging generation and one rollback
generation; remove older generation-owned resources with `usl-stack cleanup`.

## External integrations

External effects are controlled independently from image deployment.

- Pocket ID is the interactive login authority.
- Resend is the production SMTP transport.
- Gmail IMAP feeds Odoo aliases after the mailbox and recipient routing pass.
- PDP/Peppol reception may remain enabled while registration is pending.
- Sending and e-reporting require their own accepted activation gates.
- Bank ingestion must validate the intended company, journal, account, and
  sender before automatic processing.

Historical queues must never be replayed during an upgrade or restore. Staging
must remain neutralized: no live mail, filing, payment, bank, or signing side
effects.

### Inbound addresses

The shared Gmail inbox preserves the original recipient and routes it through
Odoo aliases:

- `expense@unstaticlabs.com` for employee expenses;
- `purchases@unstaticlabs.com` for USL vendor bills;
- `purchases-uslmedia@unstaticlabs.com` for USL Media vendor bills;
- `todo@unstaticlabs.com` for unassigned personal tasks;
- `project-<slug>@unstaticlabs.com` for explicit project aliases;
- `bank-<company>-<journal>@unstaticlabs.com` for configured bank ingestion.

The default sender, catchall, and bounce local parts are `odoo`, `catchall`,
and `bounce`. Verified personal sender addresses map to the existing user,
contact, and company-specific employee; they do not create another employee.

## Admission checks

Before opening a changed runtime, require:

- exact release and running-image identity;
- healthy Odoo, Paperless, MCP, Sign, databases, and Ollama contract;
- `proxy_mode=True`, `list_db=False`, the exact database filter, public HTTPS,
  and a successful WebSocket upgrade;
- balanced Accounting and expected company/business counts;
- Odoo filestore and Paperless original coverage;
- preserved OCR, previews, Tantivy, and vectors;
- no unexplained mail, Documents, bank, payment, Sign, or PDP queue work;
- zero active cron failures;
- Pocket ID and multi-company access checks;
- a qualified recovery point and tested rollback.

Run the deterministic read-only gates with:

```bash
scripts/usl-stack health --target production --json
scripts/usl-stack smoke --target production --json
```

## Incident response

1. Freeze access and preserve logs and the failed release identity.
2. Select the full qualified snapshot created immediately before deployment.
3. Prove it in isolated storage unless the incident requires immediate
   rollback and the same snapshot was already independently restored.
4. Restore production with explicit `--replace --confirm production`.
5. Rebind production-only secrets and identities.
6. Run admission checks before unfreezing.
7. Record the accepted data gap, failure cause, and corrective change.

The Online export is historical evidence, never a production rollback source.

## Specialized procedures

- [Electronic-invoice activation](activate-french-electronic-invoicing.md)
- [Pocket ID](pocket-id-sso-runbook.md)
- [Paperless and Documents](paperless-documents-runbook.md)
- [Document renderer](document-renderer-runbook.md)
- [Sign](sign-runbook.md)
- [Odoo MCP](odoo-mcp.md)
- [Product and migration boundary](product-migration-boundary.md)
