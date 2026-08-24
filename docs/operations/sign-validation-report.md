# USL Sign validation and release-readiness report

Status date: 2026-08-24

## Outcome

The current feature tip delivers one Odoo-native signing product on the pinned
OCA Sign foundation. It closes the creation, editor, request, signer, decision,
service-health, evidence and recovery journeys without modifying vendored OCA
source. Pocket ID owns passkeys; Odoo owns workflow, consent, document binding,
identity review, evidence and archival; EU DSS remains the authoritative PAdES
construction and validation engine.

The final product model exposes only Standard, Strong personal and Qualified
external document signing. Internal Decision requests are attributable Odoo
decisions with their own signed and archived proof; they are never presented as
electronic signatures.

## Product closure

- **Navigation:** Sign opens on the journey landing. Top-level workspaces are
  Library, Open Requests, My Signatures and Configuration.
- **Library:** the root opens native OCA Templates. Users can upload or drop one
  or more PDFs, create one atomic envelope and enter the editor immediately.
  Completed Documents and Decisions are separate native Library entries.
- **Template editor:** typed fields, stable signer colors, explicit roles,
  click/right-click/palette placement, whole-field movement, bounded resize,
  keyboard actions, ordered autosave, rollback, undo/redo and immutable template
  versions remain in the focused `usl_sign` extension.
- **Requests:** concise lifecycle, signer progress, requested/achieved trust,
  blocker, proof and archive summaries lead to the next safe action. Completion
  still requires every signature, independent validation, complete evidence and
  checksum-confirmed Paperless archival.
- **Standard:** signer-specific links, typed fields, consent, authentication,
  immutable hashes, local platform PAdES seal, DSS validation, completion
  certificate and dossier archival.
- **Strong personal:** Pocket ID fresh passkey authorization, a browser-worker
  non-exportable document key, document-bound challenge, short-lived step-ca
  certificate, DSS personal PAdES and evidence archival.
- **Qualified external:** provider-neutral export/wait/import flow. DSS must
  prove the exact exported revision, signer and requested qualified level.
- **Service Status:** a company-aware administrator workspace reports Standard,
  Strong, Qualified external, Daily proof and optional TSA capabilities as
  Ready, Degraded, Not configured, Unreachable or Action required. It persists
  only safe operational results.
- **Decision proof:** Any-one and Everyone-must-approve rules produce an
  append-only decision history, canonical signed manifest, validated PDF/A-3
  receipt and checksum-idempotent Paperless archive. Proof failure retains the
  outcome and exposes recovery.
- **Daily proof:** closed UTC-day manifests chain per company and list the exact
  request/decision hashes. OpenTimestamps anchoring is asynchronous and does not
  block signature or decision completion.

## Security and legal position

- Pocket ID receives only a binding digest, never a document.
- Every Strong authorization binds signer, enrolment, request, role, exact PDF
  hashes, consent, CSR/public key, policy, nonce and expiry.
- Fresh `amr=["phr"]` authentication is required; OTP, replay, stale ceremony
  and different-document use fail closed.
- The ECDSA document key is created `extractable:false` in an isolated browser
  worker. Odoo receives only the CSR/public key and signature value.
- Public Strong pages use a strict CSP without analytics or unrelated bundles.
- Platform-seal, manifest-signing, CA, mTLS, OIDC and Paperless secrets remain
  outside Git and Odoo settings fields.
- Standard is labelled “Standard electronic signature with reinforced
  evidence.” Strong remains “Strong personal signature — designed for
  advanced-signature requirements.” No formal AES, qualified or handwritten-
  equivalent claim is made for local signatures.
- OpenTimestamps proves that the signed manifest and listed hashes existed no
  later than a verified Bitcoin block time. It is not an exact signing time,
  RFC 3161 timestamp, signer identity, qualified timestamp or QES.

## Evidence and archival

Odoo retains structured operational state and the source/frozen/final PDFs,
annexes, page map, fields, roles, policies, signer identity, authentication,
consent, hashes, append-only event chain, certificates, DSS reports, pyHanko
result, completion certificate and signed manifest. The deterministic PDF/A-3
dossier embeds the durable artifacts, is validated with veraPDF, platform-
sealed and archived through the checksum-idempotent `usl_documents` Paperless
operation.

Daily manifests are signed with a key distinct from the PDF platform-seal key.
Odoo retains the original and upgraded portable `.ots` receipts. Confirmation
requires agreement between Blockstream and mempool.space, local raw-header hash
calculation, protocol attestation verification and six confirmations. The
confirmed daily proof receives its own sealed PDF/A-3 dossier and Paperless
record; existing request dossiers are not rewritten.

