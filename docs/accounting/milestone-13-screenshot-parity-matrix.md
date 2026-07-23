# Milestone 13 screenshot parity and user-journey scorecard

Status date: 23 July 2026.

This matrix maps the eight supplied Odoo Online Enterprise screenshots to the
Community 19 replacement. It records functional equivalence, not visual
similarity. A screenshot is evidence of a user need; it is not sufficient
evidence of accounting correctness.

## Status vocabulary

| Status | Meaning |
| --- | --- |
| Implemented | The replacement exposes the same material workflow and it has technical evidence. |
| Equivalent replacement | The Community/OCA interaction differs, but the user can complete the same accounting job with preserved evidence. |
| Partial | The workflow is proven in isolated Track B or an audit surface, but the clean replacement target does not yet expose the complete result. |
| Not applicable | The source interaction is deliberately outside the confirmed company or milestone scope. |
| Deferred | The workflow is valid future scope but is not required for Accounting v1. |

## Screenshot matrix

| # | Enterprise reference | User job to preserve | Community 19 replacement | Status | Current evidence and remaining boundary |
| --- | --- | --- | --- | --- | --- |
| 1 | Accounting dashboard and configuration menu | Start daily accounting work and reach configuration without losing the native journal overview. | `Unstatic Labs — Accounting Home` is the default operational landing page; the native journal Dashboard remains a separate destination. The seven-area Accounting navigation keeps configuration manager-only. | Equivalent replacement | Manager and disposable reviewer browser journeys pass. The reviewer sees one allowed company and no Configuration menu or Accounting Settings control. |
| 2 | Dynamic Profit and Loss | Select a period and comparison, inspect expandable lines, drill into sources and export the same filtered result. | The canonical dynamic report workbench provides native/imported scope, period presets, comparison, posted/draft scope, filters, grouping, search, expansion, source drilldown and screen/XLSX/PDF parity. | Implemented | Add-on tests, production-derived report probes and manager/reviewer browser journeys pass. Formula, French statement variant and presentation acceptance remain a named professional gate. |
| 3 | General Ledger and report navigation | Review account movements with filters, opening/closing logic, source lines and exports. | Canonical General Ledger and Trial Balance launchers use the shared dynamic workbench; maintained OCA report screens remain technical comparison surfaces. | Implemented | Filtered journal export, drilldown, typed workbook and structured PDF evidence pass. Accountant acceptance remains open. |
| 4 | General Reconciliation | Review account-grouped residuals and reconcile eligible non-bank items without rewriting historical evidence. | Maintained OCA General Reconciliation is the operational workbench; source relationships remain in read-only Advanced Audit evidence. | Equivalent replacement | Track B proves native partials, document netting and exchange-difference paths. The 75 source relationships crossing draft/future endpoints remain review-only pending the recorded P1 policy decision. |
| 5 | Tax Returns timeline | See obligations, deadlines, form state and the evidence behind declared values. | The versioned Declarations workspace provides the schedule/calendar, CA12-E field guidance, source/calculation/warning/reviewer state and links to the official source and professional filing portal. | Equivalent replacement | Deadline and declaration tests and browser routes pass. Electronic submission is not claimed; final tax mapping and professional acceptance remain open. |
| 6 | Bank Matching | Categorize and reconcile bank transactions while preserving operator inputs and residual review. | Maintained OCA Bank Matching handles operational matching; imported historical relationships remain read-only evidence and unmatched items remain visible on Accounting Home. | Equivalent replacement | Manager/reviewer browser journeys, mutation-control tests and Track B categorization/reconciliation stages pass. Live bank synchronization is deferred beyond Milestone 13. |
| 7 | Vendor bill with split PDF preview | Open a native supplier bill and inspect/download the original invoice evidence beside the accounting document. | The standard vendor-bill form uses the Community chatter attachment workbench, source filename, rendered thumbnail and native PDF viewer. Source-designated main attachments are restored on the native move. | Partial / equivalent UI | Track B has 215/215 business-document binaries and 202/202 main selections, with zero missing files, unmapped targets, checksum mismatches, duplicate traces or main-selection mismatches. The manager browser path renders the bill, thumbnail and PDF viewer route. Community does not reproduce the Enterprise split pane pixel-for-pixel, and these Track B records still need integration into the clean replacement target. |
| 8 | Expenses list and receipts | Review a native expense, its approval/accounting state and the original receipt evidence. | Standard Odoo Expenses records preserve the source receipt as the native expense attachment and main evidence; the form exposes the receipt count, original filename and thumbnail. | Partial | Track B has 325/325 expenses, 263/263 binaries and 245/245 main selections, with zero attachment integrity defects. The manager browser path renders the expense and receipt thumbnail. Clean-target integration remains open. |

