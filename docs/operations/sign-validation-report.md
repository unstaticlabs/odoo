# Sign validation and release-readiness report

Status date: 2026-08-25

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
- **Simple PDF requests:** Start now defaults to upload, asks only for the PDF,
  signer and an optional note, derives the document name, and opens field
  placement immediately. Advanced linking and template reuse stay available
  without interrupting the common journey.
- **Requests:** Open Requests contains only requests owned or coordinated by
  the current user. The form leads with people, signing method, deadline,
  progress and one next action. Verification and file fingerprints stay in a
  reviewer-only disclosure under Result & proof.
- **My Signatures:** the native list includes both pending and historical
  signer records. Filters separate Ready to sign, Waiting for my turn, Signed
  by me, Completed, Closed and due-dated items without hiding history by
  default.
- **Configuration:** user-facing signer roles are now **Signing Roles** and
  explain that they assign template fields to people. Person-selection rules
  are guided, while linked-record expressions stay in an administrator-only
  advanced section. Identity reviews, signing policies, external providers,
  daily timestamps and settings use business-facing names and next-step help.
- **Signing Readiness:** the former settings redirect is replaced by a native,
  company-aware capability workspace. Standard, Strong, Qualified external,
  Daily proof and optional TSA report Ready, Degraded, Not configured,
  Unreachable or Action required with a safe result, latency, version and next
  action. Credentials are never persisted.
- **saas-19.3 compatibility:** binary fields use the new binary wrapper API,
  downloads use `ir.binary` streams, and business-record form integration uses
  the new renderer contract.
- **Final archival:** request and daily-timestamp dossiers are converted from
  saas-19.3's `BinaryValue` wrapper to the base64 contract expected by
  `usl_documents`. This fixes the case where signing and validation succeeded
  but Paperless finalization left the request in Evidence incomplete.
- **Journey language:** signer-facing lists now distinguish personal status
  from overall request status. Closed requests retain Completed, Declined,
  Expired, Cancelled or Result rejected instead of collapsing to Done. Strong
  and external journeys use short action-led phases, and protocol details stay
  behind reviewer or security disclosures.
- **Paperless retrieval:** the external Paperless action is offered only when
  the current user's archive identity and this document's permission are both
  synchronized. Authorized Odoo preview and download remain available, so a
  user is never sent to an archive page that will reject them.

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

- the final isolated `scripts/sign-qa-stack test /usl_sign` run installed the
  module from clean state and completed 71 post-test entries with zero failures
  and zero errors. Odoo's per-module statistics reported 77 Sign tests plus the
  six web-suite wrappers;
- desktop and mobile frontend suites each passed 15 tests and 65 assertions.
  They cover the stale palette-drag regression, whole-field movement, the
  iframe pointer bridge, autosave rollback, template upload, dashboard
  scrolling and action routing;
- six headless Chrome 151 journeys passed without biometrics: native template
  creation, requester preparation/send/monitoring, Standard public signing and
  archival, identity-connection presentation, Strong signing presentation,
  and the dashboard/Start flow;
- the first final run found one stale requester-test selector: Odoo 19.3 stores
  radio values in `data-value`. The test was corrected to use Odoo's native
  renderer contract, the focused requester journey passed, and the complete
  suite then passed;
- four focused backend tests for My Signatures statuses/actions, terminal
  request presentation, external next-step gating and configuration guidance
  passed with zero failures and zero errors;
- clean Sign boundary and reproducible browser-worker/private-key checks
  passed; Python compilation, `git diff --check`, and French catalogue
  validation across 13 maintained catalogues also passed;
- XML syntax, Python compilation, shell syntax and `git diff --check`;
- French catalogue format and product-language checks for all maintained USL
  catalogues. The new dashboard, simple upload, request, field-group, System
  Status, recovery and signer-facing terms have maintained translations;
- live QA recovery of request 4: Completed, validation Valid, evidence
  Complete, archive Archived, linked `usl.document` present and no remaining
  archive error;
- live System Status refresh: Standard, Strong personal and Daily timestamps
  Ready; Qualified external Action required because the lightweight QA tenant
  has no trusted-list feed; optional PDF signing timestamps Not configured.

The final source gates passed: clean Sign product boundary, reproducible
browser-worker/private-key boundary, product/migration source boundary, Python
compilation, XML parsing, shell syntax, French catalogue validation, and
`git diff --check`. The full-product database boundary is not a Sign-only gate:
the worktree guard correctly refused the canonical project, and the isolated
lightweight QA database correctly reported unrelated Accounting and Project
product modules as uninstalled. No canonical database was opened from this
feature checkout.

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
