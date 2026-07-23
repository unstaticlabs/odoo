# Manage Currency Rate Automation

Audience: Accounting Manager.

Use this workspace to retrieve future ECB reference rates. It does not replace
the historical source rates used by imported accounting, and it does not replace
the actual conversion used by a bank, card processor or payment platform.

## Open the workspace

Go to:

```text
Accounting > Configuration > Currency Rate Automation
```

Only an Accounting Manager can open or change this configuration.

## Check the configuration

Confirm:

- `Company` is the company whose base currency and rates you intend to update;
- `Provider` is `European Central Bank`;
- `Retrieve Daily` is enabled;
- `Last Sync Status` is `Retrieved`;
- `Last Reference Date` is the latest ECB working-day date you expect.

The ECB normally publishes reference rates on working days. A weekend or holiday
can therefore leave the most recent reference date earlier than today.

## Retrieve now

Select `Retrieve Now`.

The details show:

- reference date;
- created and updated row counts;
- count of protected source-traced rows;
- currencies absent from the feed, if any.

Running retrieval again for the same reference date updates the same native rate
rows. It should not create duplicates.

## Inspect the native rows

Select `Open Currency Rates`.

Check:

- date;
- units of the foreign currency per company-currency unit;
- inverse rate;
- `Rate Provider = ecb`;
- retrieval timestamp when shown.

## Important accounting boundary

ECB values are reference rates published for information. Preserve the actual
bank or platform conversion when that conversion defines a transaction.

Never replace a source-traced historical rate to make a current reference rate
look consistent. If a retrieved rate appears wrong, stop and ask an Accounting
Manager to inspect the provider status and native rate row.
