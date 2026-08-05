# Native Sign

## Product boundary

Sign is an Odoo-owned business application. Odoo remains authoritative for
templates, positioned fields, signer roles, policies, requests, business
links, lifecycle, evidence and retention. A trust provider performs the
identity, authentication and cryptographic signing ceremony. Provider
identifiers are operational details attached to an Odoo request; they are not
the product's business identity.

Yousign, operated by Youtrust, is the first provider. Provider-neutral request
and policy fields deliberately avoid Yousign product names so another
eIDAS-capable provider can implement the same contract without replacing
historical records or user journeys.

## Foundation decision

The Distribution pins OCA `sign_oca` at
`3b768318bc5eaccb79535337478f49d59d17d0b1` as the Community-native PDF editor
and portal foundation. It already supplies useful OWL/PDF.js field placement,
role assignment, request generation and portal-token concepts. Reusing it is
safer and more maintainable than a second PDF coordinate editor.

`sign_oca` alone is not the delivered trust product. Its native flow writes
drawn values into the PDF after each signer, has only Draft, Sent, Signed and
Cancelled states, and explicitly leaves cryptographic inalterability and OTP
as future work. Its roles and permissions also do not distinguish template
management, provider administration and evidence review. `usl_sign` therefore
owns the production lifecycle, assurance, evidence, permissions, provider
adapter and user-facing navigation while extending the useful OCA editor.

The rejected alternative was a fully independent `usl_sign` editor and model
stack. It would avoid adapting OCA semantics, but duplicate complex PDF.js,
portal and positioned-field behavior and create a second Community signing
ecosystem to maintain. The selected extension boundary preserves the strongest
existing work while keeping provider and legal semantics in Distribution-owned
code.

## Assurance language

- **Standard** requests a simple electronic signature. OTP is used when the
  selected policy requires it.
- **Verified** requests an advanced electronic signature and the identity or
  authentication controls required by the policy and provider.
- **Qualified** requests a qualified electronic signature. Under eIDAS this is
  the level intentionally selected when handwritten-signature equivalence is
  required.

Requested and achieved assurance are stored and displayed separately. A
provider audit trail or completion certificate is evidence of the ceremony;
it is not described as proof that every signature is qualified or that the
underlying agreement is legally valid. Odoo derives achieved assurance and the
authentication method from every preserved per-signer provider audit trail. An
unknown or missing level leaves the request in Action required rather than
copying the requested level into the achieved result.

## Audited source perimeter

The preserved Odoo Online source contains 11 Sign templates, 8 requests, 99
template items, 87 request values, 53 logs and 50 attachments classified as
signing evidence. Historical completed requests must be reconstructed as
read-only evidence. They are never re-signed, and no assurance level is
inferred when the preserved evidence does not prove it.

One-shot extraction and source mappings belong under `migration/`; no source
identifier or reconstruction model enters the delivered registry.

## Delivered application

The top-level **Sign** application exposes Templates, Requests, My Signatures,
Completed and Configuration. A template manager prepares PDF templates with
signature, initials, free text, signer name, date, checkbox, company and role
fields. Each field belongs to a signer role and uses top-left PDF coordinates;
rotated pages are normalized before provider payloads are built. A used
template is versioned when its document, layout, policy or timing changes.

A request freezes its source PDF, SHA-256, template version, field layout,
signers and assurance policy before provider submission. Its normal lifecycle
is Draft → Ready → Sent → Viewed/Partially signed → Completed. Declined,
Expired and Cancelled are terminal. Provider or evidence discrepancies enter
Action required and retain a recovery instruction instead of falsely reaching
Completed. A provider “done” event is insufficient: the final PDF and every
expected signer audit trail must be retrieved, hashed and stored first.

Standard and Verified ceremonies are embedded on the Odoo signer page.
Qualified ceremonies use an explicit handoff page because the provider must
control its identity journey. Single-role Standard templates may expose a
reusable public link. The link collects a fresh name, email, optional mobile
and consent for each submission, applies a hashed per-source rate limit, and
queues provider creation after the HTTP transaction commits. It never reveals
another signer’s data or reuses their request.

Odoo owns invitation and reminder cadence. Due reminders target only currently
eligible signers, respect signer order, and stop at the policy/template cap.
Expiry, decline and cancellation create immutable JSON evidence. Completed
PDF delivery to signers is a per-company opt-in; portal users may otherwise
download only completed documents assigned to their commercial contact.
Completion also freezes the configured evidence-retention horizon on the
request. Indefinite retention and legal holds are explicit states; evidence is
not deleted by an unattended job.

Templates linked to a business model participate in the standard OCA action
menu. Contacts additionally show their request count and current state. The
request chatter records business milestones without exposing provider secrets.

## Permissions and company isolation

- **Sign User** creates and operates requests they own and signs requests
  assigned to their contact.
- **Template Manager** additionally manages templates and their layouts.
- **Evidence Reviewer** reads company requests, immutable evidence and
  provider-event diagnostics but cannot change ceremonies.
- **Sign Administrator** manages assurance policies, provider configuration
  and recovery actions.

The administrator uses Sign → Configuration → Settings for the active allowed
company. This narrowly scoped screen can change only Sign settings; it does not
grant access to general system settings or expose server-side secrets.

Company record rules apply to templates, policies, requests, signers,
provider events, public submissions and evidence. Provider API credentials and
webhook secrets are environment variables; they are never readable Odoo
fields. Public and portal routes use opaque tokens, return generic unavailable
states, and never accept a provider transaction identifier as authorization.

## Historical Odoo Online records

The temporary `migration/sign_restore` service reads the source database in a
read-only transaction and verifies every filestore object against its stored
SHA-1 and size. It maps source templates to the native editor and stores each
original PDF, signed PDF, completion certificate and privacy-reduced audit log
as a separate immutable evidence object. Two source pairs share the same
company/name/PDF fingerprint, so 11 source template links intentionally
converge on 9 native templates; the 8 completed requests and 11 signers remain
distinct.

Historical requests use the `odoo_online` provider code, are read-only and
cannot be resent. Their requested process class is retained as Standard while
achieved assurance and authentication method remain empty and
`migration_assurance_unproven` remains true. Finalization uninstalls the
temporary module and proves that request/evidence counts and document hashes
did not change.

## Known limitations

The first live adapter is Yousign API v3; additional providers require a new
adapter implementing the existing service contract. The application does not
independently validate an eIDAS certificate chain: provider audit evidence and
the achieved level reported by the provider remain distinct from Odoo’s PDF
readability check. PDF forms outside the supported field catalogue, corrupt or
encrypted PDFs, ambiguous historical contacts, and malformed historical
coordinates require manual review. Reusable links are intentionally restricted
to one-role Standard templates.
