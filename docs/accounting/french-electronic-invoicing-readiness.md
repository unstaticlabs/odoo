# French Electronic-Invoice Reception Readiness

## Product contract

French VAT-registered businesses must be able to receive regulated electronic
invoices from **1 September 2026**. The official calendar is maintained by
[impots.gouv.fr](https://www.impots.gouv.fr/professionnel/questions/partir-de-quand-suis-je-concerne-par-la-reforme-de-la-facturation).
Reception requires a French approved platform and a structured UBL, CII or
Factur-X document; an ordinary emailed PDF is not a regulated electronic
invoice.

This release is **ready but inactive**. It can prove the complete software
reception journey without a network call. It does not prove that USL is
commercially eligible for Odoo's approved-platform service, registered in the
French directory, or connected in production.

## Architecture decision

Three credible approaches were evaluated:

1. use Odoo's maintained `account_edi_ubl_cii`, `account_peppol`,
   `account_peppol_response` and `l10n_fr_pdp` modules;
2. use OCA `account_invoice_import_ubl`;
3. build a USL directory, approved-platform transport and decoder.

The first approach is implemented behind an isolated USL safety and evidence
layer. It reuses Odoo's French approved-platform adapter and native vendor-bill
decoder. OCA's module is maintained and useful for importing UBL files, but it
does not supply the French approved-platform transport, directory registration
or lifecycle needed for mandatory reception. A bespoke transport would
duplicate regulated, security-sensitive machinery and create a larger
maintenance and certification burden.

The only compatibility extension needed is isolated in
`rebuild_account_migration`: Odoo's provider reception method assumes an XML
attachment even when the French adapter identifies a delivery as Factur-X.
The extension extracts the embedded CII/UBL document for native decoding while
retaining the original Factur-X PDF as immutable reception evidence. Odoo core
is unchanged.

## User-facing states

**Accounting > Configuration > Electronic-Invoice Readiness** presents only
product states:

- **Configuration incomplete** — one or more company identifiers, journal or
  contact prerequisites are missing;
- **Not yet verified** — configuration may be present, but the representative
  offline reception test or provider eligibility verification is outstanding;
- **Test passed** — the current company produced a correct native draft bill
  from the maintained safe fixture;
- **Ready but inactive** — company configuration, safe test and provider
  eligibility decision are complete, but no live connection is active;
- **Production activation required** — a production deployment is prepared
  but still needs deliberate approval, registration or scheduled-reception
  enablement;
- **Active** — production approval, connected receiver state and reception-only
  jobs are all present.

The screen never interprets installed code as proof of a provider contract or
live connectivity.

## Reception and evidence

UBL invoices, UBL credit notes, CII and Factur-X enter Odoo's native import
framework and create draft `account.move` vendor bills or refunds. Multiple VAT
rates and document currencies are preserved. The bill then follows ordinary
review, posting, payment and reconciliation.

Each delivery records company-scoped evidence containing the original file,
platform message reference, document fingerprint, structured format, invoice
or credit-note type, processing attempts, recovery guidance and related native
bill. The Accounting Manager sees friendly results:

- **Draft Bill Created** — review and process the native draft;
- **Duplicate Controlled** — the same message or payload did not create a
  second bill;
- **Action Required** — the original is retained and can be retried up to five
  times after correction;
- **Rejected by Platform** — retain the provider result and investigate before
  creating any replacement.

Technical exception text is restricted to technical administrators. Read-only
accountants can inspect company-authorized evidence and the resulting bill but
cannot test, retry, post or activate.

## External-call boundary

All Compose services receive these explicit defaults:

```text
USL_EINVOICE_LIVE_ENABLED=0
USL_EREPORTING_LIVE_ENABLED=0
```

With the reception guard off, live provider calls, French directory lookup,
Peppol lookup, registration, deregistration and authentication refresh are
blocked. Installation and every module upgrade also disable reception,
auto-registration, regulatory-document, lifecycle and e-reporting jobs.

Production reception requires all of the following:

1. French company identifiers, scheme `0225`, contact and purchase journal;
2. the representative offline test marked **Test passed**;
3. verified provider eligibility, subscription and terms;
4. an actual production deployment;
5. `USL_EINVOICE_LIVE_ENABLED=1` in that deployment;
6. Accounting Manager approval;
7. native approved-platform receiver registration;
8. a separate **Enable Scheduled Reception** action.

Only four reception jobs are enabled. Auto-registration and all e-reporting or
regulatory-flow jobs remain disabled. `USL_EREPORTING_LIVE_ENABLED` is a
separate future rollout and must remain `0` for reception activation.

## Verified boundary

Durable backend and browser coverage is listed in
[French electronic-invoicing validation](french-electronic-invoicing-validation.md).
No test registers USL, contacts a live directory/provider, retrieves a real
invoice, sends an invoice, or submits e-reporting.
