# Cash position on Accounting Overview

The Overview cash card reports two company-currency figures. Cash and posted
ledger balances are effective through the current date; the future settlement
estimate also reserves for every currently open unposted reimbursement.

## Cash on banks

**Cash on banks** is the posted balance of the default accounts of active bank
and cash journals that are included in the cash position. Unmatched bank
transactions remain included because their posted liquidity line already
changed the real account balance.

The calculation deliberately excludes:

- accounts that are not the default account of a real bank or cash journal;
- internal-transfer and suspense accounts;
- pending receipt or payment accounts;
- credit-card and other financing journals;
- bank or cash journals that an Accounting Manager marks **Include in Cash
  Position** off, for example a restricted balance.

An Accounting Manager governs the last rule from **Accounting >
Configuration > Journals**. New bank and cash journals are included by default.

Two implementations were considered:

1. sum every account classified by Odoo as Bank and Cash;
2. use the default liquidity accounts of configured bank and cash journals.

The second is used. Account classification alone also captures legacy
pending-collection and internal-transfer accounts, while a journal default
account represents the actual account whose balance the native Odoo bank
dashboard manages.

## Projected cash after settlement

**Projected cash after settlement** is:

> Cash on banks + signed open General Reconciliation balance − unposted
> reimbursable expenses

The General Reconciliation component uses every non-zero residual on a posted,
reconcilable account effective through today. A positive residual increases the
projection and a negative residual decreases it. It therefore includes
receivables, payables, tax and social balances, shareholder/current accounts,
suspense accounts, prepayments and other accounts visible in the canonical
General Reconciliation workspace.

Expected receipts and payments remain separately identified, drillable subsets
of that balance:

- customer invoices and receipts are expected receipts;
- supplier refunds are expected receipts;
- supplier bills and receipts are expected payments;
- customer refunds are expected payments;
- posted employee-paid expenses awaiting reimbursement are expected payments.

They are not added again. This prevents the same payable or receivable journal
item from changing projected cash twice.

The expense component covers company-currency totals for all employee-paid
expenses currently in **Draft**, **Submitted** or **Approved** state that have
no accounting entry yet. A future expense date does not exclude an already
entered claim: the card is estimating eventual settlement, not only today's
ledger. Posted, in-payment and paid expenses are excluded because their
accounting residual or bank movement is already represented. Company-paid
expenses are also excluded: their cash movement has already occurred.

Two projection policies were compared:

1. include only identified commercial documents;
2. model the broader scenario in which every open General Reconciliation
   balance settles in cash, then reserve for unposted employee reimbursements.

The second is now used because it gives management a more assertive view of the
complete open accounting position. It is intentionally a planning estimate,
not a forecast of individually scheduled cash flows. A suspense balance,
prepayment, VAT credit or other open item may ultimately clear through
reclassification or offset rather than cash. Cleaning and reconciling those
accounts updates the estimate naturally.

The `odoo_dev` candidate verified on 28 July 2026 also demonstrates why the
identified receipt and payment subsets must not be added twice: €50.30 of
expected receipts and €166.80 of expected payments are already included in the
€18,397.47 signed General Reconciliation balance. With €95,917.42 cash on banks
and €16,831.02 of unposted reimbursable expenses, the mathematically reconciled
projection is €97,483.87.

## Drill-down and reconciliation

The collapsed **How this estimate is built** section keeps the daily card
focused on its two cash figures. Expanding it exposes the signed calculation
and its audit routes:

- Select the **Cash on banks** amount to open exactly the journals included in
  that balance.
- Select the **Projected after settlement** amount to open the bank,
  liquidity and reconcilable accounts used by the estimate.
- **Open General Reconciliation balance** opens every included residual,
  grouped by account.
- **Expected receipts** and **Expected payments** open the identified document
  subsets already contained in that balance.
- **Unpaid expenses** opens the included draft, submitted and approved
  employee-paid expenses; the card also shows their totals by state.

All amounts use the selected company's currency. Posted residuals dated after
today are excluded; currently open unposted employee reimbursements are
included regardless of expense date.
