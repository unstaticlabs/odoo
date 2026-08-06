# USL Sign validation and release-readiness report

Status date: 2026-08-06

## Paused post-merge checkpoint

The journey-led productization work was paused after merging the completed
Pocket ID passkey branch. The merge keeps Pocket ID as the passkey owner and
keeps Odoo responsible for document binding, consent, identity review,
certificates, PAdES, evidence and workflow. The merged source has not been
deployed or revalidated; the results below describe evidence produced on the
two parent implementations and must not be presented as a fresh merged-build
result.

Pending work when development resumes:

- Review the complete merged diff, especially the shared request lifecycle,
  enrolment rules, strong-signing controller, portal templates and evidence
  boundary.
- Reconcile this older validation narrative with the Pocket ID product and
  runbook wording; remove obsolete Odoo-owned WebAuthn/RP references.
- Adapt or split `test_strong_browser.py`: keep its requester, Standard mobile
  and workspace acceptance coverage, but replace its obsolete Odoo-owned
  virtual WebAuthn ceremony with the Pocket ID acceptance harness.
- Rebuild disposable Sign data from the final modules, then install or upgrade
  `usl_pocketid` and `usl_sign` in `odoo_dev` with the dedicated Sign OIDC
  client and external secrets.
- Run clean-install Odoo tests, desktop/mobile frontend tests, Pocket ID Go
  tests, DSS/pyHanko/veraPDF checks, Paperless acceptance, the Sign clean
  boundary, `make product-migration-boundary`, and the end-to-end requester and
  signer journeys.
- Confirm the final registry and source contain no Odoo-owned passkey models,
  credential material, WebAuthn registration ceremony, legacy provider code
  or stale configuration UI.
- Refresh this report using only post-merge evidence. Preserve the independent
  legal/security audit, additional authenticator/browser, TSA/PAdES-T/LT and
  qualified-external sample limitations until they are genuinely closed.

## Outcome and product boundary

USL Sign is implemented as the product extension `usl_sign` over the pinned,
unmodified OCA `sign_oca` foundation. OCA remains responsible for its native
template, role, PDF.js and portal primitives. USL owns the journey workspaces,
typed editor behavior, clean lifecycle, trust guidance, secure invitation and
passkey ceremonies, DSS/CA boundary, immutable proof, completion gate and
Paperless dossier.

The delivered product path contains no provider API adapter, provider
credential, webhook lifecycle, reconstruction module, compatibility model or
development-record migration. The development Sign state is disposable and is
rebuilt from the final modules. Existing production data migration is not part
of this clean-development release.

## User journeys implemented

- Sign opens on the personal action landing page. The only top-level
  workspaces are Library, Open Requests, My Signatures and Configuration.
- Start routes either to an attributable business decision or to a document
  signature request. Document signing continues through template or one-off
  PDF preparation.
- Library combines ready/draft Templates with strictly gated Completed
  Documents. It never treats an upload or unarchived result as completed.
- Requesters and named coordinators prepare, monitor, remind and recover work
  within company-aware permission limits. Signers see only their signer-focused
  workspace unless they also own or coordinate a request.
- Standard signing supports typed fields, multiple source documents and
  signers, optional order, consent, refusal, reminders, expiration,
  cancellation, validation and proof retrieval. Every submission is bound to
  the exact reviewed PDF revision and serialized so concurrent signers cannot
  overwrite one another.
- The public PDF.js signer view now normalizes OCA's text/checkbox role
  comparison at the USL extension boundary. The frozen public payload exposes
  the semantic and technical field type plus the configured default. Name,
  Date, Email and Phone render as focused native controls with appropriate
  autocomplete behavior; invalid email input is rejected by the browser before
  submission. Values are submitted only for their assigned signer without
  modifying the vendored OCA module.
- Strong personal signing uses reviewed enrolment, passkeys, a browser-worker
  P-256 key marked non-extractable, a document-specific challenge, a
  ten-minute step-ca certificate, personal PAdES embedding and independent DSS
  validation. Request/signer row locks plus a partial unique database index
  allow only one live key-and-certificate ceremony per signer, including across
  duplicate browser tabs.
