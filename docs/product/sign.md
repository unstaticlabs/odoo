# USL Sign

## Product boundary

USL Sign is an Odoo-native document-signing application. Odoo is the
operational source of truth for templates, business links, signers, consent,
lifecycle, validation, evidence and archive state. Paperless receives one
durable dossier after Odoo has validated the completed ceremony; it does not
become a second workflow system.

The delivered application extends the pinned OCA `sign_oca` module at commit
`3b768318bc5eaccb79535337478f49d59d17d0b1`. OCA supplies the PDF.js authoring
surface, positioned fields, roles and portal foundation. `usl_sign` adds the
final workflow, trust guidance, immutable snapshots, secure invitations,
cryptographic services, evidence, archival and company-aware permissions.
The vendored OCA module is not forked or modified.

The alternative of building another editor and request stack was rejected:
it would duplicate the hardest interactive behavior and create a second
signing architecture. Provider-managed signing was also rejected for normal
use because it would move the workflow and evidence boundary outside Odoo and
introduce a recurring dependency. External signing remains an exceptional,
provider-neutral journey.

## Approval and trust guidance

An attributable Odoo approval is preferred when no signed PDF is required.
The linked business record's native approval is used when available;
otherwise `usl.sign.approval` records the record, approvers, decision, reason,
policy and append-only events.

Document requests have exactly three trust levels:

- **Standard electronic signature with reinforced evidence.** This is the
  default for routine documents. It combines explicit consent, secure
  individual links, a frozen document, detailed evidence and a non-qualified
  USL platform PAdES seal. The seal protects integrity and attests the evidence
  process; it is not a personal signature.
- **Strong personal signature — designed for advanced-signature
  requirements.** This is for an identified recurring signer with a reviewed
  enrolment and a fresh Pocket ID passkey interaction. Each signer authorizes a document-specific,
  short-lived personal PAdES signature. No formal advanced-signature claim is
  made until independent legal and security reviews support it.
- **Qualified external signature.** This is reserved for a formal QES
  requirement or maximum-assurance case. Odoo freezes and exports the exact
  document, then validates the imported result independently before completion.

The versioned policy engine considers the document category, company, signer
type, relationship, risk/value, enrolment and formal or counterparty
requirements. The recommendation, business reason and consequence are visible
before sending. Requested and achieved trust are separate fields. An
authorized override needs a reason, is recorded in the event chain and never
silently downgrades the request.

## Lifecycle and completion gate

The request lifecycle is:

`Draft`, `Ready`, `Sent`, `Viewed`, `Partially signed`, `Waiting for enrolment`,
`Waiting for external signature`, `Signed document to import`, `Validation in
progress`, `Completed`, `Evidence incomplete`, `Validation failed`, `Declined`,
`Expired`, `Cancelled`, and `Action required`.

Every request can hold ordered source documents and annexes, a deterministic
consolidated PDF and page map, template/field/policy/signer snapshots, ordered
signers, validation runs, evidence artifacts and an archival operation.
Published or used templates are immutable; editing starts a new version.
Sending freezes the document bytes, SHA-256 hashes, layout, roles, signer
identity, consent wording and policy.

`Completed` is a strict conjunction: every expected signer has signed, EU DSS
validation passes, pyHanko cross-validation agrees, the evidence package is
complete, and Paperless has accepted the dossier or reported a
checksum-identical duplicate. Sending mail, exporting a file, opening an
external service, uploading a PDF or receiving a claimed completion never
satisfies this gate.

The linked Odoo record shows current state, next action, requested/achieved
trust, completed PDF, completion certificate and archival state without
requiring a chatter reconstruction.

## Standard journey

Users can prepare reusable templates or one-off requests, place supported
fields by role, preview roles, use multiple documents and signers, require
signing order, set reminders and expiration, and handle refusal or
cancellation. The OCA storage and PDF foundation is extended with a
three-pane Odoo-native editor. A user chooses a typed field and signer, then
clicks the PDF; drag/drop and right-click call the same explicit placement
command. Per-template signer colors remain stable across reloads and are
shared by the palette, PDF fields, inspector and signer preview. Required
state uses a separate marker and never replaces the signer color. The editor
also provides page navigation, zoom, move, resize, delete, keyboard access,
undo/redo, autosave status, conflict detection and explicit loading,
read-only and failure states.

Each invitation contains 256 bits of entropy. Only its SHA-256 is stored. The
first use is rate-limited and exchanges the bearer secret for a short-lived,
revocable session bound to the request, signer and expiry. The policy can use
the secure invitation, a portal account or Pocket ID. The signer page works on
mobile, captures field values and explicit consent, and records the
authentication method, timestamp, IP address and user agent.

After the last signer, Odoo renders the final PDF, asks the internal DSS
service to apply the USL platform seal, re-reads the persisted bytes and runs
independent validation. The completion certificate describes the ceremony and
does not imply a personal, qualified or handwritten-equivalent signature.

## Strong personal journey

An identity reviewer first links a known partner to an explicit relationship
basis: Pocket ID, employee, contractor or recurring-partner relationship. The
enrolment records the reviewer, date, reference, notes and policy version.
The signer connects an existing Pocket ID identity, which is bound by immutable
issuer and subject rather than email. An identity reviewer then confirms the
relationship under the versioned policy. Pocket ID owns passkey registration,
recovery and credential revocation; Odoo receives no passkey public key,
counter, AAGUID or transport data. Odoo can independently revoke the Sign
enrolment, and re-enrolment never changes completed signatures.

Enrolment and signing use isolated pages with a strict Content Security Policy
and no analytics. During each signing ceremony:

