# Activate French Electronic-Invoice Reception

Use this runbook only after the SaaS 19.2 Accounting fork is the production
system and Valentin has approved the provider.

## Before activation

1. Open **Accounting > Configuration > Accounting Framework > Electronic
   Invoicing**.
2. Confirm **Reception Capability** is **Implemented and Validated**.
3. Confirm the VAT number, SIREN/SIRET and French scheme `0225` identifier.
4. Record the platform contact email and mobile number.
5. Select the purchase journal for received draft bills.
6. Select **Odoo Approved Platform (native integration)**. A different provider
   requires its maintained adapter and separate validation.
7. Confirm this is the deployed production system and set **Accounting
   Deployment** to **Production**.
8. Record the provider decision, support and rollback contacts in the
   deployment change record.
9. Select **Approve Production Activation**.

Do not perform these steps on a reconstructed, validation, development or
copied database.

## Register and validate reception

1. In Accounting settings, open native **Activate Electronic Invoicing**.
2. Complete the approved-platform identity and registration workflow.
3. Confirm the status reaches receiver/connected and the French directory start
   date is correct.
4. Select **Enable Scheduled Exchange** from the readiness record.
5. Arrange one controlled supplier invoice with a known supplier, reference,
   amount and tax.
6. Under **Review > Electronic Invoice Reception**, verify one **Draft Bill
   Created** record, the provider message ID, SHA-256, structured attachment and
   expected native draft bill.
7. Review and post the bill normally, then continue through payment and bank
   reconciliation.

## Diagnose

- **Technical Failure**: inspect the original attachment, processing summary
  and bill chatter. Correct transport, schema or mapping problems before
  posting.
- **Rejected by Provider**: retain the status and payload, then follow the
  platform lifecycle/support process.
- **Duplicate**: no second bill was created. Compare the linked reception,
  message ID and SHA-256 before treating it as a correction.
- **Registration Pending**: verify the platform account and French directory
  effective date; do not work around it with standalone Peppol registration.

## Suspend or roll back

1. Select **Suspend Scheduled Exchange** to disable all Peppol/PDP jobs.
2. Preserve received bills, payloads and reception evidence.
3. Use native disconnect only after the provider confirms directory and routing
   consequences.
4. Select **Revoke Approval and Suspend** to prevent re-registration.
5. Record the incident, last successful message and invoices needing follow-up.

Never delete reception evidence or substitute email PDFs for regulated
electronic invoices during an outage.
