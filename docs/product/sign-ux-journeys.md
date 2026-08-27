# Sign user journeys

This is the release checklist for the Sign experience. It complements
`docs/product/sign.md`; the product contract remains authoritative for trust,
validation, evidence and completion rules.

Each journey has one clear owner, outcome and primary action. Normal users see
business language. Security and evidence details stay available to reviewers
without interrupting everyday work.

## Experience rules

- Show the current responsibility and next action before background details.
- Keep one primary action on each screen.
- Use **Standard**, **Strong personal** and **Qualified external** in compact
  views. Show the full legal wording only when the user reviews the method.
- Say what is happening to the document. Do not expose implementation names,
  hashes, certificates or protocol terms unless a reviewer opens details.
- Never describe a request as complete until signature validation, evidence and
  final storage satisfy the product contract.
- Preserve a safe Odoo download whenever a separate Paperless login or document
  permission is unavailable.
- Every empty, waiting, failure and retry state must tell the user what happens
  next without implying that they caused an infrastructure problem.

## Ten release journeys

### 1. Send one PDF to one person

- **Persona:** occasional requester.
- **Goal:** upload an everyday PDF and send it without learning templates or
  signing infrastructure.
- **Ideal path:** Request signatures → upload PDF → choose signer → place fields → check and
  send.
- **Primary actions:** **Request signatures**, **Place signing fields**, **Send for signature**.
- **Expectation:** the PDF name, Standard method, Customer field group and
  sensible reminder settings are preselected. Optional request metadata stays
  under **Request name, message and linked record**.
- **Release check:** malformed PDF, missing email, unsaved fields and sending
  failure each have a specific recovery message.

### 2. Send a reusable template

- **Persona:** requester handling a recurring agreement.
- **Goal:** choose a ready template, assign the people and send it.
- **Ideal path:** Templates → Use template → assign one person to each field
  group → review recommendation → continue → send.
- **Primary action:** **Use template**.
- **Expectation:** field groups explain the part each person fills; no one is
  silently assigned. Ordered signing is visible when it matters.
- **Release check:** incomplete assignments, unavailable Strong identity and a
  requested method override stop before sending with a clear next step.

### 3. Build and publish a template

- **Persona:** template owner.
- **Goal:** turn one or more PDFs into a safe reusable template.
- **Ideal path:** Templates → Upload PDF → add field groups and typed fields →
  publish.
- **Primary actions:** **Upload PDF**, then **Review and publish**.
- **Expectation:** click placement is the default; drag and right-click are
  reliable shortcuts. Role colours, save status, undo and validation are
  consistent.
- **Release check:** zoom, rotation, multiple pages, reload, concurrent edits,
  immutable published versions and upload rollback are covered.

### 4. Monitor and recover a request

- **Persona:** requester or named coordinator.
- **Goal:** immediately understand who is blocking progress and safely act.
- **Ideal path:** Open Requests → open request → follow the highlighted next
  action → remind, retry, replace or cancel when appropriate.
- **Primary action:** the state-specific action in the header.
- **Expectation:** signer progress, method, deadline and status are visible
  first. Validation, identity and final-storage failures use distinct language.
- **Release check:** reminders, cancellation, expiration, duplicate actions,
  archive retry and validation retry are permission-aware and idempotent.

### 5. Sign a routine document

- **Persona:** occasional external signer.
- **Goal:** review, complete and sign from an invitation without knowing Odoo.
- **Ideal path:** open invitation → confirm email when required → review the
  whole PDF → complete assigned fields → consent → sign → receive confirmation.
- **Primary action:** **Review document**, then **Sign**.
- **Expectation:** the requesting company, document, signing identity, due date
  and remaining fields are obvious. Copy is short and natural. An adopted full
  name is shared between the signature and initials dialogs, and repeated marks
  can be filled automatically without reopening the adoption dialog.
- **Release check:** mobile layout, resume, expired link, wrong code, decline,
  missing required field and interrupted submission all fail safely. After
  signing, the signer can download the current document with an explicit
  warning when it is not final yet.

### 6. Find my pending and earlier signatures

- **Persona:** internal or portal signer.
- **Goal:** see what needs signing and retrieve what was already signed.
- **Ideal path:** My Signatures → Ready to sign or Signed by me → review/sign or
  open the result.
- **Primary action:** select a pending row to review and sign, or select a
  completed row to open the signed record and its proof directly.
- **Expectation:** personal status and overall document status are separate;
  waiting for another signer is not presented as an error. The Sign drawer
  names each pending document and opens that signing journey in a new tab.
- **Release check:** identity matching and company rules prevent sibling
  contacts or unrelated users from seeing or signing a document.

### 7. Connect a recurring signer's identity

