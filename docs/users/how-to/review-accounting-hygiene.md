# How To Review Accounting Hygiene

Audience: Valentin, the accountant, and finance operators.

Use Accounting Hygiene to turn daily accounting gaps into a short,
company-scoped review queue. It does not create a second issue database: every
count opens the native record, current closing control or durable review
decision behind it.

## Open the Workbench

Go to:

```text
Accounting > Review > Control > Accounting Hygiene
```

Open the active company. The status is:

- `Ready` when no current hygiene bucket requires attention;
- `Attention Required` when daily work, evidence, a warning or a reviewer
  decision remains;
- `Blocked` when a P0 issue or current blocking closing control exists.

## Review the Queues

Work through the sections in this order:

1. `Bank to Match`: finish Bank Matching or document a deliberate boundary.
2. `Incomplete Documents`: complete, post, cancel or classify draft invoices
   and bills.
3. `Vendor Documents Missing Evidence`: attach the source supplier document or
   explicitly document why it is unavailable.
4. `Expenses Missing Receipts`: attach the source receipt or obtain the
   required explanation.
5. `Stale Draft Documents` and `Stale Expense Work`: review items more than 30
   days old.
6. `Unusual Account Balances`: review accounts whose aggregate debit/credit
   direction is opposite their configured natural side.
7. `Current Closing Controls`: inspect bank, tax, payroll, asset, currency,
   analytic, report, FEC and lock-date controls for the active period.
8. `Open Issues` and prepared decisions: resolve technical defects or route
   accounting judgments to the named authority.

Open receivable and payable balances are review queues, not automatic errors.
Confirm legitimate balances instead of clearing them merely to reduce a count.

An unusual balance is also a review signal, not proof of an error. The control
uses all posted history through the close date for balance-sheet accounts and
the configured fiscal year for income and expense accounts. It recognizes
common French contra-account families such as accumulated depreciation and
purchase/sales rebates. Legitimate examples can include a bank overdraft,
supplier advance, customer credit or two-sided clearing account.

Accounting Managers can change `Hygiene Balance Policy` on an account in the
Chart of Accounts when its documented natural side differs from the automatic
rule. Choose `Debit or Credit Is Expected` only when either direction is
genuinely normal; retain the accounting evidence behind that choice.

## Refresh Period Controls

Accounting Managers can click `Refresh Controls`. This recalculates the current
closing controls and reloads the workbench.

Accountant reviewers cannot refresh or mutate accounting state. They can open
the same allowed-company records, inspect evidence and prepare authorized review
decisions.

## Keep Ownership Clear

- `Prepared for Valentin` contains product, scope or CEO decisions.
- `Prepared for Prosper` contains accountant decisions.
- Finance operators resolve factual daily-work queues.
- Automatic checks may identify a record, but they do not self-accept an
  accounting judgment.

Do not mark a closing, report, FEC or tax decision accepted unless the named
authority has reviewed its evidence.
