# Review an Incoming Electronic Invoice

## Open work that needs attention

1. Open **Accounting > Vendors > Incoming E-Invoices**.
2. The removable **Needs Attention** filter shows rejected or unprocessed
   documents first. Remove it to see the complete reception history.
3. Use the plain-language result:
   - **Ready for Review**: open the vendor bill and verify it;
   - **Duplicate Ignored**: no second bill was created; open the linked
     original bill;
   - **Needs Attention**: correct the stated problem, then an Accounting
     Manager may select **Retry**;
   - **Rejected**: preserve the document and investigate the supplier or
     platform result before creating anything manually.

## Review and process a bill

1. Select **Open Vendor Bill**.
2. Compare supplier, reference, invoice date, currency, lines, VAT and total
   with the original document in the normal attachments.
3. Use the **Electronic invoice** smart button when you need the reception
   history.
4. If correct, post the bill. Native Odoo sends the Approved Platform approval
   response when the production connection is enabled.
5. Pay and reconcile it through the ordinary vendor-bill and Bank Matching
   workflow.

Vendor Bills also provides a removable **Received Electronically** filter and
an optional **Electronic invoice** status column.

## Refuse an incorrect invoice

Cancel the received draft and complete the native refusal dialog. A refusal
requires both a reason code and a plain-language note for the supplier. This is
an external response in production; it is not the same as deleting or silently
ignoring a bill.

Read-only accountants can inspect the bill, original document and reception
history. They cannot configure, retry, activate, pause, post or refuse.
