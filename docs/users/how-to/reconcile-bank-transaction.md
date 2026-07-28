# Match a Bank Transaction

1. Open **Journals** and locate the bank journal.
2. If the transactions are not present yet, choose **Import Statement** on the journal card. Upload a CAMT, QIF, CSV or XLSX file, then choose **Import and View**. CSV/XLSX files use the mapping configured on that journal.
3. Return to the journal card and select **Bank Matching**.
4. Select a transaction from the queue.
5. Review the partner evidence. An exact bank account, exact declared counterparty
   name, or repeated unambiguous reconciled history can set the partner
   automatically. A less certain match remains a visible suggestion: choose
   **Use Partner** only after checking it. Existing partners and reconciled
   transactions are never overwritten.
6. In **Reconcile**, review the closest opposite-sign amounts first. The removable **Closest amount** and **Closest date** chips are defaults, not restrictions.
7. Choose an existing invoice, bill, payment or journal item when it is the true counterpart.
8. For a direct charge or receipt, deliberately choose a manual operation, then set partner, account, tax and label.
9. Check the remaining difference before confirming.
10. Choose the action that matches your decision:
    - **Complete Match** applies the prepared counterpart and reconciles the
      transaction.
    - **Reconcile & Review** applies the prepared match and reconciles the
      transaction, but keeps its entry flagged **To Review** for a later
      accounting check.
    - **Mark for Review** appears when the proposal is incomplete. It records
      the follow-up flag without changing the reconciliation.
11. After matching, keep the result visible. Confirm the matching-reference chip, Full or Partial status, and residual.
12. Use **Undo** if the counterpart is wrong. For a partial match, continue with the remaining residual later.

**Transactions** is the full-width bank history and investigation screen. **Bank Matching** is the operational queue. They are intentionally separate.

In Transactions, clicking a row opens the bank transaction and its generated
accounting entry together. Before matching, set or correct the **Partner**
directly when needed; an automatic suggestion is only a shortcut. Click
**Linked document or entry** to open the matched invoice, bill, refund or
journal entry. The compact list also retains **Open Entry** as a direct shortcut
to the full bank journal entry.

Partner inference only identifies the counterparty. It never posts, reconciles,
changes an amount or chooses a ledger account. The **Matching evidence** column
in Transactions explains the signal and confidence. Editing the partner
manually clears the automatic provenance.

Accounting Managers configure predictable direct treatments under
**Configuration > Bank Matching Rules**. See
[Manage Bank Matching Rules](manage-bank-matching-rules.md) for usage evidence,
redundant partner-only rules and governed rule suggestions.