The source period has no customer credit-note case, so no screenshot or replay
can honestly prove that scenario. Supplier refunds are present in Track B.

## Native evidence architecture decision

Three credible designs were compared:

1. Keep source binaries only on exact-ledger evidence records. This maximizes
   historical isolation but leaves native bills and expenses without the
   evidence users need during normal work.
2. Link native records back to the restored source filestore. This avoids
   copying bytes but couples the replacement to a private, separately operated
   database and filestore, with fragile availability and authorization
   boundaries.
3. Replay verified binaries onto the native Track B records, preserve the
   source attachment trace and source-designated main selection, and rely on
   the native record ACL for access.

Option 3 is implemented. Every imported binary is read from the isolated source
filestore, checked against source checksum and size metadata, attached only
after its traced native target resolves, and counted in the stage evidence.
Missing files, unmapped targets, checksum mismatches, duplicate traces and
main-selection mismatches are blocking attachment defects. The source remains
read-only and the replay never falls back to an unverified link.

## User-journey scorecard

| Journey | Valentin / Accounting Manager | Prosper / Accountant Reviewer | Current result |
| --- | --- | --- | --- |
| Start daily work | Operational Home, prepared actions, balances, close/declaration state and native Dashboard route. | Same company-scoped Home without settings or mutation controls. | Passed in browser. |
| Review reports | Full dynamic filters, drilldown and exports. | Same review and export surfaces; no create/edit controls. | Passed in browser and add-on tests; professional acceptance pending. |
| Review bank and general reconciliation | Operational OCA workbenches and source audit links. | Read-only views and move drilldown; validate/reset/reconcile mutations hidden and denied. | Passed in browser and ACL tests; cross-boundary policy pending. |
| Review declarations and closing | Schedule, field guidance, close controls and package/FEC preparation. | Read-only evidence and test-file preparation within allowed company. | Technically passed; statutory and professional approvals pending. |
| Review native assets, deferrals and analytics | Operational native/OCA records with posted effects and schedules. | Read-only record, move and analytic evidence. | Passed in Track B browser journeys. |
| Review supplier evidence | Native bill, original PDF thumbnail and viewer route. | Imported native attachment binary is readable through the allowed accounting record. | Manager browser and reviewer regression test passed; clean-target integration pending. |
| Review expense evidence | Native expense, approval/accounting state, original receipt filename and thumbnail. | Expense navigation is available within the allowed company. | Manager browser and Track B reviewer navigation evidence passed; clean-target integration pending. |

## Permission matrix

| Capability | Accounting Manager | Accountant Reviewer | Enforcement evidence |
| --- | --- | --- | --- |
| Read allowed-company accounting records and imported evidence | Allowed | Allowed | Record rules, add-on tests and browser journeys. |
| Read another company without assignment | Denied by company scope | Denied by company scope | Reviewer Home pager and cross-company searches. |
| Configure Accounting | Allowed | Denied | Groups, menus and browser journey. |
| Post or edit operational accounting records | Allowed through normal Odoo controls | Denied | Model ACLs, view groups and mutation tests. |
| Validate/reset Bank Matching or General Reconciliation | Allowed where the native workflow permits | Denied | Server ACLs plus combined-view regression tests. |
| Read source-replayed bill/expense evidence | Allowed | Allowed on an accessible parent accounting record | Native attachment regression and accounting-evidence ACL tests. |
| Read private technical attachments outside accounting | System-only | Denied | Rollback-only `AccessError` regression. |
| Record a professional acceptance decision | Product/accounting authority only when acting in that named role | May prepare/record accountant decisions assigned to the reviewer | Durable review-decision workflow; no acceptance is self-recorded by the implementation agent. |

## Evidence and closure boundary

Private evidence:

- `track-b-documents-status.json`;
- `track-b-expenses-status.json`;
- `track-b-native-attachments-browser-status.json`;
- `accounting-home-browser-status.json`;
- `dynamic-report-browser-status.json`;
- Track B asset, deferral and analytic browser artifacts.

Readiness requires those technical browser and replay artifacts to remain
`passed`. It must nevertheless remain `blocked` while the open P0 report
acceptance, the P1 cross-boundary reconciliation policy and named
accountant/stakeholder decisions remain unresolved. This document is not a
production-migration authorization.
