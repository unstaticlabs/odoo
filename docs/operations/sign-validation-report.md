# Sign validation and release-readiness report

Status date: 2026-08-24

## Outcome

This feature delivers a document-first, Odoo-native signing application on the
local `19-usl` saas-19.3 baseline `e3b64c209ac`. It extends the pinned OCA Sign
module without modifying its source. Odoo owns the workflow and structured
evidence, EU DSS owns PAdES construction and validation, Pocket ID owns
passkeys, `step-ca` issues short-lived document certificates, and Paperless
stores the durable dossier.

The installed application exposes Standard, Strong personal and Qualified
external document signing. The earlier internal business-decision experiment
is deliberately dormant: its Python and XML source is retained for later
product evaluation, but it is not imported, loaded, granted access, shown in
menus, included in the dashboard, or seeded in QA.

## Product changes in this closure

- **Navigation:** Sign opens on the dashboard. The only document workspaces are
  **Request Signature → Templates**, **Open Requests**, **Completed**, and
  **My Signatures**, followed by role-aware **Configuration**.
- **Dashboard:** the action uses Odoo's content scroller and remains usable when
  its cards exceed the viewport. It focuses on documents to sign, requests to
  prepare, problems to resolve, requests waiting on others, and recent results.
- **Templates:** the default workspace is the native OCA kanban. Upload and
  empty-space drop create one validated template envelope and open the editor.
  Native cards, prepare/use, duplicate, archive and immutable-version behavior
  remain available.
- **Editor:** the palette crash caused by stale drag state is fixed. Palette
  click, drag and right-click use one placement command. A field can be moved
  from its entire body, while its explicit controls retain their own actions.
  The adapter handles pointer cancellation, iframe reload and normalized page
  coordinates.
- **Requests:** Open Requests contains only requests owned or coordinated by
  the current user. The form leads with state, progress, next action, requested
  trust, due date, proof and archive status; technical material stays under
  Proof & Validation.
- **My Signatures:** the native list includes both pending and historical
  signer records. Filters separate Ready to sign, Waiting for my turn, Signed
  by me, Completed, Closed and due-dated items without hiding history by
  default.
- **Configuration:** signer roles now explain that a role is a document slot,
  show how the person is selected, and keep linked-record expressions in an
  administrator-only advanced section. Identity reviews, trust rules, qualified
  providers and daily proofs each include purpose, safe defaults and next-step
  guidance.
- **Signing Readiness:** the former settings redirect is replaced by a native,
  company-aware capability workspace. Standard, Strong, Qualified external,
  Daily proof and optional TSA report Ready, Degraded, Not configured,
  Unreachable or Action required with a safe result, latency, version and next
  action. Credentials are never persisted.
- **saas-19.3 compatibility:** binary fields use the new binary wrapper API,
  downloads use `ir.binary` streams, and business-record form integration uses
  the new renderer contract.

## Trust and evidence behavior

- Standard is labelled **Standard electronic signature with reinforced
  evidence.** It captures consent and authentication evidence, freezes and
  hashes the request, applies the local platform PAdES seal, validates the
  persisted bytes independently, and archives a complete dossier.
- Strong is labelled **Strong personal signature — designed for
  advanced-signature requirements.** Every ceremony requires fresh Pocket ID
  passkey authentication and binds the signer, request, exact PDF, consent,
  CSR/public key, policy, nonce and expiry. The browser worker's
  `extractable:false` document key never reaches Odoo.
- Qualified external is provider-neutral. Export or provider navigation never
  completes a request; the imported PDF must pass DSS revision, signer, chain,
  qualified-provider and achieved-level validation.
- A request completes only after all expected signatures, authoritative DSS
  validation, complementary pyHanko agreement, complete evidence and
  checksum-confirmed Paperless archival.
- Closed UTC-day manifests chain per company and list each completed request's
  event head, final PDF hash, dossier hash and completion event. OpenTimestamps
  anchoring is asynchronous and never delays request completion. Confirmation
  means existence no later than the verified Bitcoin block time; it is not the
  signing time, RFC 3161, signer identity, a qualified timestamp or QES.

