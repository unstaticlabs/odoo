# Investigate a Report Value

1. Open the canonical report from **Reporting**.
2. Confirm company, period, posted/draft scope, currency and active filter pills.
3. Unfold the financial section, then the source line. Follow the French PCG
   group code and any subgroup to the full account number.
4. Select the line or contextual drill-down to open journal items.
5. Inspect date, journal, entry, partner, currency, debit, credit, residual, analytic distribution and matching reference.
6. Open the original bill, invoice, payment, expense or journal entry.
7. Return with the browser Back button; the report session preserves its
   filters and opened lines.
8. Download PDF or XLSX only after the screen shows the intended scope.

For VAT, declaration and French-statement lines, the displayed source and adjustment classification explains whether a value is ledger-derived or external.

## Fiscal result and appropriations in the balance sheet

**Résultat de l’exercice** shows net income and expenses from the company's
fiscal-year start through the report's end date. Its label states those dates.
For a monthly report, this is the fiscal-year-to-date result; the profit and loss
report continues to use its selected period. A posted entry that resets income
or expense accounts remains part of the ledger calculation. The report does
not reconstruct a result from before that entry.

**Report et affectations du résultat** combines historical income and expenses
with posted balances of appropriation accounts. It appears when their net is
nonzero. An appropriation's posting date does not establish which year's result
it allocates. Actual capital, reserves, retained earnings and dividends remain
in their posted accounts. These are presentation calculations; opening a report
creates no appropriation or corrective journal entry.

Open the source button to inspect the contributing journal items, including
historical items where applicable. The XLSX **Audit Data** sheet retains the
prior result and the opening/current appropriation components. For combined
companies, each company's own fiscal-year dates apply; the report retains their
individual periods. A deliberately restricted account, journal, partner or
analytic scope can be unbalanced: investigate its actual difference rather than
assuming every filtered view must balance.

For a fully offset historical result, the visible carryforward line is omitted.
Its technical evidence remains in **Audit Data**: account identifiers, native
opening/current/closing balances, exact company context and source domains.
The calculation always includes the complete report scope. Audit-cell evidence
is bounded; its total counts and completeness flags explicitly identify any
omitted account contributions or oversized filter context. Use the native
ledger with the saved report filters when that evidence is incomplete.
