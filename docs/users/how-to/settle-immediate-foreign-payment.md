# Settle an exact foreign amount

Use **Settle** when a foreign-currency invoice or bill is suggested against a
company-currency bank transaction and Odoo estimated the foreign amount
because the bank did not report it.

Example:

```text
Cloudflare bill:          $5.00
Actual bank debit:        €4.40
Odoo estimated payment:   $5.03
```

1. Open the posted invoice or bill.
2. Find the bank transaction under **Outstanding Credits** or **Outstanding
   Debits**.
3. Check the exact document amount, actual bank amount, discarded Odoo
   estimate, and expected settlement gain or loss.
4. Select **Settle**.

Settle uses `$5.00` from the selected document, preserves the actual `€4.40`
bank debit, and reconciles normally. Odoo may record a legitimate
company-currency exchange gain or loss. It will not leave the synthetic
`$0.03` difference open.

**Add** remains unchanged and available beside Settle. Use Add whenever you
want the existing matching behavior or when the bank supplied authoritative
foreign-currency data.

Settle is absent when the match is ambiguous, outside company policy, already
allocated, locked, protected, or contains another amount such as a fee or
withholding. Review those cases in Bank Matching; do not force the amount.

After success, the payment history shows a trace such as:

```text
Settled · $5.00 · €0.02 FX loss
```

Open the information icon to inspect the bank amount, invoice-derived foreign
amount, discarded estimate, carrying value, exchange account, rates, dates,
and provenance.

To undo the settlement, use the normal **Unreconcile** action or open the Exact
Settlement audit record and choose **Reverse Settlement**. The bill and bank
suspense reopen, and the bank transaction returns to having no foreign amount.
