# Prepare Electronic-Invoice Reception

France requires every VAT-registered business to receive regulated electronic
invoices through an approved platform from 1 September 2026.

1. Open **Accounting > Configuration > Accounting Framework > Electronic
   Invoicing**.
2. Read the statuses separately:
   - **Implemented and Validated** means the software capability is present;
   - **Ready for Production Activation** means configuration and approval are
     complete;
   - **Not Connected** means no live service is active.
3. Complete the VAT number, SIREN/SIRET, French electronic-invoicing
   identifier, contact email, mobile number and incoming purchase journal.
4. Leave **Accounting Deployment** as **Development** and the provider
   unselected until the production system and approved platform are decided.
5. After activation, review incoming items under **Accounting > Review >
   Electronic Invoice Reception**.
6. Open **Draft Bill Created** items and review the native vendor bill before
   posting. The structured invoice and provider message remain as evidence.
7. Investigate **Technical Failure**, **Rejected by Provider** and **Duplicate**
   records; create another bill only when the evidence proves a separate
   accounting document is required.

Activation, diagnosis and suspension are documented in the
[production runbook](../../operations/activate-french-electronic-invoicing.md).
