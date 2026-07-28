# Menus and Screens

## Overview

The daily starting point. It summarizes cash, documents, reconciliation,
Hygiene, declarations and closing readiness. Each actionable count opens the
relevant records.

The **Cash on banks** headline includes only real bank and payment-account
balances effective through today. **Projected after settlement** estimates the
result if every open General Reconciliation balance settles in cash and every
unposted employee-paid expense is reimbursed. **Projected after taxes** then
reserves estimated year-to-date corporate income tax without posting anything
to accounting. Expand **Projection details** for the signed open
balance, identified receipt/payment subsets, Draft/Submitted/Approved expense
totals, posted profit before IS, rate bands and account 444 instalments. Select
any amount to inspect its records or the related declarations.

When bank transactions still need a counterpart or accounting category, a
compact **N to match** alert appears on Cash on banks and the **To Match**
smart indicator remains available above the Overview. Both open Bank Matching;
the compact cash alert disappears naturally when the queue is clear.

Blocking Hygiene and closing/declaration alerts link directly to the records
that need review. Cards with no current action show a lightweight green
**Ready** status.

**Compte Courant Associé** shows the estimated net position with the
shareholder configured in Accounting Settings. The amount says whether USL
owes the shareholder or the shareholder owes USL. Expand **View projection
details** to reconcile the posted account 455 balance with unpaid expenses
that are not already posted to that account. The estimate is read-only and
does not reimburse, post or reclassify anything.

The tax figure is planning guidance, not the final 2065 result. Unposted
expenses do not reduce it, and fiscal adjustments, losses and credits remain
for declaration review. In the 25% band, a genuinely deductible €1 expense
typically lowers the estimate by about €0.25; an investment may only be
deductible through depreciation. The projection is intentionally broader than
cash on banks: suspense, tax, current-account and prepayment residuals remain
visible until Accounting cleans or reconciles them.

## Journals

The native journal dashboard. Use journal cards to open entries, documents, full-width bank transactions or Bank Matching.

## Transactions

The complete bank-transaction history. Clicking a row opens the bank
transaction. **Linked document or entry** opens the matched invoice, bill,
refund or journal entry; **Open Entry** opens the bank transaction's own journal
entry. **Match** opens an unreconciled or partially matched line in Bank
Matching. A matching-reference chip opens every journal item in that matching
group. Fully matched transactions offer **Undo Match** to Accounting users
after confirmation.

The transaction form keeps matching and review status separate. It places the
bank transaction beside the same accounting-line presentation used in Bank
Matching, including accounts, partners, dates, labels, debit, credit,
currencies and any open balance. Accounting users can set or correct the
partner directly before matching; suggestions remain optional shortcuts. The
linked **Still to match** residual opens that transaction in Bank Matching;
after completion it becomes **View matching**. The
read-only accountant can inspect the same evidence but cannot select or remove
proposed lines, match, undo or change the partner.

## Customers and Vendors

Customer invoices, credit notes, payments, supplier bills, refunds and vendor
payments.

Under **Vendors**, **Bills** opens with a removable **Bills** filter and
**Expenses** opens with a removable **Receipts** filter. Use Bills for supplier
invoices and Expenses for purchases supported by a receipt rather than an
invoice. Removing either chip broadens the view to the other vendor document
types without changing or duplicating the underlying accounting records.

Employee expense claims remain in the separate **Expenses** application.

## Expenses application

Use **Expenses > My Expenses** for individual expense evidence and workflow.
The list shows **Attachment status**, each expense's normal status and its
optional **Expense Batch** link. It deliberately does not show a permanent
**Batch readiness** column.

The default **Not in a batch** filter keeps the working list limited to
expenses that can still be grouped; remove it to review historical batched
expenses. Use **Ready to submit**, **Needs information** and **Already in a
batch** for more specific review. Select one or more unbatched Draft, Approved
or Posted expenses, then use the single **Create expense batch** action. The
former automatic **Submit ready expenses** action is not part of the product.
The preview shows aggregate readiness, attachment and expense statuses, and
missing information before the batch is saved or its Draft lines are
submitted. Both completion actions close the preview and refresh the list.

Managers use **My Expenses > Expense Batches** to review the shared purpose,
period, totals, analytic context and individual evidence. Accounting entries
retain a direct link back to the batch and its expenses.

## Action explanations

Consequential or non-obvious Accounting buttons provide a concise explanation
when you pause the pointer over them. The explanation states whether the action
posts accounting, creates a draft, changes workflow state, opens supporting
records, or contacts an external service. Confirmation dialogs remain reserved
for actions that are difficult to reverse.

The **Review status** chip on posted accounting documents records whether an
entry is **To Review**, **Reviewed**, **Supervised**, or an **Anomaly**. An unset
chip is explicitly labelled **Set review status** on a form and **No review
status** in a list; it no longer appears as an unexplained empty control.

## Accounting

Journal entries, journal items, payments, assets, general reconciliation and closing workspaces.

## Review

Accounting Hygiene and other focused review work. Technical audit tools are available only in the advanced area.

**Electronic Invoice Reception** lists every approved-platform payload and its
draft bill, duplicate, rejection or technical-failure evidence.

## Reporting

The canonical designed financial, partner, tax, asset, management and
analytical statements. **Compte de résultat analytique** is the governed
analytical statement; use **Reporting > Analyse analytique** for free-form pivot
exploration. Combine configured analytic plans with dates, financial accounts,
partners, products, journals and companies; switch between pivot, list and
graph; and open any aggregate to its source records.

## Declarations

French obligations, periods, deadlines, calculated fields, external confirmations, review and filing status.

## Configuration

Accounting settings, journals, accounts, taxes, analytic dimensions and the
shared Accounting Framework. Accounting Managers govern Controls, Reports and
Declarations from one discoverable area. Read-only accountants inspect the
operational results, while Technical Administrators can see installed engine
keys and implementation boundaries.

Under **Settings > Management Projections**, Accounting Managers select the
account and employee used by the Overview shareholder current-account
projection.

**Configuration > Electronic Invoicing** shows software capability,
production readiness and live-connection status separately. It holds company
identifiers, provider decision, reception journal and controlled activation
approval. Development remains visibly **Not Connected**.
