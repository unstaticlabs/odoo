# Sign validation and release-readiness report

Status date: 2026-08-27

## Outcome

This feature delivers a document-first, Odoo-native signing application on the
aligned `19-usl` saas-19.3 parent `ae00fc0fbda7`. It extends the pinned OCA
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
  Empty sections are compact, signer chips expose each person's progress, and a
  Strong draft whose signer identity is not ready moves to Needs attention.
- **Templates:** the default workspace is the native OCA kanban. Upload and
  empty-space drop create one validated template envelope and open the editor.
  Native cards, prepare/use, duplicate, archive and immutable-version behavior
  remain available.
- **Editor:** the palette crash caused by stale drag state is fixed. Palette
  click, drag and right-click use one placement command. A field can be moved
  from its entire body, while its explicit controls retain their own actions.
  The adapter handles pointer cancellation, iframe reload and normalized page
  coordinates.
- **Simple PDF requests:** Request signatures defaults to upload, accepts one or
  more signers, derives the document name, and opens field placement
  immediately. Optional naming, message, record linking and template reuse stay
  available without interrupting the common journey.
- **Requests:** Open Requests contains only requests owned or coordinated by
  the current user. The form leads with people, signing method, deadline,
  progress and one next action. Verification and file fingerprints stay in a
  reviewer-only disclosure under Result & proof. Ready requests provide a
  check-before-you-send summary of the document, signer roles, signing method,
  proof level, deadline and message. Strong requests identify missing personal
  identity setup before any invitation can be sent.
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
  My Signing Identity is a single direct record rather than a list and presents
  the two human steps—connect Pocket ID, then organization review—without
  exposing internal policy versions or evidence identifiers.
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
- **Final signer polish:** public review, identity, processing and completion
  states use small dependency-free vector scenes with honest adjacent status
  text. Only an accepted completion displays a success mark; identity and
  review motion never claim verification. Motion stops under the browser's
  reduced-motion preference, and the public pages retain static content and
  viewport-unit fallbacks for older browsers.
- **Requester and notification closure:** Request signatures persists the
  selected method before field placement, explains its authentication and
  retained proof, and discloses Strong signer order. Recently completed is
  capped at five with a direct Completed Documents continuation; completed
  workspaces are newest first. The Sign notification drawer lists actionable
  assignments without horizontal overflow at desktop, mobile or zoomed widths.
- **Unified signer workspace:** Standard and Strong use the same field state
  model. Every assigned field remains manually fillable; the optional guide
  only focuses incomplete fields and survives scrolling, resizing and manual
  edits. Consent records the server-observed connection, bounded browser/device
  context and the explicit granted, refused or unavailable location outcome.
  Withholding browser location never blocks signing by itself.
- **Strong PDF topology:** each signer receives a one-use candidate bound to
  the unchanged base revision, completed fields, consent, browser evidence,
  Pocket ID nonce, CSR and short-lived certificate. Personal PAdES revisions
  are published sequentially under a request lock; the final platform seal is
  applied only after every personal revision and certificate chain validates.
  Candidate bytes and browser key context are cleared on every terminal path.
- **Evidence dossier v2:** new packages use stable human-readable artifact
  names and contain no database identifiers. The signed manifest covers the
  original, frozen and signed documents, completion certificate, signing
  summary, event history, validation summary, certificate chains, technical
  reports ZIP and optional timestamp receipts. The checker retains v1 and
  imported-dossier support and never reuses one embedded file for two manifest
  entries.
- **Archive operations:** Paperless permission synchronization groups documents
  with identical ACLs into bounded batches, commits only successful batches and
  isolates failures. Private QA uses an explicit browser origin, while Odoo's
  own preview/download remains the authoritative fallback. Sign and Documents
  share the same governed archive cards, previews, links and permissions.
- **Administration:** native Settings → Users & Companies → Companies remains
  unchanged. Accounting Managers receive a separate Settings → Users &
  Companies → Electronic Invoicing entry for readiness controls; other users
  cannot see or invoke that specialized action.

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

The following checks were run from this worktree without a physical
authenticator and with both regulatory live flags set to `0`:

- the final `scripts/sign-qa-stack test /usl_sign` run reported 104 Sign tests,
  six web wrappers and 98 loaded post-test methods with zero failures or errors.
  Desktop and mobile frontend suites each passed 40 tests / 147 assertions;
- `scripts/sign-qa-stack test /usl_documents` reported 108 Documents tests,
  six web wrappers and 106 loaded post-test methods with zero failures or
  errors. Desktop passed 28 tests / 203 assertions and mobile passed 25 tests /
  194 assertions;
- a clean disposable `scripts/odoo-dev test rebuild_account_migration` run
  reported 66 distribution Accounting tests, three OCA reconciliation tests,
  six web wrappers and 53 loaded methods with zero failures or errors. Both
  electronic-invoice browser tours passed; desktop passed 26 tests / 91
  assertions and mobile passed 25 tests / 88 assertions. The intentionally
  logged failed self-check belongs to a negative recovery test;
- the patched Pocket ID image rebuilt from its pinned source. Its OIDC and
  WebAuthn Go packages passed. No reviewer rate-limit override was introduced;
