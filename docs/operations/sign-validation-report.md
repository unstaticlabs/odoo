# Sign validation and release-readiness report

Status date: 2026-08-26

## Outcome

This feature delivers a document-first, Odoo-native signing application on the
current `19-usl` saas-19.3 baseline `f302ae6cdb43`. It extends the pinned OCA
Sign module without modifying its source. Odoo owns the workflow and structured
evidence, EU DSS owns PAdES construction and validation, Pocket ID owns passkeys,
`step-ca` issues short-lived document certificates, and Paperless stores the
signed document plus its durable proof package.

The installed application exposes Standard, Strong personal and Qualified
external document signing. Approval-only business decisions remain outside
the application and use the native workflow of the relevant Odoo business app.

## Product changes in this closure

- **Release-boundary cleanup:** dormant approval-only models and views were
  deleted instead of shipping an unregistered second workflow. The redundant
  always-true signed-PDF flag was removed, document categories now have one
  shared definition, and the source boundary rejects those residues if they
  reappear.
- **Server-side authorization:** external-signature imports, validation,
  recovery and reminders require requester/coordinator access even when an
  RPC is called directly. Signers retain only the intended frozen-document
  export and provider-review actions. External journeys reject direct business
  field mutation, and internal evidence/manifest builders are private methods.

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
- **Final archival:** request and daily-timestamp files are converted from
  saas-19.3's `BinaryValue` wrapper to the base64 contract expected by
  `usl_documents`. Request completion now archives the signed PDF as the
  primary document and the PDF/A-3 proof package as a separately retrievable
  companion. The package embeds the signed PDF and supporting evidence; both
  checksum-idempotent operations gate completion.
- **Journey language:** signer-facing lists now distinguish personal status
  from overall request status. Closed requests retain Completed, Declined,
  Expired, Cancelled or Result rejected instead of collapsing to Done. Strong
  and external journeys use short action-led phases, and protocol details stay
  behind reviewer or security disclosures.
- **Strong identity setup:** reviewers now send the personal Pocket ID setup
  link by email; copying it is an explicit fallback and never navigates to or
  consumes it. The relationship field is presented as the organization's own
  review record, not a Pocket ID identifier. Connecting Pocket ID creates a
  reviewer activity, approval automatically resumes waiting requests, and a
  failed callback remains visible with a safe next step. Internal Odoo signers
  can continue from My Signatures when SMTP delivery fails.
- **Paperless retrieval:** the external Paperless action is offered only when
  the current user's archive identity and this document's permission are both
  synchronized. Authorized Odoo preview and download remain available, so a
  user is never sent to an archive page that will reject them.
- **Identity and signing usability:** obsolete retention configuration is gone;
  setup-link actions copy immediately with a visible fallback; Strong readiness
  stops unconfigured signers before the ceremony; method choices use responsive
  cards with plain-English help; and successful identity setup removes its spent
  action.
- **Signer and status usability:** initials use a dedicated adoption dialog and
  suggestion, the field navigator no longer rebuilds fields or duplicates
  dialogs, completed My Signatures rows open their result directly, dashboard
  cards show ACL-safe signer chips, and System Status uses a responsive kanban.
  Archive failure language consistently requires Paperless confirmation of both
  the signed PDF and proof package.

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

- `scripts/sign-qa-stack test /usl_sign` installed the module in a disposable
  database and finished 84 post-test entries with zero failures and zero
  errors. Odoo reported 90 Sign tests plus six web-suite wrappers;
- desktop and mobile Sign frontend suites each passed 20 tests and 78
  assertions. Seven headless Chrome journeys covered dashboard/Start,
  requester prepare/send/monitor, template creation, the iframe field editor,
  Standard public signing and archival, identity setup presentation and the
  Strong signing page;
- the external-signer authorization regression passed in isolation and in the
  full suite: a signer can export the frozen document but cannot import,
  validate, retry, resume, remind or mutate the external journey;
- `scripts/sign-qa-stack test '/usl_pocketid,/usl_documents'` finished 137
  post-test entries with zero failures and zero errors. Odoo reported 103
  Documents tests, 42 Pocket ID tests and six web wrappers; the mobile
  Documents suite passed 24 tests and 181 assertions;
- the Pocket ID patch rebuilt with its Go test layer uncached. The OIDC and
  WebAuthn packages passed. A virtual-authenticator Strong journey then made
  one fresh passkey assertion and completed a request with OIDC validation,
  EU DSS validation, complete evidence and Paperless archival. Browser traffic
  contained no private key material. No physical passkey was requested;
- the DSS image rebuilt without cache and Maven passed all three Java tests.
  The deployed service smoke passed CA issuance, separate manifest signing,
  PAdES construction, complementary pyHanko validation, deterministic PDF/A-3
  packaging, veraPDF, replay rejection and alteration detection;
- Paperless acceptance passed direct archival, checksum-idempotent reuse,
  simulated outage and recovery. Completion required a distinct signed-PDF
  archive record and proof-package archive record;
- the Strong worker rebuilt byte-for-byte from its locked source, and its
  private-key boundary passed. A clean temporary `npm audit` reported zero
  vulnerabilities;
- full Ruff validation passed for `usl_sign` and every new Python service/QA
  utility. Python compilation, JavaScript parsing, XML parsing, shell syntax,
  PO format, all 13 maintained French catalogues, clean Sign boundary,
  product/migration source boundary and `git diff --check` passed;
- the installed QA image passed `python -m pip check`, and
  `make user-docs-build` rendered the complete MkDocs site successfully.

The full-product database boundary is not a Sign-only gate: the worktree guard
correctly refuses the canonical project, while this isolated lightweight QA
database deliberately does not install unrelated Accounting and Project
product modules. No canonical database was opened from this feature checkout.

The final `usl-sign-native-sign-2e96-qa` tenant was rebuilt from empty
project-scoped PostgreSQL, Odoo filestore, Pocket ID and Paperless volumes. It
installed `usl_sign` on `odoo_dev` with `--without-demo`, configured the dedicated
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

A new Chrome 151 virtual-authenticator journey completed on this tip. It is
deterministic headless evidence for the ceremony and transport boundaries, not
a substitute for manual acceptance with each supported platform authenticator.

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