1. Odoo freezes and hashes the current PDF revision.
2. A dedicated browser worker creates an ECDSA P-256 key with
   `extractable:false` and builds a PKCS#10 request using pinned
   `@peculiar/x509` 2.0.0.
3. Odoo creates a unique short-lived canonical binding covering the signer, enrolment,
   request, role, document hashes, consent digest, CSR/public-key hash, policy,
   nonce and expiry.
4. Its SHA-256 digest becomes the OIDC nonce for a dedicated confidential Sign
   client. Pocket ID requires a new credential-backed passkey interaction and
   returns a signed token with `amr=["phr"]`. Odoo verifies PKCE, state, issuer,
   audience, subject, signer group, nonce, `auth_time`, expiry and single use;
   login-code (`otp`) authorization is rejected.
5. Only then does `step-ca` 0.30.2 issue a constrained ten-minute,
   non-renewable certificate for this document and signer.
6. EU DSS 6.4 returns the PAdES data-to-sign; the worker signs it and returns
   only the certificate and signature value. DSS embeds the signature.
7. Odoo invalidates the ceremony, terminates the worker and independently
   validates the persisted PDF.

The signed ID token, bounded claims summary, JWKS snapshot and validation
result are retained as restricted evidence; access and refresh tokens are not.
The distributable/Paperless dossier contains the token hash and sanitized
validation summary, not the raw identity token; evidence reviewers can inspect
the raw signed token in Odoo.
The document and its content never go to Pocket ID. The document private key is
never exported or submitted to Odoo or Pocket ID. Strong
multi-signer requests are sequential so each personal signature covers the
prior revision. The platform seal is applied only after all personal
signatures. When an independent RFC 3161 TSA and revocation material are
configured, DSS can augment to PAdES-T/LT; otherwise the achieved PAdES level
is reported exactly as validated.

This design targets the four Article 26 eIDAS properties. The local CA and
platform seal are not qualified trust services; an existing relationship plus
reviewer is not government-ID proofing; and terminating a browser worker does
not prove physical memory zeroization.

## Qualified external journey

An administrator maintains a catalog of reviewed providers containing name,
territory, supported level, mobile URL/instructions, commercial notes,
priority and review date. These are ordinary configurable records: no provider
API client, callback, credential, route or provider-specific lifecycle is part
of the product.

Odoo exports the exact DSS-prepared frozen PDF, signer information, hashes and
instructions, then enters `Waiting for external signature`. The returned PDF
and proof files enter `Signed document to import` and `Validation in progress`.
DSS reconstructs the revision covered by the first signature and requires it
to match the exported PDF, then validates the signatures, signer attribution,
certificate chains, timestamps, qualified trust provider, qualified
certificate/device indications and actual level against configured trusted
lists. Insufficient, modified, untrusted or wrongly attributed results enter
`Validation failed`; requested trust is never copied into achieved trust.

## Cryptographic and evidence architecture

The narrow `services/usl-sign-dss` Java service pins EU DSS 6.4 and provides
PAdES preparation, embedding, platform sealing, augmentation, validation,
revision comparison, deterministic PDF/A-3 dossier construction, veraPDF
checking and manifest signing. It accepts only mutually authenticated TLS,
applies payload/time limits, uses ephemeral files and sanitizes failures.

`step-ca` 0.30.2 uses an offline root, online intermediate, restricted
provisioner, template-enforced identity/document claims, ten-minute maximum
leaf lifetimes and no renewal. CA, platform-seal, manifest, TSA and mTLS
credentials are mounted secrets and never stored in Git or editable Odoo
fields. pyHanko is a pinned independent cross-validator, never the authority;
a disagreement causes `Action required`.

Every meaningful operation appends an immutable event containing its sequence,
previous hash, canonical payload hash, actor/authentication, IP, user agent,
transition and timestamp. Request completion and daily signed head manifests
verify the complete chain. Daily heads may optionally be submitted to
OpenTimestamps; that anchoring is not described as RFC 3161 or qualified.

The retained evidence includes source and annex PDFs, the frozen PDF and page
map, fields/roles/policy/signers, consent, hashes, events, personal/platform
certificates and chains, timestamps and revocation material, all DSS reports,
external proof, signed canonical manifest, completion certificate and signed
PDF. A deterministic PDF/A-3 dossier embeds the artifacts behind a readable
cover, passes veraPDF, receives a platform seal and is sent through the
checksum-idempotent `usl_documents` Paperless operation. Failed archival is
visible and safely retryable; it blocks completion.

The single OCA Sign option **Send signers a copy of the final signed document**
defaults to enabled. USL Sign deliberately defers delivery until independent
validation and Paperless archival are confirmed. It then queues the final
PDF/A-3 evidence dossier, which embeds the signed PDF, completion certificate
and validation evidence. There is no separate USL delivery option.

## Permissions and company isolation

- **Sign User** creates and follows their company requests.
- **Template Manager** publishes versioned templates and layouts.
- **Identity Reviewer** reviews relationships and Pocket-bound enrolments, and
  can independently revoke Strong signing access.
- **Evidence Reviewer** inspects validation and evidence without changing the
  ceremony.
- **Sign Administrator** manages policies, provider catalog, services and
  recovery actions.

Global company rules isolate all operational and proof records. Controlled
actions use narrowly scoped elevation while preserving the true actor in the
event payload. Cryptographic evidence stays out of routine chatter.

## Release boundary

The delivered registry and source contain only the final product model. The
Sign development database is disposable and is rebuilt from the current
modules. No compatibility model, reconstruction service, source binding,
provider adapter or transition-only migration is shipped. Release checks scan
the source, registry, schema, routes, jobs, settings, assets, tests and docs for
obsolete Sign residue and run the repository product/migration boundary.
