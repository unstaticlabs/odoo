# USL Sign validation and release-readiness report

Status date: 2026-08-14

## Outcome

The merged Sign product is reconciled around one architecture and one set of
user journeys. OCA Sign remains the unmodified Community foundation. Pocket ID
owns passkeys and fresh user authentication. Odoo owns identity review,
document consent and binding, the signing workflow, evidence, validation and
archival. DSS remains authoritative for PAdES.

The public signer journey is now company-facing rather than implementation-
facing. It presents the document, signer role and current action in three
stages: review, confirm identity and complete. Pocket ID opens in a transient
window for fresh authentication; the document remains visible in the main
window. Success returns automatically. Cancellation, popup blocking, stale
attempts, concurrent attempts and a lost final response have explicit recovery
paths.

## Retained, changed and removed

Retained:

- Pinned OCA Sign templates, roles, PDF placement and request primitives.
- The productized typed-field editor, stable role colors and immutable template
  versions.
- Journey-led landing, Library, Open Requests, My Signatures and Configuration.
- DSS, step-ca, pyHanko, veraPDF and `usl_documents` Paperless integration.
- Clean trust model: Standard, Strong personal and Qualified external.

Changed:

- Strong authentication now belongs exclusively to Pocket ID and is bound to
  its immutable issuer and subject identifiers.
- The external journey uses company/provider identity and concise business
  language instead of internal “USL Sign” terminology.
- Strong signing has an explicit state machine, restrained progress feedback,
  safe cancellation and session-bound completion recovery.
- Invitation URLs are generated from the configured Odoo public base URL.
- Platform-seal and daily-manifest keys are independently configured and DSS
  refuses startup if the leaf certificates are the same.
- The reusable isolated QA stack uses stable `usl-sign-pocketid-qa` naming,
  not an archived worktree identifier.
- Daily manifests now cover closed UTC days, catch up missed days, chain to the
  preceding signed envelope and list each request's event head, final PDF,
  dossier and completion hashes explicitly.
- OpenTimestamps submission and Bitcoin verification are automatic,
  asynchronous and company-configurable. Request completion remains
  independent from delayed Bitcoin confirmation.

Removed:

- Odoo-owned passkey credentials, counters, registration ceremonies and the
  obsolete WebAuthn service.
- Old browser tests that exercised the superseded Odoo WebAuthn architecture.
- Technical callback wording such as “Pocket ID verified”.
- Legacy provider, restoration, compatibility and historical-proof concepts.

## Final journeys

- **Start:** request an attributable business decision or request document
  signatures.
- **Standard:** review the exact PDF, complete assigned typed fields, consent,
  sign, validate, seal and archive the evidence dossier.
- **Strong personal:** enrol through Pocket ID, receive reviewer approval,
  review the frozen document, create a non-exportable document key in the
  browser worker, confirm identity with a fresh passkey, receive a short-lived
  certificate, embed and validate personal PAdES, then archive.
- **Qualified external:** export the exact frozen revision, wait for a
  provider-neutral external result, import it and accept it only after DSS
  establishes signer attribution and the required qualified level.
- **Retrieve:** Library exposes completed documents only after signatures,
  validation, evidence and checksum-confirmed Paperless archival all succeed.

## Security and legal positioning

- Pocket ID receives only the OIDC binding digest, never the document.
- Every Strong authorization binds signer, enrolment, request, role, document
  hashes, consent, CSR/public key, policy, nonce and expiry.
- `otp` authentication is rejected and fresh `amr=["phr"]` is required.
- The document private key is generated `extractable:false` and remains in a
  dedicated browser worker. Only the CSR and signature value leave it.
- One live ceremony is allowed per signer. One-use data is invalidated on
  completion or cancellation; replay and different-document use fail closed.
- Public strong pages use a strict CSP, no analytics and no unrelated Odoo
  frontend bundles.
- Local certificates, platform seals and timestamps are not described as
  qualified services. The exact label remains “Strong personal signature —
  designed for advanced-signature requirements.” A formal Advanced Electronic
  Signature claim requires independent legal and security review.

## Evidence and archival

Odoo retains the source and frozen PDFs, annexes and page map, fields, roles,
policy, signer identity, authentication and consent, original/final hashes,
append-only event chain, certificates and chains, DSS reports, pyHanko result,
completion certificate, signed manifest and final PDF. External-provider proof
is included where applicable.

The evidence service creates a deterministic PDF/A-3 dossier, embeds the
artifacts, validates it with veraPDF and seals it. `usl_documents` archives the
dossier through a checksum-idempotent Paperless operation. Archive failure is
visible and retryable. A request cannot become completed until the expected
signatures, independent validation, complete evidence and Paperless checksum
confirmation all exist.

