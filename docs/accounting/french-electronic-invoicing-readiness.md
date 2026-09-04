# French Electronic-Invoice Reception Readiness

## Product contract

French VAT-registered businesses must be able to receive electronic invoices
through an approved platform from **1 September 2026**. The official calendar
and approved-platform requirement are maintained by
[impots.gouv.fr](https://www.impots.gouv.fr/facturation-electronique-et-plateformes-agreees).
Odoo documents its native French Approved Platform workflow in the
[France localization guide](https://www.odoo.com/documentation/19.0/fr/applications/finance/fiscal_localizations/france.html).

USL's product is implemented and validated offline, but intentionally
disconnected until production activation. Generic Peppol registration from
the Online source is not proof of French Approved Platform registration.

## Architecture decision

Three approaches were compared:

1. Odoo's maintained `account_edi_ubl_cii`, `account_peppol`,
   `account_peppol_response` and `l10n_fr_pdp` modules;
2. OCA UBL import without a French approved-platform transport;
3. a bespoke USL transport, directory and decoder.

The product uses option 1. OCA import alone does not cover French directory,
registration and lifecycle behavior. A bespoke transport would duplicate
regulated security-sensitive machinery. The USL layer supplies readiness,
company-scoped safety, evidence, permissions and an operational inbox; it is
not a parallel invoicing engine and adds no Odoo core patch.

Stable `rebuild.*` model names, tables and XML IDs remain in
`rebuild_account_migration` for installed-database compatibility. New behavior
must not depend on reconstruction provenance.

## Business states and actions

**Accounting > Configuration > Invoicing > Electronic Invoicing** and
**Settings > Users & Companies > Electronic Invoicing** open the same
company-scoped readiness workspace. The Settings shortcut is visible to
Accounting managers; the native **Companies** menu remains the place for
ordinary company details such as address, email and branding. The readiness
workspace exposes one
state and the action relevant to it:

| State | Meaning | Primary action |
|---|---|---|
| Needs setup | Identity, journal or contact is incomplete | Complete setup |
| Ready to test | Business setup is complete | Run self-check |
| Ready for production | Decoder and current configuration are validated | Prepare production activation |
| Activation required | Registration or local reception startup requires a deliberate action | Activate / Start receiving |
| Registration in progress | Odoo's Approved Platform is registering the company in the French directory | Wait for native receiver status |
| Receiving | This company accepts automatic incoming checks | Check now / Pause |
| Needs attention | Connection or received document needs action | Review issue |

Raw endpoints, schemes, proxy state, platform references and poll diagnostics
are technical-administrator details. E-reporting is shown separately as
**Not enabled — separate 2027 rollout**.

## Non-polluting self-check

The self-check:

1. derives a representative UBL buyer from the selected company;
2. runs the complete native decoder inside a database savepoint;
3. validates supplier, draft bill, currency, two lines, taxes and original
   attachment;
4. deliberately rolls back every generated operational record;
5. stores only pass/fail time, a configuration fingerprint and concise result.

Changing identity, journal, contact, fiscal country or self-check version
invalidates the result. Repeated self-checks leave move, partner, attachment
and reception counts unchanged. Older untouched €175 synthetic test bills are
removed on upgrade; modified ones remain for explicit manual review.

## Incoming documents and responses

UBL, CII and Factur-X invoices and credit notes become native draft vendor
bills/refunds. Their original document remains attached to the bill. The
business inbox is **Vendors > Incoming E-Invoices**; successful rows open the
bill. Vendor Bills has a **Received Electronically** filter and optional source
status.

Native posting sends the Approved Platform approval response. Native
cancellation opens the refusal dialog and requires a reason code plus note.
Payment and reconciliation remain ordinary Accounting operations.

Duplicate messages and payloads never create a second bill. Malformed or
retryable documents preserve their original and expose a bounded, idempotent
retry. Technical details are restricted to technical administrators.

## Safety and upgrade contract

Every non-production environment uses:

```text
USL_EINVOICE_LIVE_ENABLED=0
USL_EREPORTING_LIVE_ENABLED=0
```

The reception guard covers registration, lookup, fetch, approval/refusal
responses and manual checks. The separate e-reporting guard covers payment
lifecycle and Flow 10 behavior. Reception activation never enables
e-reporting.

Reception enablement is stored per company. Shared native schedulers may remain
active, but process only approved, connected and enabled companies. Module data
does not force schedulers inactive on every upgrade, so a valid production
connection is preserved. Installation and reconstructed targets remain
inactive and cannot gain external traffic from module installation alone.

## Reconstruction mapping

The canonical import preserves safe business configuration from the source:
accounting contact email/phone and mapped purchase journal. For French
companies it derives scheme `0225` and the SIREN. It does not copy proxy users,
keys, tokens, KYC state, registration approval, receiver claims, pilot mode or
e-reporting. Source generic-Peppol state is external migration evidence only.

Validation evidence is recorded in
[French electronic-invoicing validation](french-electronic-invoicing-validation.md).