- Qualified external signing is manual and provider-neutral. Odoo freezes and
  exports the exact PDF, tracks waiting/import states, reconstructs the first
  signed revision and accepts completion only after DSS establishes the
  required qualified result and signer attribution.

## Trust and legal positioning

The only document-signature levels are:

- Standard electronic signature with reinforced evidence.
- Strong personal signature — designed for advanced-signature requirements.
- Qualified external signature.

Requested, recommended and achieved trust remain separate. Authorized
overrides require a reason and never silently downgrade a request. The local
CA, passkey ceremony and platform seal are not described as qualified trust
services. The strong local journey does not claim formal Advanced Electronic
Signature status pending independent legal and security review.

## Components and security decisions

- OCA Sign supplies the maintained Community application foundation.
- EU DSS 6.4 is the authoritative PAdES construction and validation engine.
- pyHanko 0.36.2 is an independent cross-validator; disagreement blocks
  completion.
- `webauthn` 3.0.0 verifies RP ID, origin, exact challenge, user verification,
  credential state and counter behavior.
- Web Crypto and pinned `@peculiar/x509` 2.0.0 generate the document key and
  CSR in an isolated worker. Only the CSR/public key, certificate and signature
  value cross the browser boundary.
- step-ca 0.30.2 issues constrained, non-renewable personal certificates.
- veraPDF 1.30.2 validates the PDF/A-3 archival dossier.
- PDF platform seals and evidence manifests use different mounted leaf keys.
  DSS refuses startup when their public keys are identical.
- Service transport uses mutual TLS, bounded payloads, ephemeral processing
  and sanitized errors. Private keys and service credentials remain outside
  Git and Odoo configuration fields.

## Evidence and archival behavior

Odoo retains the source PDFs and annexes, frozen consolidated bytes and page
map, fields, roles, policy and signer snapshots, consent and authentication
evidence, original/final hashes, append-only event chain, personal/platform
certificates, timestamps/revocation summaries, DSS reports, pyHanko result,
external proof, completion certificate, signed manifest and final PDF.
Certificate-chain extraction is a mandatory first-class evidence artifact:
pyHanko returns each PDF signature's DER chain while DSS remains the validation
authority, and completion fails closed if that immutable artifact is absent.

The evidence service builds a deterministic human-readable PDF/A-3 dossier,
embeds the evidence artifacts, validates it with veraPDF and applies a platform
seal. The existing checksum-idempotent `usl_documents` operation archives the
dossier to Paperless. Completion requires every expected signer, matching
achieved trust, valid DSS and pyHanko results, complete evidence and a confirmed
Paperless document relationship. Archive failure remains visible and retryable.
Post-dossier archive events are covered by the next signed daily event-head
manifest to avoid a circular dossier checksum.

## Automated and service validation

The following current-tree commands passed:

- `scripts/odoo-dev test usl_sign odoo_sign_mobile_standard_final`
  - 65 post-tests; Odoo statistics reported 67 `usl_sign` tests and 6 `web`
    tests; zero failures and zero errors.
  - Desktop JavaScript: 12 tests and 56 assertions.
  - Mobile JavaScript: 12 tests and 56 assertions.
  - Authenticated WebClient landing/Library coverage, passkey enrolment and two
    ordered personal PAdES browser-worker ceremonies passed. Two distinct
    enrolled people and virtual platform passkeys were used; the second
    ceremony's bound document hash equals the persisted PDF revision produced
    by the first, and both browser network traces exclude private-key material.
  - A real public Standard signer page loaded the PDF through PDF.js, rendered
    assigned Name, Date, Email, Phone, Checkbox and visual Signature fields,
    proved the Name
    control remains connected while focused and edited, prefilled the native
    Date/Email/Phone controls, rejected an invalid email before the request
    route, opened OCA's standard signature dialog, adopted and placed the visual
    signature, captured all corrected values and explicit consent, submitted
    the production route, produced a platform-sealed PAdES, passed DSS and
    pyHanko validation, generated the completion certificate and evidence
    dossier, and reached the archive-gated completed state.
  - The same request first proved the signer page and PDF fields render at a
    desktop viewport, then completed the full ceremony under Chrome mobile
    device emulation at 390 by 844 pixels. The final action bar remains inside
    the viewport after OCA reveals it, and the typed fields, checkbox, visual
    signature dialog, consent, validation and archive gate all operate at that
    narrow width.
  - Fresh isolated test databases intentionally contain no Paperless API
    credential. This browser test therefore substitutes only the final
    transport response with a checksum-identical Paperless result; the normal
    archival operation, dossier checksum, completion gate and relationship are
    still exercised. Separate archival tests cover live operation creation,
    failure, retry, checksum deduplication and linking, and the deployed
    `odoo_dev` environment has real Paperless connectivity.
