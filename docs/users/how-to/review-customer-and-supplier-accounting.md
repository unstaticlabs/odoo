# How To Review Customer and Supplier Accounting

Audience: CEO, accountant, finance operator.

Use this guide when you need to understand what customers owe, what suppliers are owed, which invoices or bills remain open, and how those balances are represented in the reconstructed accounting evidence.

## Start from Partner Reports

Open:

```text
Accounting > Reporting > Partner Ledger
```

Use the benchmark period unless you are reviewing another period:

```text
Start Date: 2024-01-10
End Date: 2025-09-30
Target Move: Posted Entries Only
```

Use the `Partners` filter if you want one customer, supplier or shareholder account.

Click `Preview`.

## Review Customer Balances

For customer accounting, use these reports:

- `Customer Statement` to review a customer-focused statement.
- `Aged Receivable` to review what remains owed by customers as of the selected date.
- `Open Items` to inspect open receivable lines.
- `Partner Ledger` to inspect all partner movements, including settled lines.

In the preview, check:

- partner name;
- account code;
- move name;
- due date where present;
- debit;
- credit;
- residual;
- balance.

Use `Open Journal Items` or the external-link icon on a preview row to inspect the contributing journal items.

## Review Supplier Balances

For supplier accounting, use these reports:

- `Aged Payable` to review supplier debts as of the selected date.
- `Open Items` to inspect open payable lines.
- `Partner Ledger` to inspect the supplier's full activity.
- `General Ledger` filtered to payable accounts if the accountant wants account-level detail.

In supplier review, check:

- bill reference;
- supplier name;
- due date;
- payment or residual status;
- tax lines;
- reconciliation relationship;
- source trace fields.

## Check One Invoice or Bill

From a report row:

1. Click the external-link icon or `Open Journal Items`.
2. Open the journal item.
3. Check the source-trace fields.
4. Open the parent journal entry.
5. Review the debit and credit lines.
6. Review the partner and account.
7. Check attachments if they are available on the document or related entry.

The target record does not need to reuse the same internal database ID as Odoo Online. It must preserve the business identity and the trace back to the source record.

## Check Settlement State

For a paid or partially paid document, compare:

- residual amount;
- open item report membership;
- full or partial reconciliation review records;
- linked payments where available;
- any exchange-difference entry.

Do not treat a zero residual alone as proof that reconciliation parity is correct. Use the reconciliation review screens when the relationship matters.

## Typical CEO Review Questions

Use these checks to answer practical questions:

- Which customers still owed money at the close?
- Which suppliers were still unpaid at the close?
- Is the shareholder current account represented separately from company cash?
- Are the largest customer or supplier balances supported by source-traced entries?
- Do customer and supplier balances agree with the balance sheet control accounts?

## Typical Accountant Review Questions

Use these checks to answer accounting review questions:

- Do receivable and payable account balances agree with partner subledgers?
- Are due dates and residual amounts reliable?
- Are credit notes and payments represented with the correct partner?
- Are open items really open, rather than missing reconciliation import?
- Are material balances supported by evidence or an explicit missing-evidence discrepancy?
