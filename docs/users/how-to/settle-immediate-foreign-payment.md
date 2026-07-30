# Match a foreign bill to a company-currency bank transaction

When the bank reports only an EUR card debit, Odoo estimates its foreign
amount. The invoice payment row can offer three actions:

| Action | Use it when | Result |
| --- | --- | --- |
| **Add** | You want Odoo's existing suggestion | Native behavior; the estimated foreign difference may remain |
| **Settle** | The supplier received the exact invoice amount | Uses the invoice residual and records Odoo's normal EUR exchange gain or loss |
| **Use payment rate** | Purchase and card conversion were one immediate event | Uses the invoice residual and bank EUR amount; adjusts safe expense or revenue accounts and records no FX |

Example:

```text
Bank €4.40 · Invoice $5.00 · Odoo estimate $5.03
Recommended: Use payment rate · no FX
```

All available actions remain on the same compact row. The recommended action
is highlighted. Hover or focus an action to see its accounting consequence.

## Choose the action

- Choose **Add** to accept Odoo's `$5.03` candidate unchanged.
- Choose **Settle** when the invoice was `$5.00`, including a delayed payment.
  The foreign balance closes at `$5.00`; Odoo may record the difference
  between the invoice's EUR carrying value and the bank debit.
- Choose **Use payment rate** for a same-day or nearby card settlement when the
  displayed policy checks pass. The invoice closes at `$5.00`, the bank stays
  exactly `€4.40`, and the safe non-tax economic lines receive the EUR
  difference.

There is no confirmation window. Odoo checks the amounts, dates, rate,
permissions, locks, and accounting structure again when you click.

## When fewer actions appear

**Settle** remains available for delayed or unusual-rate transactions but its
helper asks you to check the warning. It disappears when Add already contains
the exact authoritative foreign amount.

**Use payment rate** is shown only inside the configured immediate-event date
and rate policy and for simple, safely adjustable economic lines. Documents
involving stock valuation, fixed assets, deferrals, mixed-sign allocations,
fees, withholding, or other ambiguous facts keep Add and, where safe, Settle.

A small **Review** indicator appears only when the source facts are conflicting
or ambiguous. Its tooltip explains what to review in Bank Matching.

## Check or undo the result

The payment history shows one trace:

```text
Settled · $5.00 · €0.02 FX loss
Payment rate · $5.00 · €4.40 · no FX
```

Open the information icon for the bank amount, document-derived foreign
amount, discarded estimate, carrying value, rates, provenance, native FX, or
economic allocations.

Use the normal **Unreconcile** action, or **Reverse Settlement** on the audit
record, to undo the whole linked settlement. Odoo restores the open document,
bank suspense, and original bank foreign-amount state.
