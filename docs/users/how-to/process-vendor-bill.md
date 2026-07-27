# Process a Supplier Bill

1. Open **Vendors > Bills** and select **New**, or upload the bill from the
   vendor document area. The removable **Bills** chip keeps supplier receipts
   out of the normal bill-processing queue.
2. Confirm supplier, invoice date, reference, payment terms and currency.
3. Review every business line: description, expense or asset account, taxes and analytic distribution.
4. Attach the supplier invoice as primary evidence.
5. Review **Suggested existing payments** below the totals. Existing payments
   and close unreconciled bank transactions are ranked by reference, amount,
   currency, date and partner evidence. A partner inferred from bank evidence
   remains useful even when it was not reliable enough for automatic
   assignment. A close amount/date candidate may also appear with a missing or
   different partner; the suggestion states the evidence and the change that
   matching would make. Draft suggestions are informational: post the bill
   before matching.
6. Select **Confirm/Post** and review the generated journal items. If the
   highest-ranked payment is correct, select **Add**. For an uncategorized bank
   transaction, **Match bank transaction** moves its suspense counterpart to
   the bill payable account. **Match & reassign** also replaces a missing or
   different bank partner with the bill supplier. Both actions then use native
   reconciliation and record the amount, date and partner evidence in chatter.
   Otherwise register a new payment when payment is initiated or recorded.
7. Open the relevant bank journal and choose **Bank Matching**.
8. Select the bank transaction. The **Reconcile** tab starts with removable **Closest amount** and **Closest date** filters.
9. Match the payment or open bill. If an amount remains, confirm whether the result is a legitimate partial payment, a fee, an exchange difference or an error.
10. Open **Grand livre**, Compte de résultat and VAT for the period to inspect the result.

Use **Manual Operation** only when no existing document, payment or journal item is the correct counterpart—for example a bank fee, transfer or direct account category.

On a posted bill, **Pay** opens payment registration; it does not initiate a
bank transfer. **Credit Note** creates a linked draft supplier refund. Review
and post that refund separately—the original posted bill is preserved.
