# Settle an immediate foreign-currency payment

Use **Settle** when a foreign-currency invoice or bill was converted by the
bank immediately and the suggested payment shows both the exact foreign
amount and the actual amount charged or received in company currency.

1. Open the posted invoice or bill.
2. Find the payment under **Outstanding Credits** or **Outstanding Debits**.
3. Review the payment identity and amount.
4. Choose **Settle** to match it at the bank's executed rate.

**Add** always remains available. Choose **Add** for the normal Odoo
reconciliation treatment, especially when a foreign receivable or payable was
outstanding over time. Standard Odoo accounting may then create an exchange
gain or loss.

Odoo shows **Settle** only when the amounts, currencies, direction, dates,
rate, accounting period, permissions, and document structure pass the
company's policy. If **Standard FX only** appears, its helper explains why
**Settle** is unavailable. Do not work around an amount discrepancy by
changing the payment; review the bank transaction and any separate fee first.

After success, the payment history shows **Settled at payment rate**. Open its
information popover to inspect the executed currency pair, executed and
reference rates, and source. Accountants can open the linked Immediate
Settlement record for the complete adjustment and allocation audit.

To undo the match, use Odoo's unreconciliation action. Odoo reverses the
linked settlement adjustment and restores the document and payment residuals.
Do not try to edit or cancel the adjustment journal entry directly.