- Focused stale-revision test:
  `TestCleanUslSign.test_unordered_standard_signers_are_bound_to_the_reviewed_pdf_revision`.
- Multi-document freeze regression:
  `TestCleanUslSign.test_multiple_documents_and_annexes_freeze_in_order_with_individual_hashes`;
  this proves deterministic source/annex ordering, page mapping, per-document
  SHA-256 evidence and the consolidated frozen hash.
- Strong single-flight regression:
  `TestCleanUslSign.test_only_one_live_strong_ceremony_can_exist_per_signer`;
  the deployed database contains the partial unique index and no duplicate live
  signer ceremonies.
- Standard public-browser regression:
  `TestStrongBrowser.test_standard_signature_through_public_browser_and_archive`;
  this covers the OCA/USL typed-field role boundary, focus stability and native
  date input through the actual public signer interface rather than a
  controller-only simulation.
- `docker compose build usl-sign-dss`
  - Java compilation and three JUnit identity-matching tests passed.
- `docker compose exec -T odoo usl-sign-services-smoke`
  - live mTLS DSS and CA health;
  - short-lived constrained certificate issuance;
  - one-time CA-token replay rejection;
  - independently verified evidence-manifest signature and purpose certificate;
  - platform PAdES sealing and DSS/pyHanko agreement;
  - exact first-revision matching and altered-PDF rejection;
  - deterministic PDF/A-3 construction and veraPDF validation before and after
    sealing.
- `make sign-product-validate product-migration-boundary`
  - clean Sign residue boundary, deterministic browser-worker/private-key
    boundary and product/migration boundary passed.
- `scripts/odoo-dev ruff custom-addons/usl_sign custom-addons/usl_documents/models/res_users.py custom-addons/usl_documents/tests/test_documents.py`
  - all checks passed after consolidating the Sign test-package import.
- `scripts/odoo-dev test usl_documents odoo_documents_queue_idempotency`
  - 94 post-tests; Odoo statistics reported 96 `usl_documents` tests and 6
    `web` tests; zero failures and zero errors.
  - 27 desktop UI tests (190 assertions) and 24 mobile UI tests (181
    assertions) passed.
  - The new no-op group-write regression proves repeat QA identity/profile
    configuration does not enqueue permission changes for every document. The
    existing company-access and manager-role removal tests still prove remote
    permission revocation and rollback fail closed.
- `git diff --check` and shell syntax checks passed.

During development, a regression intentionally triggered the PostgreSQL unique
constraint and Odoo correctly rejected the duplicate, but the Odoo test runner
classified the deliberate database error as a suite error. The final regression
therefore verifies the installed unique index and safe challenge replacement
without deliberately aborting the test cursor. Initial public-browser attempts
also exposed three fixture/test-harness defects: a required company email was
missing, a browser-ready expression returned a DOM node instead of a Boolean,
and the isolated database had no Paperless credential. Each was corrected or
bounded as described above before the passing run. A separate initial test
attempt lacked sandbox access to Docker Buildx activity files; the same command
completed successfully with normal Docker permissions.

An initial focused Documents companion test also collided with an existing
fixed Paperless ID because `test-tag` intentionally uses the populated
`odoo_dev` database. The isolated module suite above rebuilt a clean disposable
database and passed both the new idempotency case and the existing revocation
coverage.

