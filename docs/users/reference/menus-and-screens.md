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
When configuration is missing, an administrator can use **Configure in
Accounting Settings** directly from the card. A unique shareholder on account
455100 is restored automatically during reconstruction; ambiguous cases remain
an explicit choice.

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

## Bank Statements

The primary monthly completeness screen for configured bank exports. The list
shows whether each statement **Needs attention**, is **Ready for review**, is
**Certified**, or was **Reopened**. Open a month to inspect the retained source
email and files, imported native movements, official Documents version,
opening/closing balances, continuity and exceptions. Import completeness and
transaction reconciliation are separate: certifying a complete bank statement
does not mark its individual movements as matched.

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

Select the **Expenses** app title for **My Expenses**, the individual expense
evidence and workflow list. The navbar links directly to **Expenses to
Process** and **Expense Batches** (**Lots de dépenses** in French), followed by **Reporting** and
**Configuration**. It deliberately has no redundant **My Expenses** menu
group. The list shows **Attachment status**, each expense's normal status and
its optional **Expense Batch** link without a permanent **Batch readiness**
column.

The default **Needs batching** filter shows only unbatched Draft, Approved and
Posted expenses. Paid, Refused, Submitted and In Payment expenses remain
available through **All expenses**. Batching is optional: removing the filter
does not change an expense or prevent its normal submission, posting or
payment. Use **Ready to submit**, **Needs information** and **Already in a
batch** for more specific review. Select one or more eligible expenses, then
use **Add to a Batch**. For a multi-selection it is the primary contextual
action without removing native submission actions. The create-or-select
preview ranks compatible Batches, shows readiness, payer split, warnings and
context impact before anything is saved. Both completion actions close the
preview and refresh the list.

Managers use **Expense Batches** to review purpose, totals, dates, payer split,
interactive shared analytics, Product breakdown and individual evidence. A
small line indicator explains the exact missing information, warning or real
context difference. Accounting entries retain a direct link back to the Batch
and its expenses.

## B2C application

**Orders** contains canonical historical orders and every retained source row.
**Operations** separates payments/refunds/fees, fulfilment/COGS and governed
product/SKU mappings. **Accounting Sessions** contains monthly review sessions
and links to existing Accounting or bank evidence. **Analytics** deliberately
separates order revenue, evidenced line revenue, payment events, fulfilment
costs and native stock so totals from different grains are not multiplied.

**Configuration** is limited to B2C Managers. Restricted provider payloads are
hidden unless the user also has the sensitive-evidence role because they may
contain customer personal data. See [Review B2C commerce
history](../guides/review-b2c-commerce.md) for the complete review workflow.

## Action explanations

Consequential or non-obvious product buttons provide a concise explanation
when you pause the pointer over them. The explanation states whether the action
posts accounting, creates or deletes drafts, changes workflow state, updates
archive access, or contacts an external service. Confirmation dialogs remain
reserved for actions that are difficult to reverse.

The **Review status** chip on posted accounting documents records whether an
entry is **To Review**, **Reviewed**, **Supervised**, or an **Anomaly**. An unset
chip is explicitly labelled **Set review status** on a form and **No review
status** in a list; it no longer appears as an unexplained empty control.

## Accounting

Journal entries, journal items, payments, assets, general reconciliation and closing workspaces.

## Review

Accounting Hygiene and other focused review work. Technical audit tools are
available only in the advanced area.

## Vendors

**Incoming E-Invoices** lists approved-platform deliveries using business
outcomes: **Ready for Review**, **Duplicate Ignored**, **Rejected** or
**Needs Attention**. Successful rows open the native vendor bill. Provider
references, payload hashes and attempt diagnostics remain available only to a
technical administrator.

## Reporting

The canonical designed financial, partner, tax, asset, management and
analytical statements. **Compte de résultat** is the single French performance
statement; the former detailed alias is not shown separately. **Compte de
résultat analytique** is the governed
analytical statement; use **Reporting > Pilotage > Analyse analytique** for
free-form pivot exploration. Combine configured analytic plans with dates,
financial accounts, partners, products, journals and companies; switch between
pivot, list and graph; and open any aggregate to its source records.

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

**Configuration > Invoicing > E-Invoicing** shows honest product states:
**Configuration incomplete**, **Not yet verified**, **Test passed**, **Ready
but inactive**, **Production activation required** or **Active**. A full-width
**Next Action** leads to the relevant reception setup, test or production
connection fields. Development remains inactive.

Migration, reconstruction, parity, dump and test-orchestration menus are not
part of normal Accounting. Retained machinery is inaccessible from the daily
manager and read-only-accountant menus.