## Reproduced validation

The final current-worktree run passed:

- `scripts/sign-qa-stack test /usl_sign`
  - Odoo reported 73 `usl_sign` tests and 6 `web` tests.
  - 67 post-tests completed with zero failures and zero errors.
  - Desktop frontend: 13 tests, 57 assertions.
  - Mobile frontend: 13 tests, 57 assertions.
  - Headless requester, Standard signer, focused Strong page and native landing
    journeys completed without a physical authenticator.
- `scripts/sign-qa-stack smoke`
  - mTLS CA/DSS, signing-key separation, constrained certificate issuance,
    DSS/pyHanko agreement, deterministic PDF/A-3, veraPDF, sealing, replay and
    alteration checks passed.
- `make sign-product-validate product-migration-boundary`
  - Clean Sign provider/legacy residue boundary passed.
  - Browser-worker reproducibility and private-key boundary passed.
  - Product/migration boundary passed.
- `docker exec -i usl-sign-0a32-qa-odoo-1 python3 - /mnt/custom-addons <
  scripts/check_fr_translations.py`
  - All 11 French product catalogues passed.
- Fresh QA bootstrap
  - Created a new `odoo_dev --without-demo` database from module state.
  - Generated and archived one synthetic completed Decision proof through the
    real local DSS and Paperless services.
  - Seeded four templates and focused open journey records.
  - Odoo, Pocket ID, step-ca, DSS, Paperless and their databases report healthy.

The QA bootstrap found and closed two permission-boundary defects before this
final run: internal Decision participant rows could not be reconciled by a Sign
user, and a decision-maker could not read the service-owned Paperless operation
while finalizing proof. The fix keeps the underlying models protected and uses
elevated access only inside the constructed internal workflow. Dedicated
regression tests cover both cases.

The expected registry warning about replacing OCA's coarse request state
selection remains. This is deliberate because the product requires the exact
final lifecycle and no OCA vendored source is changed.

## Isolated manual-QA tenant

The running project is `usl-sign-0a32-qa`. It uses new project-scoped database,
filestore, Pocket ID, CA/DSS, Paperless and secret resources. It was initialized
without a dump or copied volume. Source add-ons are bind-mounted from this
worktree and the reusable runtime image is content-addressed from dependency
inputs.

- Odoo: `http://odoo-sign-0a32-qa.localhost:17408`
- Pocket ID: `http://pocket-id-sign-0a32-qa.localhost:19408`
- Paperless: `http://127.0.0.1:20408`
- Database: `odoo_dev`

The focused seed contains Valentin as requester/administrator, Roger as signer
and decision-maker, four practical templates, a ready Routine Agreement, a
Strong enrolment-blocked request, a Qualified external waiting request, a
pending decision and a genuinely finalized synthetic decision proof. It does
not contain fabricated completed signatures.

## Prior real-device evidence

The merged Pocket ID feature branch previously completed a real Chrome 150 on
macOS 26.6 Touch ID journey. It reached `completed`, confirmed fresh
`amr=["phr"]`, validated PAdES Baseline B through DSS, archived complete
evidence in Paperless, cleared ceremony secrets and found no private JWK,
PKCS#8, seed or private `CryptoKey` in automated traffic inspection.

That remains architectural evidence, not a fresh acceptance of this current
presentation and closure tip. No Touch ID request was made in this run.

## Genuine limitations

- Independent legal and security audits remain required before a formal
  Advanced Electronic Signature classification.
- The real-device result reached PAdES Baseline B. No independent RFC 3161 TSA
  was configured, so PAdES-T/LT is not demonstrated.
- Real-device acceptance covers Chrome/macOS/Touch ID only. Safari, Firefox,
  Face ID and Windows Hello still require separate acceptance.
- No real qualified-provider sample was available for external QES acceptance.
- Final human visual acceptance is required for the current desktop/mobile
  presentation, responsiveness and manual drag/drop feel.
- OpenTimestamps aggregation normally takes hours. No live Bitcoin confirmation
  is claimed by this final run; deterministic fake calendars and explorers
  cover the protocol and recovery paths.
- Public-explorer verification is weaker than a local Bitcoin Core node. The
  portable `.ots` proof remains independently verifiable later.
- Browser-worker termination cannot prove physical memory zeroization.
- Fresh-passkey enforcement remains a tracked Pocket ID patch until released
  upstream (`pocket-id/pocket-id#1654`).
