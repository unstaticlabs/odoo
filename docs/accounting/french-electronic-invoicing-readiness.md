# French Electronic-Invoicing Reception Readiness

## Product contract

All French VAT-registered businesses must be able to receive electronic
invoices from **1 September 2026**. Small and micro-enterprises begin mandatory
electronic issuance and e-reporting on **1 September 2027**. The official
calendar is maintained by
[impots.gouv.fr](https://www.impots.gouv.fr/professionnel/questions/partir-de-quand-suis-je-concerne-par-la-reforme-de-la-facturation).

Compliant domestic electronic invoices use UBL, CII or a mixed structured
format and pass through a French approved platform. A scanned or ordinary PDF
sent by email is not compliant. See the
[DGFiP overview](https://www.impots.gouv.fr/professionnel/je-decouvre-la-facturation-electronique)
and
[approved-platform responsibilities](https://www.impots.gouv.fr/facturation-electronique-et-plateformes-agreees).

## Architecture decision

Two approaches were evaluated:

1. build a USL transport, directory client and invoice decoder;
2. govern Odoo's maintained SaaS 19.2 modules:
   `account_edi_ubl_cii`, `account_peppol`, `account_peppol_response` and
   `l10n_fr_pdp`.

The second approach is implemented. Odoo remains responsible for
approved-platform transport, French UBL/CII/Factur-X formats, native
vendor-bill creation, lifecycle status and invoice attachments. The isolated
USL module adds the production activation boundary, company readiness,
reception evidence and duplicate/technical-failure governance.

## Runtime states

**Configuration > Accounting Framework > Electronic Invoicing** separates:

- **Implemented and Validated**: the native modules and USL reception contract
  are installed;
- **Ready for Production Activation**: identifiers, contact, purchase journal,
  native provider adapter, production marker and manager approval are complete;
- **Not Connected**: no live participant, directory registration or endpoint
  exists.

Capability never implies a live connection.

Every payload creates immutable `rebuild.einvoice.reception` evidence with its
company, provider message ID, SHA-256, original attachment, processing result
and related vendor bill. Repeated message IDs or payload hashes are
acknowledged without a second bill. Malformed payloads are **Technical
Failure**; provider error deliveries are **Rejected by Provider**. Accounting
problems remain on the ordinary draft bill.

## Activation safety

Installation and module updates force Peppol/PDP retrieval, status, lifecycle,
webhook and e-reporting jobs inactive. Native French registration is blocked
until:

1. the database is the deployed production Accounting system;
2. the Odoo Approved Platform adapter is selected, or another adapter has been
   implemented and validated;
3. identifiers, contact and reception journal are complete;
4. an Accounting Manager records production activation approval.

Registration still does not enable scheduled exchange. After the provider
reports the company as a receiver, a manager must enable it explicitly.
Because the Odoo jobs are database-wide, every connected company must be
production-approved first.

Selecting **another approved platform** remains visible but blocks activation
until its maintained adapter has behavioral evidence. This prevents a generic
Peppol setting being presented as French reform readiness.

## Offline validation

The permanent module test generates a representative French UBL 2.1 invoice,
imports it through Odoo's native reception method, verifies the draft bill and
structured attachment, posts the bill, and verifies deduplication, malformed
XML, provider rejection and the activation boundary.

No remote request, production identifier, directory registration, supplier
invoice retrieval, sending or e-reporting occurs.
