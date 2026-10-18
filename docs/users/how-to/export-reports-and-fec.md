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

A report is **provisional** until the global fiscal lock or hard lock covers
its closing date. A report closing on 30 June is final once the lock is at 30
June or later; a lock on 29 June is not enough. This applies to every selected
company and to the comparison period. A tax, sales or purchase lock alone is
insufficient, and lock exceptions do not count. Draft-inclusive reports and
incomplete periods also remain provisional. The status is recomputed from the
lock dates each time the report is refreshed or exported; it is never a manual
label. It appears in the same way on screen and in the files:

- **Screen:** a yellow **PROVISOIRE** pill comes first among the active-scope
  pills, and a notice below them lists the reasons. A final report shows
  neither. The screen layout adapts to a phone.
- **PDF:** **Document provisoire** in the preamble and on every page, with the
  reasons in the preamble.
- **XLSX:** on the `Report` sheet, a bold row **DOCUMENT PROVISOIRE** with the
  reasons above the subtitle, and **Document provisoire** in the printed header
  and footer. The `Metadata` sheet shows a readable **Statut** row
  (**Provisoire** or **Définitif**) and, when provisional, the reasons. A final
  workbook has no label on the `Report` sheet.
- **CSV:** the status is part of the metadata column; there is no visible label.
- **FEC (`.txt`):** never labelled. The file layout is legal and unchanged; the
  FEC does not carry a provisional status.

The analytic pivot PDF has no closing date and carries no status.

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
