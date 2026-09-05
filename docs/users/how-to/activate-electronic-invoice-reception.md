# Activate, Monitor or Pause Reception in Production

Use this guide only on the deployed production Accounting system during an
approved change window. Development, QA, reconstructed and copied databases
must remain disconnected.

## Before activation

Confirm that:

- **Accounting > Configuration > Invoicing > Electronic Invoicing** shows
  **Ready for production**;
- the VAT number, SIREN/SIRET, incoming purchase journal and accounting contact
  have been reviewed;
- the self-check is current;
- the legal representative and Accounting Manager are available;
- a recoverable database and filestore backup exists;
- one supplier can send a controlled first invoice.

## Activate reception

1. Select **Activate reception**.
2. Review the identity, journal and contact shown by Odoo.
3. Confirm the Accounting Manager approval.
4. The legal representative completes the native Odoo Approved Platform
   authentication and accepts the displayed terms.
5. Complete the native receiver registration.
6. While Odoo reports `smp_registration`, the readiness workspace shows
   **Registration in progress**. Wait for the native receiver state; do not
   restart registration merely to change the badge.
7. When the workspace returns to **Activation required**, select
   **Start receiving invoices**.
8. Confirm the company reaches **Receiving**.

This journey activates incoming invoices only. It does not enable e-reporting,
payment-lifecycle reporting or outgoing regulatory flows.

## Validate the first invoice

1. Open **Accounting > Vendors > Incoming E-Invoices**.
2. Confirm exactly one **Ready for Review** item for the agreed supplier
   invoice.
3. Open the native draft bill and compare supplier, reference, dates, currency,
   lines, VAT and total.
4. Confirm the original UBL, CII or Factur-X document is available in the
   normal attachments.
5. Select **Check now** and confirm a duplicate bill is not created.
6. Post, pay and reconcile the bill normally.
7. Record the bill and successful check in the production change record.

## Monitor and recover

- **Needs Attention** opens the affected reception items.
- A temporary connection failure preserves all documents; select **Check now**
  after recovery.
- A malformed document remains available for investigation and controlled
  retry.
- A duplicate links back to the original bill and never creates a second
  liability.

## Pause safely

Select **Pause incoming invoices** on the company. New automatic checks stop
for that company; existing bills, attachments and reception history remain
available. Coordinate provider deregistration separately—do not use it as a
routine pause.

Maintainers should follow the repository production operations runbook for
deployment guards, incident handling and rollback.