Daily evidence manifests use a purpose-specific key distinct from the PDF
platform-seal key. They are submitted to at least two official OpenTimestamps
calendars using a persisted privacy nonce. Odoo keeps the original and upgraded
portable receipts, then accepts confirmation only when Blockstream and
mempool.space agree on the block and raw header, the block hash and OTS
attestation verify locally, and six confirmations exist. The confirmed daily
proof receives a separate sealed PDF/A-3 dossier and checksum-idempotent
Paperless archive. This is independent existence evidence, not signer
identification, RFC 3161, a qualified timestamp or QES.

## Current merged-tree validation

Passed:

- Final full isolated-stack run:
  - 87 post-install tests; zero failures and zero errors.
  - Odoo reported 65 `usl_sign`, 34 `usl_pocketid` and 6 `web` tests.
  - Desktop and mobile frontend suites each passed 12 tests with 59
    assertions, including the Completed Library timestamp files and status.
  - The Standard signer browser journey completed with local DSS available.
    It used headless test state only and invoked no biometric authenticator.
- Focused OpenTimestamps/Odoo suite on the isolated QA test database:
  - 12 selected post-install tests; Odoo reported 16 `usl_sign` tests.
  - Zero failures and zero errors.
  - Covered closed-day catch-up, manifest chaining/immutability, two-calendar
    quorum, stable retry nonce, exact signed-envelope binding, receipt
    upgrade/immutability, malformed and substituted receipts, explorer outage,
    disagreement, reorg and invalid/oversized header data, five-versus-six
    confirmations, and checksum-identical Paperless dossier recovery.
  - All calendar and explorer traffic was deterministic and fake; no public
    service was contacted.
- Targeted post-fix regression:
  - The RFC 3339 Bitcoin block time returned by public explorers is converted
    to Odoo's UTC-naive database representation before persistence.
  - One selected test passed with zero failures and zero errors.
- Dependency verification:
  - `pip check`: no broken requirements.
  - Exact runtime versions: `opentimestamps` 0.4.5,
    `python-bitcoinlib` 0.12.2 and `pycryptodomex` 3.23.0.
  - `pip-audit` 2.9.0: no known vulnerabilities in those three pins.
  - License metadata/classifiers report LGPL-3 for OpenTimestamps,
    LGPL-3-or-later for `python-bitcoinlib`, and BSD/Public Domain for
    `pycryptodomex`.
- `make french-translations`: all ten product catalogues passed after the new
  proof states and reviewer actions were translated.
- Ruff, XML parsing, shell syntax, `git diff --check`, `pip check` and the
  deterministic frontend workspace unit fixture passed for the changed files.

- `scripts/odoo-dev test usl_sign odoo_sign_journey_release`
  - 49 loaded Python test methods.
  - Odoo reported 53 `usl_sign` tests and 6 `web` tests.
  - Zero failures and zero errors.
  - Desktop frontend: 12 tests, 56 assertions.
  - Mobile frontend: 12 tests, 56 assertions.
  - Covered landing, Library, requester Routine Agreement, Standard public and
    mobile signing, Pocket enrolment/Strong page rendering, claims, cancellation,
    retry and lost-response recovery.
- `make sign-product-validate product-migration-boundary`
  - Clean product residue boundary passed.
  - Browser-worker reproducibility/private-key boundary passed.
  - Product/migration boundary passed.
- `docker compose build usl-sign-dss`
  - Java compilation and three JUnit tests passed.
  - DSS now enforces platform-seal/manifest signing-key separation.
- `docker compose exec -T odoo usl-sign-services-smoke`
  - Live mTLS CA/DSS checks, constrained certificate issuance, one-use token
    replay rejection, manifest signing, DSS/pyHanko agreement, PDF revision
    comparison, deterministic PDF/A-3 and veraPDF checks passed.
- Focused Pocket ID Strong controller tests passed for fresh claims,
  cancellation/retry, company-facing public information and completion receipt
  recovery.

The isolated virtual-authenticator acceptance exposed and fixed two QA issues:
the Odoo public base URL was not frozen in a reused development database, and
the harness still expected the removed “Pocket ID verified” text. A subsequent
harness-only attempt could not start local Chrome because Chrome did not create
its DevTools port. One bounded retry failed identically before opening a page.
No product failure or biometric interaction occurred. The isolated stack was
then stopped. This run is not claimed as a new complete browser acceptance. No
new browser or signer journey was run for the OpenTimestamps change.