- EU DSS Maven verification passed all five Java tests. The deployed service
  smoke passed CA issuance, separate manifest signing, PAdES construction,
  complementary pyHanko validation, deterministic PDF/A-3 packaging, veraPDF,
  replay rejection and alteration detection;
- virtual-authenticator Strong request 15 completed with two distinct personal
  certificates, two intact incremental PAdES revisions, one final platform
  seal, fresh signed Pocket ID evidence, cleared ceremony secrets, manifest v2
  and both Paperless archives. The resulting PDF has SHA-256
  `92488b83b82fd8b3fc47c416fa06ece855b896338edc10d9362b05906b008cba`;
- Standard direct request 20 and outage/recovery request 21 both completed and
  archived. Each retained two signer attestations, exactly one platform seal,
  a distinct signed-PDF archive and a distinct dossier archive. Duplicate
  submission reused the checksum-identical Paperless objects;
- `make sign-product-validate` rebuilt both locked browser workers byte for
  byte and passed the private-key boundary. `npm audit` reported zero
  vulnerabilities and the installed QA image passed `python -m pip check`;
- scoped Ruff, Python compilation, JavaScript and shell parsing, XML parsing,
  `msgfmt`, all 15 French catalogues, action-helper validation, clean Sign
  boundary, product/migration source boundary and `git diff --check` passed;
- all eight Sign restoration matcher tests passed, including duplicate export
  prevention, byte-and-size identity matching and exact external-archive
  perimeter enforcement;
- the preserved `odoo_dev` product database passed
  `make product-migration-boundary`: all 14 product modules are installed and
  no migration module, model, field, table, column or XML ID remains in the
  operational registry or schema;
- the Impeccable detector returned no findings across Sign source templates,
  views, JavaScript and styles. Automated desktop/mobile journeys cover manual
  filling, guide switching, scrolling and resizing, repeated fields, adoption,
  keyboard use, overflow, Strong fields and recovery states;
- `make user-docs-build` rendered the complete documentation site. The only
  output was Material for MkDocs' informational future-MkDocs-2 notice.

The feature remains based on the explicitly selected parent
`ae00fc0fbda702029b684bfc1da72107df8e06d7`. Current `origin/19-usl` adds two
later documentation-only commits ending at `65b9bd882706`; they touch no file
changed by this feature and the three-way merge audit reports no conflict. They
are intentionally not replayed into this pinned feature history.

The final preserved `usl-migration-native-sign-2e96-qa` tenant is upgraded in
place, not reset. The forward upgrade retained the one completed ceremony that
predates candidate-hash binding; all newly created ceremonies must carry the
three binding hashes and cannot clear them later. The QA updater now upgrades
the complete installed Sign dependency set rather than `usl_sign` alone. Its
existing `odoo_dev` data, PostgreSQL volume, filestore, identity records and
Paperless archive remain intact: 11 requests, 15 signers, one template, 682
pre-existing Odoo document records and 1,743 attachments were present before
and after the module upgrade.

The final browser origins are
`http://odoo-sign-native-sign-2e96-qa.localhost:17025` and
`http://paperless-sign-native-sign-2e96-qa.localhost:20025`; Pocket ID remains
`https://pocketid-odoo-dev.unstaticlabs.com`. Paperless allowed-host,
CSRF/CORS, OIDC and Odoo deep-link configuration use that private Paperless
origin. Odoo and Paperless listen only on Roger's private address; database,
CA and DSS ports remain internal.

Four already-consumed QA archive operations were initially stranded by a
restored Paperless workflow that assigned their documents and successful task
records to superseded integration identities. Their Paperless IDs 683–686 and
full SHA-256 values matched the four Odoo operations exactly. Only those
matched objects were transferred to the canonical Documents integration
identity, after which the normal Odoo reconciliation linked both signed PDFs
and both dossiers and advanced Standard request 9 and Strong request 11 from
Evidence incomplete to Completed. The Sign QA configurator now reuses that
canonical identity instead of creating a competing archive owner; the obsolete
identity is disabled and its token revoked. The final credential can read both
pre-existing and new archive records, all 686 Odoo archive operations are
Archived, and the preserved Paperless queue is stable at zero queued and zero
in flight. No file, task, history or unrelated queue entry was deleted or
recreated.

The expected registry warning about replacing OCA's coarse request state
selection remains deliberate: the product requires the exact lifecycle and
does not alter OCA source.

## Odoo Online Sign restoration qualification

The read-only source snapshot `source-0b9916db4807` was restored twice into the
disposable `codex-migration-sign` project, never into the reviewer QA project.
The second pass reused the same USL and Paperless records and hashes. Both
validations proved 8 external requests, 11 participants, 53 preserved audit
events, 33 other history messages, 40 request-linked Paperless artifacts and
one additional unique inactive-template archive root. Each request has exactly
five governed artifact purposes: exported signed PDF, exported Odoo
certificate, source PDF, source-time certificate and readable sanitized
history. Two checksum-identical inactive templates reused an existing root.

Finalization removed all 105 temporary source bindings. The final registry
contained no source Sign models, migration models, migration fields or native
validation/evidence claims on the external records. The attachment ledger then
reported 81 still-pending source file IDs, all outside Sign; the wider source
migration therefore remains blocked on seven explicitly incomplete non-Sign
scopes. The Sign source matcher, attachment disposition and deterministic
replay tests passed, as did both product/migration boundary gates.

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
