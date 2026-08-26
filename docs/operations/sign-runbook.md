# USL Sign operations runbook

## Service boundary

Odoo owns the signing workflow and structured evidence. The internal EU DSS
service constructs and validates PAdES, `step-ca` issues short-lived personal
certificates, and Paperless stores the final signed PDF plus its sealed proof
package. The first-release qualified journey is manual and provider-neutral.
Do not install an external signing API adapter or store service credentials in
Odoo.

All Sign keys and credentials are deployment secrets. Keep the offline CA
root offline after initialization. Mount only the online intermediate,
restricted provisioner, platform/manifest keys and mTLS material needed by the
service that uses them.

## Initialize local services

The pinned components are:

- EU DSS 6.4 in `services/usl-sign-dss`;
- `step-ca` 0.30.2;
- Pocket ID 2.12.0 from exact commit
  `b3fb8de5bc55aa813a27b4e15c1d761026fcceaa`, with the tracked strict
  fresh-passkey patch in `services/usl-pocket-id`; the generic upstream design
  is tracked in Pocket ID issue
  [#1654](https://github.com/pocket-id/pocket-id/issues/1654);
- `@peculiar/x509` 2.0.0 in the dedicated browser worker;
- `opentimestamps` 0.4.5 with `python-bitcoinlib` 0.12.2 and
  `pycryptodomex` 3.23.0 in Odoo for portable Bitcoin existence proofs;
- pyHanko 0.36.2 in the DSS container for independent cross-validation;
- veraPDF in the DSS container for PDF/A dossier validation.

For a lightweight QA tenant tied to the current worktree, run:

```shell
scripts/sign-qa-stack start
scripts/sign-qa-stack info
scripts/sign-qa-stack status
scripts/sign-qa-stack upgrade
scripts/sign-qa-stack login-link valentin
scripts/sign-qa-stack login-link roger
scripts/sign-qa-stack test /usl_sign
scripts/sign-qa-stack smoke
scripts/sign-qa-stack stop
```

The wrapper derives a stable slot from the worktree path and creates a uniquely
named Compose project, port block, database volume, filestore, Pocket ID
tenant, CA/DSS secrets and Paperless volumes. It validates the ports before the
first start. The initial start creates `odoo_dev --without-demo` and runs the
focused synthetic bootstrap; it never imports an SQL dump, filestore or shared
volume. Custom add-ons remain bind-mounted, while the Odoo runtime image is
content-addressed from its dependency inputs so unchanged images are reused.
Use `upgrade` after changing `usl_sign`: it updates the module in place and
restarts only Odoo, preserving the disposable QA records and isolated service
volumes. Use `start` for a complete service/configuration refresh.

`info` prints the resolved project, URLs, review users and login-link commands.
`login-link` creates a one-time Pocket ID session for the named synthetic user;
after opening it, use **Continue with Pocket ID** on the Odoo login page. `test`
uses a separate disposable database and headless/synthetic authenticators only.
`smoke` exercises the local CA, DSS, key separation, pyHanko, deterministic
PDF/A-3 dossier, veraPDF, replay and alteration checks. `stop` leaves all
project volumes intact, so the same `start` command brings the tenant back.
Remove project volumes only when deliberately requesting a new clean seed.

Loopback is the default and must remain the normal developer setting. For an
explicitly authorized private-network review, bind only the Mac's exact
private or Tailscale IPv4 address and use that same address (or its `*.ts.net`
name) in the three browser-facing URLs:

```shell
export USL_SIGN_POCKETID_BIND_ADDRESS=100.64.0.10
export USL_SIGN_POCKETID_PRIVATE_QA=1
export USL_SIGN_POCKETID_ODOO_HOSTNAME=100.64.0.10
export USL_SIGN_POCKETID_POCKET_HOSTNAME=100.64.0.10
export USL_SIGN_POCKETID_PAPERLESS_PUBLIC_URL=http://100.64.0.10:PORT
scripts/sign-qa-stack start
```

Use the Paperless port printed by `scripts/sign-qa-stack info`. The Sign QA
Compose overlay publishes only Odoo, its gevent endpoint, Pocket ID and
Paperless on that one address. PostgreSQL, CA and DSS remain internal to the
isolated Compose project. The helper rejects wildcard, public and multicast
bind addresses; an explicit private-QA opt-in is required for non-localhost
OIDC hostnames. The QA-only private HTTP exception is accepted only when
`USL_DEPLOYMENT_ENV=development` and the opt-in is set; preproduction and
production continue to require HTTPS. Plain HTTP on a private address is
suitable only for this disposable synthetic review tenant, not for production
or a real passkey ceremony.

## Product workspaces

The installed Sign application is document-only. Its navigation is:

- opening **Sign** shows immediate work, blockers, waiting requests and recent
  results; there is no duplicate dashboard menu item;
- **Request Signature → Templates** for reusable documents and the PDF field
  editor;
- **Request Signature → Open Requests** for non-terminal requests owned or
  coordinated by the current user;
- **Request Signature → Completed** for validated and durably archived results;
- **My Signatures** for both pending documents and the current user's signing
  history;
- **Configuration** for identity, trust, provider, readiness, timestamp and
  role administration according to group membership.

The dormant internal-decision experiment is not installed. When a signed PDF
is unnecessary, use the approval mechanism of the relevant Odoo business app.
Do not expose the dormant model or views from Sign without a separate product
and security review.

Configuration entries answer distinct operational questions:

- **My Signing Identity** shows whether the current user has a reviewed Pocket
  ID link for Strong personal signing.
- **Identity Reviews** lets authorized reviewers verify or revoke a recurring
  signer's relationship; Pocket ID continues to own passkeys and recovery.
- **Trust Rules** recommends Standard, Strong personal or Qualified external
  from business context without silently changing the requested level.
- **Qualified Providers** is a reviewed provider-neutral reference catalog,
  not an API integration.
- **Signer Roles** defines reusable places such as Client or Employee. The
  actual person is normally selected on each request.
- **Signing Readiness** checks each end-to-end capability and explains the next
  safe action. It stores health results, never credentials.
- **Daily Timestamp Proofs** exposes signed closed-day manifests and portable
  OpenTimestamps evidence.
- **Settings** contains company policy and delivery defaults. Keep final-dossier
  delivery enabled unless a reviewed company policy says otherwise.

The lower-level `scripts/sign-pocketid-stack` helper remains available for
Pocket patch, archive and virtual-authenticator acceptance. Those commands may
create browser sessions and should not be used during unattended manual QA.
The dedicated Sign client is restricted to its own `usl-signers` group,
requires fresh passkey authentication, advertises the matching discovery
capability and issues no refresh token.

For the ordinary local Sign services, run:

```shell
scripts/sign-services-bootstrap
docker compose build usl-sign-dss
docker compose up -d usl-sign-step-ca usl-sign-dss
docker compose exec -T odoo usl-sign-services-smoke
```

The bootstrap creates the offline/online CA material, restricted provisioner,
platform seal, manifest key and mutually authenticated service certificates.
It writes generated environment material beneath `.secrets/sign/`; the whole
directory is ignored by Git. Production key creation must happen in the
approved secret-management environment, not on a developer laptop.

Required Odoo-side environment values are generated by the bootstrap and
mounted through the Odoo secret directory:

```text
USL_SIGN_DSS_URL=https://usl-sign-dss:8443
USL_SIGN_DSS_CLIENT_CERT=/run/usl-sign/client-chain.crt
USL_SIGN_DSS_CLIENT_KEY=/run/usl-sign/client.key
USL_SIGN_DSS_CA_BUNDLE=/run/usl-sign/root_ca.crt
USL_SIGN_STEP_CA_URL=https://usl-sign-step-ca:9000
USL_SIGN_STEP_CA_PROVISIONER=usl-sign
USL_SIGN_STEP_CA_JWK_FILE=/run/usl-sign/provisioner.jwk
USL_SIGN_STEP_CA_CA_BUNDLE=/run/usl-sign/root_ca.crt
```

Never enable `USL_SIGN_DSS_ALLOW_PLAINTEXT` outside a tightly isolated unit
test. Configure an RFC 3161 TSA only after its trust, retention, privacy,
availability and commercial terms have been reviewed.

Daily OpenTimestamps proof is enabled by default per company and can be
disabled by a Sign administrator. Its network destinations are deliberately
environment-managed and HTTPS-only; do not turn them into editable Odoo URLs.
The defaults are the four official calendar pools, with Blockstream and
mempool.space as the two public explorer APIs. Optional overrides are:

```text
USL_SIGN_OTS_CALENDARS=https://a.pool.opentimestamps.org,https://b.pool.opentimestamps.org,https://a.pool.eternitywall.com,https://ots.btc.catallaxy.com
USL_SIGN_OTS_EXPLORERS=https://blockstream.info/api,https://mempool.space/api
USL_SIGN_OTS_TIMEOUT=5
```

Keep at least two distinct official calendars and exactly two distinct
Esplora-compatible explorer endpoints. No document, signer, consent or
unhashed evidence is sent to a calendar: only a nonce-protected digest of the
DSS-signed manifest leaves Odoo. The opt-in live smoke uses a synthetic digest
only. `Awaiting confirmation` is the normal immediate result because calendar
aggregation usually takes several hours. Automated tests use deterministic
fake calendars and explorers and never depend on these public services.

Qualified-external validation is disabled until `USL_DSS_LOTL_KEYSTORE`, its
password, `USL_DSS_LOTL_URL`, and `USL_DSS_OJ_URL` are supplied through the
deployment secret environment. The PKCS#12 file itself belongs under the
mounted DSS secret directory. DSS refreshes the EU trusted lists every 24
hours, rejects expired or invalid lists, and marks qualified trust unavailable
after 36 hours without a successful refresh. A failed refresh disables QES
acceptance but leaves Standard and Strong local validation available. Monitor
`/v1/health` for `qualifiedTrustReady` and `qualifiedTrustRefreshedAt`.

## Trust configuration

In each company:

1. review the versioned recommendation policies and their business wording;
2. assign Sign User, Template Manager, Identity Reviewer, Evidence Reviewer
   and Sign Administrator roles by least privilege;
3. test DSS connectivity and the CA health check from Sign settings;
4. configure the company platform-seal and timestamp policy;
5. keep **Daily Bitcoin existence proof** enabled unless the company has a
   documented opt-out;
6. configure a Paperless correspondent/document type and verify the
   checksum-idempotent `usl_documents` operation;
7. add only reviewed, current qualified providers to the external catalog,
   including territory, mobile instructions, priority and review date.

An empty provider catalog is safer than an unreviewed recommendation. Provider
names are ordinary catalog data; adding one does not enable an integration.

## Standard acceptance

Use a synthetic, non-confidential PDF. Select the signer and a typed field,
then click the PDF to place it. Repeat with drag/drop and right-click; every
path must show or retain the signer explicitly. Verify all supported field
types, multiple pages, stable role colors, live recoloring after a role change,
resize/move/delete, undo/redo, autosave and retry states, required markers,
page navigation, zoom and keyboard use. Exercise a one-off
request and a reusable template on desktop, and confirm that narrow screens
show the deliberate non-authoring state while mobile signing remains usable.

Verify multiple signers both unordered and ordered, secure-link exchange,
portal/Pocket ID policy where configured, explicit consent, reminders,
expiration, refusal and cancellation. Confirm that:

- only a token hash is stored and five bad exchanges trigger bounded blocking;
- a sent request cannot change document, layout, signer, consent or policy;
- repeated actions/events are idempotent;
- the platform seal validates under the local trust policy;
- altering persisted PDF bytes fails validation;
- the completion certificate uses cautious Standard wording;
- archive failure leaves the request incomplete and retry recovers safely.

## Strong-personal acceptance

Use a real platform authenticator in a supported browser. Record the browser,
OS and authenticator actually tested; do not advertise an untested platform.
The dedicated Pocket ID Sign client and callback must exactly match the
production Odoo host. Strict discovery capability is mandatory.

For the isolated worktree tenant, start the stack and create a short-lived
account-settings link for the synthetic Roger user:

```bash
scripts/sign-qa-stack start
scripts/sign-qa-stack login-link roger 16m
```

Pocket ID uses six-character login codes at lifetimes of fifteen minutes or
less. This QA tenant disables code-based Strong authorization, so the manual
onboarding link deliberately uses sixteen minutes and redirects directly to
account settings. Open it in the browser being tested and add the real
platform passkey. The credential is scoped to the isolated Pocket ID relying
party printed by `scripts/sign-qa-stack info`.

Prepare the reviewed browser journey and copy the IDs and URLs printed after
each command:

```bash
scripts/sign-qa-stack strong-acceptance-prepare touchid-qa real_platform
# Open invitation_url and connect the same Pocket ID account.
scripts/sign-qa-stack strong-acceptance-review ENROLMENT_ID touchid-qa
# Open signing_url, consent, and complete the fresh passkey signature.
scripts/sign-qa-stack strong-acceptance-verify REQUEST_ID
```

The prepare command records whether the run used a virtual or real platform
authenticator. Never label a virtual-credential result as Touch ID, Face ID,
Windows Hello or another platform product.

1. Create an enrolment for a synthetic known partner and record the relationship
   basis, the internal employee/contract/partner review reference and identity
   policy. Send the setup email; use **Copy setup link** only when a separate
   trusted delivery channel is required. The action creates a replacement link,
   copies it immediately when the browser permits clipboard access, and keeps a
   visible copy control as a fallback. It never navigates the reviewer's browser
   to the setup page.
2. Connect the correct Pocket ID subject. Odoo schedules a review activity for
   an identity reviewer; after approval, waiting requests resume automatically.
   Add and recover passkeys in Pocket ID, not Odoo.
3. Sign a frozen request through the dedicated Pocket ID popup with a fresh
   passkey interaction.
4. Inspect browser network traffic and assert that no private JWK, PKCS#8,
   seed, private `CryptoKey` or equivalent key material left the worker.
5. Verify the CSR/public-key hash and exact document hash are in the canonical
   binding; its digest equals the signed OIDC nonce. Confirm `amr` contains
   `phr`, `auth_time` follows ceremony creation, the certificate expires within
   ten minutes and renewal is unavailable.
6. Verify the personal PAdES and final platform seal with DSS and pyHanko.
7. Repeat with ordered strong signers and confirm each covers the prior PDF
   revision.
8. Try replay, a different document or CSR, a stale ceremony, OTP login, wrong
   Pocket subject/group, a revoked enrolment, missing strict capability and a
   reused certificate token; each must fail closed.
9. Re-enrol after revocation and confirm earlier completed signatures are
   unchanged.

If DSS, CA or TSA is unavailable, never bypass the ceremony. Keep the request
in an actionable failure/waiting state and retry only after service health is
restored.

The signing page must remain open while Pocket ID confirms identity: its
isolated worker owns the non-exportable document key. Pocket ID therefore opens
in a short-lived window and closes itself after a valid callback. The main page
must display the authoritative state, not a successful popup alone. A signer
may cancel an unfinished attempt and retry against the same frozen revision.
If the final HTTP response is interrupted after the server commits the
signature, the five-minute session receipt must recover the completed state
without creating another signature. Do not rely on polling `window.closed`:
cross-origin isolation can intentionally sever that browser reference.

### Recorded real-platform acceptance

The isolated worktree acceptance completed on 2026-08-06 with Chrome
150.0.7871.187 on macOS 26.6 arm64 and the Pocket ID credential named
`Chrome on Mac Passkey`. Touch ID was approved for both identity connection
and the document-bound signing authorization. Disposable QA request 23 and
ceremony 10 completed with:

- `amr=["phr"]` and an authentication time after ceremony creation;
- canonical binding SHA-256
  `ffc64126f505816441a353d68cd9cd8539ec0065ff0be6b7b714aeb7213f95a5`;
- exact document SHA-256
  `7ab6576a1338e000eb465b8d5f36f4d5a5fd951ba58dc15266893f05c1212c64`;
- a 600-second personal certificate and achieved `PAdES_BASELINE_B`;
- final PDF SHA-256
  `9ebaed38328baf401ffce9ff9b714762997d47f6d905590699f942a84856ecfd`;
- valid EU DSS 6.4 validation, complete evidence, a valid event chain and
  Paperless dossier 17 archived.

The automated CDP acceptance separately proves that no private JWK, PKCS#8,
seed or private `CryptoKey` appears in browser traffic while exercising the
same worker and ceremony code. The real-platform run proves Touch ID and the
platform credential path; do not misdescribe the virtual traffic capture as a
manual DevTools recording of the real Touch ID session.

## Qualified-external acceptance

Choose a reviewed provider from the catalog and export the DSS-prepared frozen
PDF with signer information, hashes and instructions. Verify the request
enters `Waiting for external signature` and remains there after the provider
page is opened.

After the signer returns the result, import the final PDF and every proof file.
Verify the `Signed document to import` and `Validation in progress` states.
DSS must confirm the signed first revision equals the export, the signer is
correct, the chain and timestamps validate, the trust provider and qualified
certificate/device indications are trusted, and QES was actually achieved.
Exercise modified PDF, signer mismatch, invalid/untrusted chain and
insufficient-level samples; all must become `Validation failed` without a
downgrade or manual completion option.

## Evidence and archival operations

In **Sign → Configuration → Settings**, keep **Send signers a copy of the final
signed document** enabled unless company policy explicitly forbids signer
delivery. The signing application queues signer delivery only after validation
and both Paperless archives have completed.

For every completed request, inspect the source documents, frozen snapshots,
signer/consent evidence, original/final hashes, complete event chain,
certificates, timestamps/revocation material, all DSS reports, pyHanko result,
external proof where applicable, completion certificate, signed PDF and signed
canonical manifest.

Paperless receives two request-linked records: the clean signed PDF is the
primary document to read and share; the deterministic PDF/A-3 proof package is
its audit companion. The package must embed the signed PDF and all supporting
artifacts, pass veraPDF and receive its platform seal. Success or a
checksum-identical response is required for both records before completion.
For timeout or server failure, leave the operation failed/pending, correct the
infrastructure problem and use the explicit retry action. Never change a
checksum, create a second truth record or mark the request complete manually.

The Odoo archived-document **File history** tab appears only when Paperless has
a genuine replacement or restored file. The PDF editor's colored field overlay
is not a file version and must not appear after completion. Use the request's
**Signed PDF** for the authoritative result, **Completion certificate** for a
short summary, and **Complete proof package** for audit or independent review.

The daily job closes UTC days and automatically catches up missed days. Each
company manifest is DSS-signed and chained to the preceding signed envelope.
It records every covered request's event head, final PDF and request-dossier
SHA-256 values, completion event and completion date. A separate 30-minute job
submits new manifests, upgrades pending receipts, verifies Bitcoin evidence
and recovers transient failures with bounded backoff and row locking. A
persisted 16-byte nonce makes a retried submission use the same private
commitment.

The reviewer UI uses four timestamp states:

- **Scheduled for daily proof**: the request completed or the closed manifest
  is waiting for its first submission;
- **Awaiting Bitcoin confirmation**: at least two calendars accepted the
  commitment, but no six-confirmation Bitcoin proof is available yet;
- **Confirmed — existed no later than …**: two public explorers agreed on the
  block and raw header, local verification succeeded and six confirmations
  exist;
- **Action required**: the receipt, binding, calendars or Bitcoin data failed
  closed and an evidence reviewer must inspect and retry after correction.

`Confirmed` is an existence upper bound, not the document's signing time. It
is not signer identification, RFC 3161, a qualified timestamp or QES. Public
explorer agreement is weaker than verification with a fully synced local
Bitcoin Core node. Keep the confirmed `.ots` file: it is portable and can be
verified later with `ots verify` against Bitcoin Core.

After confirmation, DSS builds and seals a separate PDF/A-3 daily proof
dossier containing the signed manifest, both receipt stages, verification
report and instructions. Paperless receives it as a distinct, linked daily
evidence record. A failed daily archive never rewrites request dossiers and
never reverses request completion or Bitcoin confirmation. Restore DSS,
veraPDF or Paperless as appropriate, then use **Retry Paperless archive**.

## Monitoring and incident response

Monitor DSS, CA and Paperless health, validation failures, `Action required`,
`Evidence incomplete`, archive retries, expired challenges/certificates,
failed daily manifests, calendar quorum failures, explorer disagreement and
Bitcoin reorg status. Logs may contain request identifiers and sanitized
error codes, but not PDFs, consent data, signing links, assertions, CSR bodies,
key material, certificates with unnecessary personal data or raw provider
proof.

For suspected key compromise, stop new strong/platform ceremonies, preserve
current evidence, revoke or rotate the affected online/intermediate or seal
credential under the approved PKI procedure, and independently reassess every
signature in the affected issuance window. Never rewrite an existing event or
proof artifact.

## Release checks

Before release, rebuild the disposable `odoo_dev` Sign state from the final
modules. Run the targeted Python/Java/JavaScript/XML/translation tests, service
smoke, browser journeys, `scripts/check-sign-clean-boundary` and
`make product-migration-boundary`. Audit the final registry, schema, routes,
jobs, settings, dependencies, assets, fixtures and documentation. The release
must contain no Sign compatibility layer, reconstruction machinery or
provider-specific integration.