The final test iteration also caught a Chromium behavior where changing a text
input to `type=date` cleared its already assigned value. USL now reapplies the
value after the type change and the full suite proves the date persists. The
same acceptance journey now proves native email and telephone semantics and
browser-side rejection of malformed email input. The first visual-signature
automation attempt watched the field node that OCA correctly replaces after
adoption and therefore failed despite a successful dialog action; the assertion
now follows the live field by stable ID and the full suite passes. A first direct host `ruff`
invocation was unavailable because the binary is intentionally provided by the
project dev container; the documented `scripts/odoo-dev ruff` command then
passed. The first mobile-layout assertion expected the action footer to be
visible before required fields were complete; OCA deliberately hides that
footer until completion. The check now runs when the footer is revealed and
proves it fits the 390-pixel viewport. The Codex in-app Browser could inspect the real PDF.js iframe and
confirm the typed controls, but its automation bridge could not retain focus or
activate the signature control inside that iframe. The production Chromium
browser test now completes the visual signature dialog and the whole public
journey, so the in-app limitation is recorded as a manual-tool limitation rather
than counted as UI acceptance evidence.

The validated module is deployed to the disposable `odoo_dev` target. The
synthetic “Routine Agreement - QA” template and Pocket ID-backed QA profiles
are provisioned by `scripts/odoo-dev bootstrap-sign-qa`. Direct inspection of
the deployed public signer iframe confirmed the assigned Name and Date inputs
and Signature action render in the running product.

A fresh deployed Routine Agreement request was then completed through the
normal signer model and live DSS, pyHanko, dossier and Paperless services. The
result reached `completed` only after validation was `valid`, evidence was
`complete`, archival was `archived` and Paperless document 650 was linked to
Odoo archive document 551. The certificate artifact contains one PDF signature
and two DER certificates. Odoo and Paperless each returned exactly 157,380
bytes with SHA-256
`1244480ce650c2fd75e72cbdca8cab6a8a3738781964180ee3c893ba52be7aa9`;
the byte comparison and event-chain verification both passed.

## Operational configuration

The local environment exposes Odoo at
`http://odoo.localhost:20436/web/login?db=odoo_dev`, Pocket ID at
`http://pocket-id.localhost:1411`, and Paperless at
`http://paperless.localhost:8010`. QA identity switching uses registered Pocket
ID passkeys; local password shortcuts remain disabled for governed users.

Production requires independently managed offline-root and online-intermediate
procedures, restricted step-ca provisioner material, separate platform and
manifest keystores, DSS and Odoo mTLS credentials, reviewed WebAuthn RP/origin
values, Paperless metadata configuration, monitoring and backup/restore tests.
Qualified validation additionally requires a protected EU LOTL keystore and
current LOTL/OJ URLs. RFC 3161 TSA and OpenTimestamps configuration is optional
and must be described according to the assurance it actually provides.

## Remaining release gates and genuine limitations

- A real-device acceptance record is still required for every advertised
  platform authenticator/browser combination. Current repeatable evidence uses
  Chromium with a virtual platform authenticator; it is not evidence for Face
  ID, Touch ID, Windows Hello, Safari or Firefox.
- Qualified-external acceptance still needs real, non-confidential provider
  samples with a configured current EU trusted list, including successful QES
  and modified, untrusted, signer-mismatched and insufficient-level failures.
- Independent security review must assess the WebAuthn ceremony, browser
  isolation, CSP/XSS surface, CA templates, key operations, service boundary
  and operational response procedures.
- Independent legal review must assess policies, identity-proofing strength,
  consent language, evidence retention and the intended Article 26 position.
- Browser-worker termination cannot prove physical memory zeroization.
- The optional RFC 3161 TSA and OpenTimestamps anchor are not configured in the
  local QA environment.
- The long-lived disposable Paperless QA instance had accumulated roughly
  45,000 stale metadata-update jobs. The cause was an idempotency defect where
  repeated no-op `group_ids` writes re-queued every visible document; effective
  before/after access, manager and active state are now compared and the full
  Documents suite passes. The existing backlog was not purged. The fresh
  dossier stayed correctly in `evidence_incomplete`/`processing` until its own
  job ran; only that job was moved ahead for this verification. A QA reset or
  bounded queue-maintenance procedure is still needed before repeatable
  performance acceptance. This did not bypass any Sign completion gate.
- Odoo logs a framework warning because the clean lifecycle intentionally
  replaces OCA's development-state selection instead of retaining obsolete
  values through `selection_add`; the registered vocabulary and database
  boundary tests prove only the final lifecycle is present.

These external and real-device gates prevent a claim that the entire product
is release-complete today. They do not provide a bypass: unavailable or
insufficient trust remains a fail-closed workflow state.
