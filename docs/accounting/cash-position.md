# Cash position on Accounting Overview

The Overview cash card reports three company-currency figures. Cash and posted
ledger balances are effective through the current date; the future settlement
estimate also reserves for every currently open unposted reimbursement, and
the after-tax estimate adds a management reserve for year-to-date corporate
income tax.

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

## Projected cash after taxes

**Projected after taxes** is the settlement projection less an additional
management reserve for estimated year-to-date French corporate income tax
(`IS`). It is read-only: it does not post account 695, change account 444,
create a declaration value or claim to calculate the final taxable result.

The estimate:

- uses posted income and expense accounts from the active company's fiscal-year
  start through today;
- excludes account 695 so an already posted IS charge cannot make the
  calculation circular;
- floors losses at zero and rounds the provisional profit and tax to whole
  euros;
- uses either the conservative 25% rate or, when explicitly configured, 15% on
  the first €42,500 of profit for a twelve-month period and 25% above it;
- prorates the €42,500 ceiling for an irregular fiscal year using the official
  month-and-part-month convention;
- treats open debit residuals on French account 444 as instalments already
  reflected in cash and in the General Reconciliation projection;
- avoids reserving an open credit liability on account 444 a second time.

The configured profile is visible on **Settings > Companies > French
Declaration Profile > Cash Projection IS Profile**. The French SME profile must
only be selected after confirming the current turnover, fully paid capital and
ownership conditions. The imported USL profile selects it because the
reconstructed 2025 account 695 charge of €9,922 is consistent with a 15% rate
on the prior reviewed profit and the company record identifies a French SASU;
the 2065 ownership and group-turnover review remains required.

On `odoo_dev` at 28 July 2026, the posted accounting profit before IS is
€91,019.50 and the rounded planning base is €91,020:

- €42,500 at 15%;
- €48,520 at 25%;
- estimated gross YTD IS reserve: €18,505;
- open 2026 account 444 instalments: €5,670.

The €5,670 was already paid out of the bank and is added back by the open
General Reconciliation balance because it is a tax prepayment. Subtracting the
gross €18,505 reserve from the €97,483.87 settlement projection therefore
produces a net after-tax projection of **€78,978.87**, equivalent to a remaining
estimated IS cash cost of €12,835 after those instalments.

This is deliberately conservative about data that is not yet accounting truth.
Draft, submitted and approved expenses reduce the settlement projection but do
not reduce the tax base until posted. Fiscal reintegrations, deductions,
losses, tax credits, special-rate income and the final 2065 result remain in
the Declarations review.

For spending decisions, the 25% band is a marginal tax indicator, not a budget
to spend. One euro of a genuinely deductible expense in that band lowers this
estimate by about €0.25, so the company still bears about €0.75. An investment
may be capitalized and reduce taxable profit only through depreciation rather
than through its full purchase price.

Three approaches were assessed:

1. post a provisional IS journal entry from the card;
2. build a full French tax engine into Declarations;
3. keep a transparent management reserve linked to the existing 2065/2571/2572
   review.

The third is used. The first risks contaminating accounting with an unreviewed
estimate. The second would duplicate professional tax computation without
reliable fiscal-adjustment inputs. The current Community and pinned OCA add-ons
contain no maintained French YTD corporate-tax forecasting capability to
adopt.

Official basis:

- [DGFiP corporate-tax rates](https://www.impots.gouv.fr/international-professionnel/impot-sur-les-societes);
- [DGFiP payment and SME eligibility conditions](https://www.impots.gouv.fr/professionnel/imposition-des-resultats);
- [BOFiP reduced-rate ceiling and irregular-year proration](https://bofip.impots.gouv.fr/bofip/2065-PGP.html/identifiant%3DBOI-IS-LIQ-20-20-20230621);
- [BOFiP accounting-to-tax adjustments](https://bofip.impots.gouv.fr/bofip/5709-PGP.html/identifiant%3DBOI-BIC-BASE-30-20160203).

## Drill-down and reconciliation

The collapsed **Projection details** section keeps the daily card
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
- **Projected after taxes** opens the current 2571, 2572 and 2065 declaration
  workspaces.
- **Posted profit before IS** opens the included YTD profit-and-loss journal
  items, while **IS instalments already in settlement** opens the included
  account 444 residuals.

All amounts use the selected company's currency. Posted residuals dated after
today are excluded; currently open unposted employee reimbursements are
included regardless of expense date.

## Compte Courant Associé

The adjacent **Compte Courant Associé** card is a separate management
projection for the shareholder selected in **Accounting > Configuration >
Settings > Management Projections**. It is not part of Cash on banks and it
does not post or reclassify an entry.

On a reconstructed database, the product safely fills this configuration when
there is one exact `455100` account and its posted entries identify one unique
partner. It restores the matching employee/expense-owner business record when
needed. It never clones a production login or its access rights: users remain
an explicit deployment and identity-management decision. Ambiguous accounts or
partners are left unconfigured, and the empty card links administrators
directly to Accounting Settings.

French PCG account 455 records at credit the funds an associate temporarily
makes available to the entity. The card therefore normalizes the accounting
sign into plain-language direction:

- a credit balance is shown as an amount USL owes the shareholder;
- a debit balance is shown as an amount the shareholder owes USL;
- unpaid employee-paid expenses increase the amount USL is estimated to owe.

The projected position is:

> shareholder-perspective posted 455 balance + unpaid attributable expenses
> not already posted to that 455 account

The expense population covers Draft, Submitted, Approved, Posted and
In-payment employee-paid expenses attributed to the configured employee or
entered by their linked user. Unposted expenses contribute their
company-currency total. A posted or partially paid expense contributes only
its residual. An expense whose entry already uses the configured 455 account
is excluded from the expense component because its amount is already in the
posted account balance.

This last rule preserves the estimate as work progresses: posting an expense
directly to 455 moves it from the “unpaid expenses” component to the “posted
account position” component without changing the net projection twice.

The foldout links to the exact journal items and expense records behind both
components. On `odoo_dev` at 28 July 2026:

- account 455100 has a €12,639.57 debit balance, so Valentin currently owes
  that amount to USL on the posted ledger;
- €16,831.02 of Valentin's employee-paid expenses remain unposted;
- the net projected position is therefore **€4,191.45 estimated owed by USL
  to Valentin**.

Two approaches were assessed:

1. show the raw 455 debit/credit balance and add every expense total;
2. normalize the direction from the shareholder's perspective and exclude
   expenses already represented by posted 455 entries.

The second is used because it states who owes whom and prevents
double-counting. Native Odoo and the installed OCA modules provide the ledger
and expense workflows but no maintained Community shareholder-current-account
projection to adopt. The implementation remains an isolated, read-only
Overview extension.

For clean USL reconstructions, a conservative fallback accepts only the unique
exact account 455100 and unique employee-paid expense owner. Any ambiguity
disables the estimate instead of guessing. Saving the explicit account and
employee in Accounting Settings is the durable configuration.

Accounting basis:

- [French PCG account 455 definition on
  Légifrance](https://www.legifrance.gouv.fr/jorf/article_jo/JORFARTI000029583902);
- [BOFiP presentation of a creditor shareholder current-account balance as
  other debt](https://bofip.impots.gouv.fr/bofip/6175-PGP.html/identifiant%3DBOI-ANNX-000411-20140428).

The **transactions to match** count is no longer a competing Overview card.
It remains available as the **To Match** smart indicator above the Overview.
When non-zero, it also appears as an alert chip on Cash on banks. Both open the
canonical Bank Matching workspace.
