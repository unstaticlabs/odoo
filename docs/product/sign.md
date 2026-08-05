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
underlying agreement is legally valid.

## Audited source perimeter

The preserved Odoo Online source contains 11 Sign templates, 8 requests, 99
template items, 87 request values, 53 logs and 50 attachments classified as
signing evidence. Historical completed requests must be reconstructed as
read-only evidence. They are never re-signed, and no assurance level is
inferred when the preserved evidence does not prove it.

One-shot extraction and source mappings belong under `migration/`; no source
identifier or reconstruction model enters the delivered registry.
