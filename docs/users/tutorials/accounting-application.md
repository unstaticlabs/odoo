# Tutorial: Take a Guided Tour of Accounting

This tutorial introduces the complete Accounting application without changing posted accounting.

## 1. Read Overview

Open **Accounting > Overview**.

Read it from top to bottom:

1. **Cash on banks**, then the projected amount after open reconciliation
   balances settle and unposted employee expenses are reimbursed;
2. the **To Match** smart indicator and the compact alert on Cash on banks,
   when bank work remains;
3. **Compte Courant Associé**, including who is estimated to owe whom;
4. documents and balances needing attention;
5. Accounting Hygiene;
6. the next closing workspace and declaration;
7. prepared review work.

The **Tax returns** card lists the next three pending declarations in deadline
order. An overdue return always appears before a future one. Open a row for its
name, type, deadline and current preparation status, or use **All declarations**
for the complete company schedule.

Open either matching indicator to inspect Bank Matching, then return with the
browser Back button. Blocking alerts link directly to their issues; green
**Ready** chips identify sections with no current action. Expand **View
projection details** on Compte Courant Associé and reconcile its posted-account
and unpaid-expense components.

## 2. Inspect Journals and documents

Open **Journals** to see native journal cards. Open a bank journal and compare:

- **Transactions**, the complete statement-line list; and
- **Bank Matching**, the focused matching queue.

Sales and Purchases cards show six calendar months of posted document totals
in company currency. Refunds reduce the month, empty months remain at zero and
the final bar is the current month to date. These bars describe invoicing
activity—not cash received, cash paid or the remaining balance.

Open **Vendors > Bills** and choose a bill. Its business lines, taxes, journal
items, residual, payment state and attachments remain on the normal Odoo
document. Existing-payment suggestions are visible while the bill is draft,
but matching becomes available only after posting. Close bank transactions may
also be suggested when the supplier is inferred, missing or different; the
**Best match** helper explains the sourced evidence, while the **Add** helper
discloses any supplier or account change before the action is used.

## 3. Follow a value into accounting

From a bill or invoice, open its journal items. Note the account, partner, debit, credit, currency, analytic distribution and matching reference.

Open **Reporting > Grand livre**, choose the same period and search for the account or document. Select the line to return to the source.

## 4. Review reconciliation

Open **Accounting > General Reconciliation**.

Use **Unreconciled** to find work, then group by account or partner. **Reconciled** shows completed matching with a colored matching-reference chip, full or partial status, residual and Undo.

Do not reconcile during this tutorial unless you intend to change accounting.

## 5. Use an interactive report

Open **Reporting > Balance générale**.

Change the period, unfold an account group, search for an account and open its journal items. Download PDF and XLSX; both exports use the filters currently visible on screen.

## 6. Read Accounting Hygiene

Open **Review > Accounting Hygiene**.

Each row is a focused, deterministic recommendation. Open one to see:

- what needs attention;
- why it matters;
- the suggested next action;
- accounting consequence;
- evidence and confidence;
- responsible role and optional assigned user.

**Open Source Record** takes you to the affected records. **Check Resolution** only resolves an issue when the underlying condition is actually fixed.

## 7. Inspect declarations and closing

Open **Declarations** and choose the next obligation. Ledger-derived values and external confirmations are separate. Use **View Entries** to trace a field to journal items.

Open **Accounting > Closing > Closing Workspaces**. Controls are calculated from current records. Accountant review is available, but internal approval can progress without it when every blocking control is clear.

You now know where daily work starts, how accounting values connect, and where review and statutory preparation live.
