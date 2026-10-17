# Company-paid expense bank matching

Status: accepted product decision
Owner: `usl_accounting`

## Outcome

An Accounting Manager can start from an expense, find a likely company bank
debit and complete the native company-paid expense and bank-reconciliation
flow without manually reproducing the same search in Bank Matching.

The feature saves review time; it does not create a second accounting engine.
The native expense, payment, journal entry, bank statement line and
partial/full reconciliation records remain the accounting truth.

## Selected design

**Find bank transactions** searches unreconciled debit statement lines in the
same company and within ten days of the expense. It accepts the same currency
or a date-effective conversion, applies a narrow amount tolerance and retains
at most five deterministically ranked suggestions. The UI explains concrete
facts—exact amount, date distance, vendor, reference and competing
expenses—instead of exposing an unexplained confidence score.

A near amount is evidence for investigation but cannot be selected. **Use**
requires an exact amount within currency rounding and an explicit confirmation.
It then:

1. sets **Paid by** to **Company** and selects a supported outbound method on
   the bank journal;
2. discloses and applies the bank partner only when that transaction has one;
3. calls Odoo's native submit, approve and post methods as the current user;
4. selects the one outstanding line belonging to the resulting native payment;
5. adds that line and reconciles through the pinned OCA bank-matching API;
6. verifies the payment and reconciliation before recording audit messages.

Odoo's duplicate review, analytic validation, lock dates, approval rules and
permissions are authoritative. If one interrupts the flow, the request rolls
back: it must not leave a changed payment mode, vendor, payment, entry or
partial reconciliation.

Draft, Submitted and Approved expenses are eligible. An already posted
employee-paid expense is not silently converted; the user must correct it
through Odoo's normal accounting workflow.

## Review when posting

Posting an approved expense now checks the same current company-bank evidence,
even if **Find bank transactions** has never been selected. When a possible
match exists, the posting operator must explicitly choose what to do:

- **Review expenses** returns to the selected expenses. Use the existing
  **Company payment match** panel to select and confirm an exact transaction.
- **Post without matching** keeps the existing **Paid by** setting and uses
  native Odoo posting. It neither selects nor reconciles a bank transaction.
- **Cancel** leaves the expenses and accounting unchanged.

The same confirmation applies to people and delegated Agents. It is a decision
by the current authorized operator, not a request for another approver. Existing
native permissions, delegation and irreversible-action controls still apply.
Agents can read `usl_review_expense_ids`, `usl_review_matches` (readable expense
and bank transaction IDs, exactness and evidence) and `usl_review_evidence` on
the returned native posting wizard, then call
`action_confirm_bank_match_posting` to make the same explicit decision.

Near amounts remain possible matches for review, although only an exact amount
can use the existing reconciliation action. A bulk post reviews all selected
expenses before posting any of them. If the expense or matching bank evidence
changes while the dialog is open, the dialog refreshes and asks for confirmation
again. Repeating a completed posting cannot create another expense entry.
The final employee-paid posting step also checks newly arrived bank data.

Opening a review only creates a temporary native wizard. It does not create
candidate-cache records, change the expense, create a payment, post an entry or
reconcile a transaction. No-match posting retains the normal Odoo journey.
No existing business records are migrated by the module upgrade; the new fields
belong only to Odoo's temporary posting wizard.

## Access and history

- Accounting Managers can refresh and use suggestions.
- Scoped read-only accountants can inspect the evidence and accepted history.
- Ordinary employees cannot access candidate bank evidence.
- Candidate rows are company-scoped and recomputable.
- A successful match invalidates other suggestions for the same transaction.
- No cron posts, approves or reconciles expenses.

## Alternatives considered

### Keep the Online bootstrap

The bootstrap used dynamic `x_*` models and fields, `sudo`, server actions,
swallowed validation errors, global journal-line guessing and direct mutation
of a suspense line. It provided a fast journey but could bypass authority,
select the wrong line and leave partial effects. It is rejected.

### Use only native expense and OCA Bank Matching screens

This is accounting-safe and remains the fallback for non-exact cases, but it
requires the user to find the same transaction twice and manually connect the
expense payment. Neither native Community nor the pinned OCA modules provide
the expense-originated search and one-click lifecycle on this baseline.

### Add a USL assistant over native/OCA APIs

This is selected. The custom part owns only deterministic candidate evidence,
confirmation and orchestration. Native Odoo and OCA continue to own every
business transition and accounting result.

## Migration contract

The former `x_sl_expense_bank_candidate` rows, expense `x_*` fields,
many-to-many cache, server actions, ACLs and view are excluded from the target.
After native expenses and statement lines are restored, current suggestions
are recomputed. Migration evidence classifies every source cache association
as reproducible, stale, already settled, shared or ambiguous and proves:

- all 360 source expenses and their native accounting parity remain unchanged;
- no legacy schema or UI survives;
- repeated refresh preserves candidate identity and creates no business
  consequence;
- source payments and reconciliations override stale suggestion data.

The clean `odoo_dev` reconstruction on 30 July 2026 classified all 42 source
cache associations: 36 were reproducible suggestions and 6 were already
settled native truth. It produced 13 current candidate rows, no refresh error
and no change to the 5,044 moves, 11,871 move lines, 110 payments, 2,584
partial reconciliations or 1,260 full reconciliations.