One earlier broader attempt reported two failures. The first exposed a test-
transaction issue around the persisted submission nonce and was fixed; both
the focused and full final suites prove nonce reuse. The second was the Standard
browser journey running without DSS because the stack test service depended
only on the database and Pocket ID. The Compose test service now waits for DSS
and step-ca, and the final full rerun passed that journey.

The normal QA cron—not the opt-in synthetic smoke—also exercised the real
asynchronous path while the stack was running. Two official calendars accepted
the nonce-protected manifest digest, and Odoo retained the initial receipt plus
two immutable upgrades. Explorer data then exposed an RFC 3339/Odoo datetime
conversion mismatch; the regression above fixes it. A controlled retry with
the final image remained safely `pending` when one explorer was temporarily
unavailable. It retained the portable receipts and Bitcoin attestation, logged
the transient failure without marking confirmation, and scheduled normal
recovery. No live confirmation is claimed.

## Prior real-device evidence retained from the merged branch

The Pocket ID parent branch previously completed a real Chrome 150/macOS 26.6
Touch ID journey. It reached `completed`, confirmed fresh `amr=["phr"]`,
validated PAdES Baseline B with DSS, completed the evidence package and
Paperless archive, cleared ceremony secrets and found no private JWK, PKCS#8,
seed or private `CryptoKey` in automated traffic inspection.

That result remains relevant architectural evidence, but it was not rerun after
the current presentation-layer changes. No Touch ID request was made during
this finalization because the workstation was unattended.

## Deployment and configuration

The disposable developer target is `odoo_dev`. Install or upgrade
`usl_pocketid` and `usl_sign`, build the pinned Pocket ID image and tracked
fresh-passkey patch, and provision a dedicated confidential Sign OIDC client.
The isolated `usl-sign-pocketid-qa` project uses its own `odoo_dev` database,
ports, containers and volumes. It was upgraded from the current worktree and is
left running at `http://odoo-sign-qa.localhost:16669`. Odoo, Pocket ID,
Paperless, DSS and step-ca report healthy. The company-level daily proof and
final-signer-copy settings are enabled, and both daily-proof crons are active.
Required external configuration includes:

- `USL_POCKET_ID_SIGN_CLIENT_ID`
- `USL_POCKET_ID_SIGN_CLIENT_SECRET`
- `USL_POCKET_ID_SIGN_REQUIRED_GROUP`
- `USL_POCKET_ID_SIGN_FRESH_REQUIRED=1`
- `USL_SIGN_OTS_CALENDARS` (optional reviewed official-pool subset)
- `USL_SIGN_OTS_EXPLORERS` (optional two fixed HTTPS Esplora APIs)
- `USL_SIGN_OTS_TIMEOUT` (optional bounded network timeout)

Configure `/sign/pocketid/callback`, PKCE S256, `prompt=login`, `max_age=0`,
scopes `openid profile email groups`, no refresh/offline access and an allowed
signer group. CA, DSS, platform-seal, manifest, mTLS, Paperless and OIDC secrets
remain outside Git and database configuration fields. Disposable development
Sign data is rebuilt; no compatibility migration is delivered.

## Genuine remaining limitations

- Independent legal and security audits are still required before any formal
  Advanced Electronic Signature claim.
- No independent TSA was configured in the verified real journey; PAdES-T/LT
  was not demonstrated.
- Real-device acceptance covers Chrome on macOS with Touch ID only. Safari,
  Firefox, Face ID and Windows Hello require separate acceptance.
- The current UI changes still require final human visual acceptance on desktop
  and mobile; automated rendering and controller coverage passed, but the host
  Chrome harness was unavailable for the final virtual run.
- No real qualified-provider sample was available to demonstrate trusted-list
  QES import end to end.
- Browser-worker termination cannot prove physical memory zeroization.
- Fresh passkey enforcement remains a tracked Pocket ID patch until an official
  release incorporates the upstream work (`pocket-id/pocket-id#1654`).
- OpenTimestamps calendar aggregation normally takes several hours; no live
  Bitcoin confirmation was produced in this validation. The opt-in synthetic
  live smoke was deliberately not run; the normal QA cron produced a real
  pending receipt and later encountered one temporarily unavailable explorer.
- Confirmation is verified through two public explorers, not a local Bitcoin
  Core node. The retained `.ots` proof supports later node-based verification.
- A Bitcoin block time establishes that the manifest and listed hashes existed
  no later than that time; it is not the exact signing time. For signature-time
  semantics or PAdES-T/LT, configure and assess an independent RFC 3161 TSA.
- Odoo logs an expected registry warning because the product deliberately
  replaces OCA's coarse request lifecycle with the exact final lifecycle.
