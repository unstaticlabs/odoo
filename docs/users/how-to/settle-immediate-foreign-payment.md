# Match a foreign bill to a company-currency bank transaction

When the bank reports only an EUR card debit, Odoo estimates its foreign
amount. The invoice payment row can offer three actions:

| Action | Use it when | Result |
| --- | --- | --- |
| **Add** | You want Odoo's existing suggestion | Native behavior; the estimated foreign difference may remain |
| **Settle** | The supplier received the exact invoice amount | Uses the invoice residual and records Odoo's normal EUR exchange gain or loss |
| **Use payment rate** | Purchase and card conversion were one immediate event | Revalues the complete document at the actual bank rate, then matches it with no FX |

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
  displayed policy checks pass. Odoo briefly resets the complete document to
  draft, values it at `$5.00 / €4.40`, reposts it, and reconciles it. The bank
  stays exactly `€4.40`, no exchange entry is created, and no technical
  adjustment line is added.

There is no confirmation window. Odoo checks the amounts, dates, rate,
permissions, locks, and accounting structure again when you click.

## When fewer actions appear

**Settle** remains available for delayed or unusual-rate transactions but its
helper asks you to check the warning. It disappears when Add already contains
the exact authoritative foreign amount.

**Use payment rate** is shown only inside the configured immediate-event date
and rate policy and for a complete, never-paid document that Odoo can legally
reset and repost. Documents with taxes, stock valuation, fixed assets,
deferrals, mixed-sign lines, fees, withholding, sent/active EDI records, secure
hashes, or other ambiguous facts keep Add and, where safe, Settle.

A small **Review** indicator explains why Use payment rate is unavailable or
what source fact needs attention. It does not add a permanent warning panel.

## Check or undo the result

The payment history shows one trace:

```text
Settled · $5.00 · €0.02 FX loss
Payment rate · $5.00 · €4.40 · no FX
```

Open the information icon for the bank amount, document-derived foreign
amount, discarded estimate, original and applied document rates, original and
repriced EUR values, provenance, or native FX.

Use the normal **Unreconcile** action, or **Reverse Settlement** on the audit
record, to undo the whole linked settlement. Odoo restores the original
document rate and EUR carrying value, the bank suspense, and the original
missing-foreign-amount state. If the original document period was locked after
settlement, Odoo blocks reversal normally.
