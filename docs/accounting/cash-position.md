# Cash position on Accounting Overview

The Overview cash card reports two company-currency figures effective through
the current date.

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

> Cash on banks + expected receipts - expected payments

Expected receipts and payments use posted, unreconciled residuals effective
through today, expressed in the company currency. They are limited to native
customer and supplier documents and employee-paid expenses:

- customer invoices and receipts are expected receipts;
- supplier refunds are expected receipts;
- supplier bills and receipts are expected payments;
- customer refunds are expected payments;
- posted employee-paid expenses awaiting reimbursement are expected payments.

Generic receivable or payable journal items, bank suspense lines and unmatched
bank allocations are not assumed to be future cash. The card counts these as
unresolved open items and links to their journal items for review. Resolving or
classifying the underlying accounting naturally updates the estimate.

This conservative definition was selected instead of summing every open 4xx or
5xx balance. The broader approach would double-count bank transactions already
present in Cash on banks and would treat transfers, prepayments and unexplained
clearing balances as future settlement.

## Drill-down and reconciliation

The collapsed **View estimate details** section keeps the daily card focused on
its two cash figures. Expanding it exposes the signed calculation and its
audit routes:

- **Included bank accounts** opens exactly the journals included in Cash on
  banks.
- **Expected receipts** and **Expected payments** open the residual journal
  items used in the projection.
- **Unresolved items** opens the other receivable/payable residuals
  deliberately excluded from the estimate.

All amounts use posted journal-item balances in the selected company's
currency. Entries dated after today are excluded.
