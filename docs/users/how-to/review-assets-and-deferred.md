# How To Review Fixed Assets, Depreciation and Deferred Schedules

Audience: accountant, CEO, finance operator.

Use this guide to inspect fixed assets, depreciation evidence and deferred expense or revenue schedules.

## Review the Fixed Asset Register

Go to:

```text
Accounting > Review > Advanced Audit > Fixed Asset Register
```

Check:

- asset name;
- company;
- asset account;
- acquisition date;
- original value;
- imported net value;
- depreciation account;
- depreciation expense account;
- source depreciation move count.

For the current benchmark corpus, three source assets are represented.

## Export the Fixed Asset Register

Go to:

```text
Accounting > Reporting > Fixed Asset Register
```

Use the export wizard to preview and export the register.

For account grouping, use:

```text
Accounting > Reporting > Fixed Asset Register by Account
```

## Review the Depreciation Schedule

Go to:

```text
Accounting > Review > Advanced Audit > Imported Depreciation Schedule
```

Check:

- source asset;
- source move;
- depreciation date;
- expense amount;
- depreciation amount;
- accumulated depreciation;
- net book value after line;
- source move state.

Some schedule rows may represent future or draft source schedule evidence. They are review evidence, not necessarily posted accounting effects.

## Operate Deferred Schedules

For the native current-period workflow, go to:

```text
Accounting > Closing > Deferrals
```

Open a deferral to review its original bill or invoice, deferral account,
recognition account, dates, analytic distribution and posted/future schedule.
Accounting managers can create a schedule and use `Post Due Entries` or the
individual-line `Post` action. Reviewers can follow the original and posted
entry links but cannot create or post.

Do not post a future line merely to make a report look complete. A running
schedule is expected while future recognition dates remain.

## Review Imported Deferred Evidence

Go to:

```text
Accounting > Review > Advanced Audit > Imported Deferred Schedule
```

Check:

- original move;
- deferred move;
- schedule type;
- schedule phase;
- deferred account;
- counterpart accounts;
- amount;
- representation status.

Deferred rows can be:

- imported posted entries;
- source draft forecasts;
- review-only evidence.

This imported view is the historical evidence surface. The Closing > Deferrals
workspace is the operational schedule.

## Export Schedules

Use:

```text
Accounting > Reporting > Depreciation Schedule
Accounting > Reporting > Deferred Expense and Revenue Schedule
```

Preview first, then generate the export.