## Reproduced validation on this tip

The following checks were run on the saas-19.3 worktree without a physical
authenticator:

- clean `usl_sign` installation and focused QA bootstrap from module state,
  with `--without-demo` and no SQL dump;
- backend `TestCleanUslSign`: 49 post-tests, with Odoo reporting 51 total tests,
  zero failures and zero errors;
- desktop frontend: 15 tests and 65 assertions, all passed;
- mobile frontend: 15 tests and 65 assertions, all passed;
- six headless browser journeys covering the native template workspace,
  requester preparation/send/monitoring, Standard public signing and archival,
  Pocket ID enrolment presentation, Strong signing presentation, and the Sign
  dashboard/Start flow. Five passed in the combined run; the dashboard test
  then exposed a faulty no-overflow assumption, was corrected, and passed in a
  focused rerun on Chrome 151;
- XML syntax, Python compilation, shell syntax and `git diff --check`;
- French catalogue format and product-language checks for all maintained USL
  catalogues. New navigation, role and readiness terms were reviewed manually;
  untranslated active terms continue to use Odoo's English fallback rather
  than unreviewed machine translation.

The exact release-gate and final QA deployment results are recorded in the
feature handoff. The final source gates passed: clean Sign product boundary,
reproducible browser-worker/private-key boundary, product/migration source
boundary, Python compilation, XML parsing, shell syntax, French catalogue
validation, and `git diff --check`. The canonical database half of the
product/migration boundary intentionally refuses to run from a linked
worktree; no canonical database was opened from this feature checkout.

The final `usl-sign-0a32-qa` tenant was rebuilt from empty project-scoped
PostgreSQL, Odoo filestore, Pocket ID and Paperless volumes. It installed
`usl_sign` on `odoo_dev` with `--without-demo`, configured the dedicated
Pocket ID and Paperless service identities, and seeded only focused synthetic
document-signing examples. No SQL dump or copied filestore was used. All
containers reached healthy state. The non-biometric service smoke then passed
CA, DSS, separate manifest signing, pyHanko cross-validation, deterministic
PDF/A-3 dossier, veraPDF, platform sealing, replay, and alteration checks.

The expected registry warning about replacing OCA's coarse request state
selection remains deliberate: the product requires the exact lifecycle and
does not alter OCA source.

## Prior real-device evidence

The merged Pocket ID work previously completed one real Chrome 150/macOS 26.6
Touch ID journey. It reached Completed, confirmed fresh `amr=["phr"]`, produced
a ten-minute personal certificate, validated PAdES Baseline B in DSS, archived
complete evidence in Paperless, cleared ceremony secrets, and found no private
JWK, PKCS#8, seed or private `CryptoKey` in automated traffic inspection.

That remains architectural evidence, not a fresh acceptance of this UI tip.
No Touch ID or other physical-authenticator prompt was triggered during this
closure.

## Genuine release limitations

- Formal Advanced Electronic Signature classification still requires
  independent legal and security audits.
- The real-device result achieved PAdES Baseline B. PAdES-T/LT remains
  unproven until an independent RFC 3161 TSA and revocation material are
  configured and accepted.
- Real-device coverage currently includes Chrome/macOS/Touch ID only. Safari,
  Firefox, Face ID and Windows Hello require separate acceptance.
- No real qualified-provider sample was available for external QES acceptance.
- OpenTimestamps aggregation normally takes hours. Automated deterministic
  calendar/explorer tests cover confirmation and recovery; this run does not
  claim a new live Bitcoin confirmation.
- Two-public-explorer verification is weaker than a local Bitcoin Core node,
  though the retained `.ots` receipt remains portable for later independent
  verification.
- Browser-worker termination cannot prove physical memory zeroization.
- Fresh-passkey enforcement remains a tracked Pocket ID patch until its
  upstream behavior is released.
- Final visual and tactile acceptance of the current desktop/mobile UI belongs
  to the manual QA tenant; automated checks do not substitute for that review.