- **Persona:** recurring signer and identity reviewer.
- **Goal:** connect the signer to Pocket ID, review the known relationship and
  make Strong personal signing available.
- **Ideal path:** reviewer sends invitation → signer connects account → reviewer
  confirms relationship → identity becomes ready.
- **Primary actions:** **Connect Pocket ID**, then **Approve identity**.
- **Expectation:** the signer understands that setup does not sign a document;
  the setup action disappears after a successful connection, and the reviewer
  sees the exact next check and the effect of revocation. If a signer reaches a
  Strong request without an active identity, the journey explains how to get a
  personal setup link instead of failing during signing.
- **Release check:** issuer/subject binding, fresh authorization, revocation and
  re-enrolment preserve completed signatures and expose no passkey material.

### 8. Apply a Strong personal signature

- **Persona:** enrolled recurring signer.
- **Goal:** deliberately authorize a personal signature on the exact document.
- **Ideal path:** review PDF → consent → confirm in Pocket ID → wait while the
  signature is added and checked → completion.
- **Primary action:** **Confirm and sign**.
- **Expectation:** three honest phases—Review, Confirm, Done—with no fake
  progress or technical ceremony language. Security details are optional.
- **Release check:** fresh authorization, exact-document binding, replay,
  different-document attack, stale session, popup failure and service outage
  are tested without a physical authenticator in routine automation.

### 9. Complete a Qualified external signature

- **Persona:** requester handling an exceptional formal requirement.
- **Goal:** take the exact frozen PDF through a reviewed external provider and
  return a valid result.
- **Ideal path:** download PDF → open provider and follow instructions → upload
  signed PDF and provider record → wait for checks.
- **Primary action:** the next numbered step.
- **Expectation:** provider choice and commercial work remain outside the core
  lifecycle; Odoo never implies that opening or uploading completed the work.
- **Release check:** modified revision, wrong signer, untrusted chain,
  insufficient level and unavailable validation all end in a clear recoverable
  state without silent downgrade.

### 10. Retrieve and review completed proof

- **Persona:** requester, auditor or evidence reviewer.
- **Goal:** retrieve the signed PDF and quickly know whether its proof and final
  storage can be trusted.
- **Ideal path:** Completed → Final document → download signed PDF, completion
  certificate or complete record → optionally inspect verification and daily
  timestamp details.
- **Primary action:** **Download signed PDF**.
- **Expectation:** achieved method, completion time, validation, storage and
  timestamp are summarized in plain language. Paperless opens only for users
  whose personal identity and document permission are synchronized; the Odoo
  copy remains available to authorized users.
- **Release check:** only fully validated and archived requests appear as
  completed. Alteration detection, checksum-identical archive retry, timestamp
  confirmation and multi-company evidence access are covered.

## Release evidence

The automated and manual results for these journeys belong in
`docs/operations/sign-validation-report.md`. A journey is not accepted from a
mockup or code review alone: its relevant model, access, failure-state and UI
tests must pass, and any remaining real-device or external-provider limitation
must be stated explicitly.

## Deliberate release scope

The workflow follows Odoo 19 Sign's document-first model and the established
Adobe Acrobat Sign/Docusign patterns for multiple recipients, ordered or
parallel signing, reusable templates, reminders, expiration, status tracking,
decline and final-file retrieval. The final send step uses an explicit
check-before-you-send summary, and validation errors retain Odoo's field-level
feedback instead of hiding them behind a generic failure.

This release accepts signers from Odoo contacts that already have an email
address. Arbitrary email-only recipients, copy-only recipients, scheduled send
and post-send recipient replacement are deliberately outside the first release;
they are not represented by partial models, hidden controls or unsupported
claims. A replacement request remains available for a rejected imported result.
These narrower boundaries keep contact identity, company access and evidence
ownership unambiguous while preserving the common one-person and multi-person
signature journeys.

The interaction review uses the following maintained references rather than
product folklore:

- [Odoo 19 Sign: request signatures](https://www.odoo.com/documentation/19.0/applications/productivity/sign/request_signatures.html)
  for the native document, recipient, order, validity and reminder flow;
- [Adobe Acrobat Sign: request signatures](https://helpx.adobe.com/sign/using/sending/request-signatures-from-others.html)
  and [Docusign reminders and expiration](https://www.docusign.com/blog/developers/default-api-reminder-and-expiration-settings)
  for mature multi-recipient and lifecycle patterns;
- [GOV.UK check answers](https://design-system.service.gov.uk/patterns/check-answers/)
  and [error summary](https://design-system.service.gov.uk/components/error-summary/)
  for pre-send error prevention and recoverable validation;
- [WCAG 2.2](https://www.w3.org/TR/WCAG22/) for keyboard operation, focus,
  reflow and target-size acceptance.
