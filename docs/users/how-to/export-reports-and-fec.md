# Export Reports and FEC

## PDF or XLSX

1. Open the report itself.
2. Set the company, dates, comparison, display unit, rounding, journals,
   accounts, partners and analytics.
3. Check the visible hierarchy and totals.
4. Select **PDF** or **XLSX**. The file downloads immediately with the current filters.

PDF is for reading and review. The readable XLSX `Report` sheet preserves
numeric cells, selected unit, selected rounding and the same visible
section/group/subtotal/total hierarchy; `Audit Data` retains raw unscaled rows
and filter metadata for analysis.

The PDF uses the official document template configured for the report:
company identity and legal context in the repeated header, selected period and
unit near the title, high-contrast financial sections, and document/page
context in the footer. If screen and export do not show the same scope, do not
use the export as review evidence.

A PDF displays **Document provisoire** in its preamble and on every page until
the global fiscal lock or hard lock covers the report's closing date. A report
closing on 30 June is final once the lock is at 30 June or later; a lock on 29
June is not enough. This applies to every selected company and to the
comparison period. A tax, sales or purchase lock alone is insufficient, and
lock exceptions do not count. Draft-inclusive exports and incomplete periods
also remain provisional; the preamble explains why.

The absence of that label does not certify the report or replace professional
review. The label changes presentation only; it does not change entries, totals
or posting permissions. Previously generated documents remain unchanged.

## FEC

1. Open **Reporting > Comptes et journaux > FEC**.
2. Confirm the proposed fiscal-year start and end dates. The defaults honor
   the exceptional first company exercise when applicable.
3. Keep **Test File** selected for validation or read-only accountant retrieval.
4. Select **Generate**.

Only an Accounting Manager can deliberately generate the final mode that may update a lock date. The read-only accountant receives a complete posted-entries test FEC.
