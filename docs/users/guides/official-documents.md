# Generate official documents

## Before the first document

An administrator opens **Settings > Document Templates** for each company and
checks **Legal identity ready** and **Renderer healthy**. Complete the native
company name, logo, address, registry, VAT and APE values, then the governed
legal form, share capital and RCS city. French invoicing also requires the
late-payment penalty wording and recovery fee.

For French invoices issued from 1 September 2026, complete the customer's
SIREN and any delivery address that differs from the billing address. Classify
invoice lines as goods or services through their products and taxes; the PDF
then carries the required transaction nature and, where configured, the
VAT-on-debits mention.

If readiness or renderer health is missing, Odoo stops the new generation and
opens the relevant Settings area. It never produces a visually different
fallback document.

## Invoices and accounting reports

Use the normal Odoo actions. Customer invoice, credit-note and pro-forma
preview/send/download actions produce the governed invoice layout while
retaining normal filenames, mail attachments, portal delivery and Factur-X
processing. Vendor originals and imported source PDFs remain unchanged.

Accounting PDF exports use the exact rows, visible hierarchy, filters,
comparison, display unit and rounding of the current report session. XLSX and
FEC remain their native machine-oriented formats.

## Official correspondence

Open **Official Documents > Correspondence**, create a draft, and select its
company before entering the recipient. The printable body accepts paragraphs,
headings, ordered or bulleted lists and simple tables. Add only the names of
attachments that must appear in the printed list.

**Finalize** snapshots the sender and recipient identity, body, signatory and
attachment list and creates the immutable official PDF. Use **Mark sent** after
delivery. To correct finalized or sent correspondence, choose **Create
correction**; edit and finalize the new superseding version. The original
version remains available and cannot be silently rewritten.

## Existing documents during an outage

Already generated immutable attachments remain downloadable subject to normal
Odoo permissions. Contact an administrator when a new render reports a health,
revision, certificate or legal-identity problem; do not recreate the document
outside the governed workflow.
