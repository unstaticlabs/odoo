# Prepare Electronic-Invoice Reception

France requires VAT-registered businesses to be able to receive electronic
invoices through an approved platform from 1 September 2026. USL uses Odoo
Approved Platform for this workflow. Preparation and self-checks do not connect
the company to the live service.

## Complete the four setup steps

1. Open **Accounting > Configuration > Invoicing > Electronic Invoicing**.
2. Open the company.
3. Complete:
   - **Company identity**: France, VAT number and SIREN/SIRET;
   - **Incoming bills**: the purchase journal where received invoices become
     draft vendor bills;
   - **Accounting contact**: the email and phone used during onboarding;
   - **Reception self-check**: select **Run self-check**.

The self-check decodes a representative UBL invoice through the same native
import path used in production. It verifies the supplier, taxes, two invoice
lines, total and original document, then rolls the test transaction back. It
does not create a lasting bill, partner, attachment or reception item, and it
does not contact a supplier, directory or platform.

The result remains valid only for the tested setup. Changing the company
identity, purchase journal or accounting contact returns the company to
**Ready to test**.

## Read the status

- **Needs setup**: one of the four setup steps is incomplete.
- **Ready to test**: configuration is complete; run the self-check.
- **Ready for production**: software and setup are validated, but no live
  connection exists.
- **Activation required**: the production receiver exists or is ready to be
  registered, but reception is not enabled locally.
- **Registration in progress**: Odoo's Approved Platform is still registering
  the company in the French directory.
- **Receiving**: incoming-invoice checks are enabled for this company.
- **Needs attention**: a received document or connection check needs action.

The screen presents the appropriate action for the current state. E-reporting
is deliberately separate and remains **Not enabled — separate 2027 rollout**.

## What happens to an incoming invoice

UBL, CII and Factur-X invoices and credit notes enter the ordinary Vendor Bills
workflow as native drafts. The original structured document remains attached.
Posting, payment and reconciliation then use the normal Accounting actions.

Continue with [Review an incoming electronic invoice](review-incoming-electronic-invoice.md).
Production operators use the separate
[activation guide](activate-electronic-invoice-reception.md) during an approved
production change.
