# Expense Batch production readiness

Status: qualified for human production review

This matrix ranks the ten product journeys that must remain reliable before the
Expense Batch feature can be promoted. It covers only Expense Analytics and
Batch context; document-management products and their infrastructure are not
part of this gate.

## Personas

- **Submitter** captures receipts, prepares draft expenses and groups related
  work without accounting privileges.
- **Expense Manager** reviews purpose, evidence and exceptions, then approves
  or returns individual expenses.
- **Accounting Manager** owns shared ledger context and controlled exception
  replacement.
- **Accountant** posts native employee-paid and company-paid expenses and
  reconciles the resulting entries.
- **Read-only Accountant** audits expenses, entries, analytics and evidence
  without changing business state.
- **Multi-company Operator** works in one or more allowed companies without
  crossing employee or company boundaries.
- **Migration Operator** reconstructs historical expense context
  deterministically and removes migration machinery from the delivered
  registry.

## Ranked journeys

| Rank | Priority | Persona | Journey and acceptance gate | Automated evidence | Current state |
| --- | --- | --- | --- | --- | --- |
| 1 | P0 | Submitter | Select compatible draft or later-stage expenses and add them to a proposed, new or existing draft Batch without changing native state. | `TestExpenseBatch.test_candidate_service_and_create_or_select_flow`, `test_posted_expense_can_be_batched_and_links_its_existing_move`, `test_approved_and_posted_expenses_can_join_existing_batch`, create-or-select browser tour | Covered |
| 2 | P0 | Accountant | Post a mixed employee/company-paid Batch, complete the native reimbursement wizard and retain one Batch, references, expense links and open-side visibility. | `TestExpenseBatch.test_mixed_payer_posting_keeps_one_batch_and_remaining_action`; Submitter/Manager/Accountant role-handoff browser tour; rollback-only QA posting probe | Covered |
| 3 | P0 | Submitter / Accounting Manager | Preview and apply shared analytics or account context while preserving explicit choices, later-stage accounting, stale-revision safety, removal baselines and retry idempotence. | `test_shared_context_precedence_revision_idempotence_and_removal`, `test_matching_explicit_context_is_not_reported_as_an_exception`, focused review browser tour | Covered |
| 4 | P0 | Submitter | See exact missing receipt or required-field reasons and submit atomically, with no partial transition when one draft is incomplete. | `test_readiness_and_preview_wizard_are_deterministic`, `test_submission_blocks_incomplete_lines_without_partial_transition`, role-handoff browser tour | Covered |
| 5 | P0 | Expense Manager | Review a mixed-state Batch, return one submitted or approved line for correction, approve only actionable lines and never regress approved history. | `test_submit_approve_and_return_one_expense`, `test_mixed_draft_and_approved_expenses_advance_without_regression`, role-handoff browser tour | Covered |
| 6 | P0 | Submitter | Prepare and save an empty Batch, add expenses later, and expose submit/context actions only when meaningful. | `test_candidate_service_and_create_or_select_flow`, `test_views_keep_readiness_out_of_list_and_expose_drill_down` | Covered |
| 7 | P1 | Submitter | Capture native expenses and receipts, preserve Product tax/account defaults and payer mode, and keep duplicate evidence as a warning rather than a hidden mutation. | `test_native_receipt_capture_tour`; readiness, receipt checksum and mixed-payer model coverage; native `hr_expense` workflow remains authoritative | Covered |
| 8 | P1 | Read-only Accountant | Inspect Batch, expense, move, analytic and receipt drill-down while every edit, lifecycle and correction control is absent or denied. | `test_readonly_accountant_can_review_but_cannot_mutate`; read-only accountant browser audit tour; `usl_accounting` Expense Batch reporting tests | Covered |
| 9 | P1 | Multi-company Operator | See only allowed companies and reject every mixed-company or mixed-employee grouping, service call and report aggregation. | `test_employee_and_company_are_hard_compatibility_boundaries`; `usl_accounting` multi-company expense and reporting suites; core desktop/mobile company-switch suites and `usl_locale` switcher tests cover the standard control | Covered |
| 10 | P1 | Migration Operator | Reconstruct the Canada Batch, repair only transition-owned provenance/taxes, preserve non-draft signatures, rerun idempotently and deliver no migration registry residue. | focused transition tests, product/migration source boundary, product database boundary, deterministic reconstruction acceptance | Covered |

## Retrospective grouping decision

Two implementations were considered for adding approved or posted expenses to
an existing Batch:

1. keep a direct `hr.expense.write` in the transient wizard; or
2. expose one Batch service that checks Batch write access, expense read
   access, native eligible states and company/employee compatibility before a
   narrowly scoped elevated link write.

The service is used because native Odoo intentionally makes approved and
posted expenses read-only for employees. Keeping elevation in the wizard would
duplicate the new-Batch path, make non-browser clients behave differently and
leave compatibility checks easy to bypass. The service never rewrites
later-stage accounting; context application skips those records and only links
their existing entries.

## Promotion rule

All P0 journeys must have ORM coverage and at least one browser lifecycle that
crosses Submitter, Expense Manager and Accountant roles. P1 security,
multi-company, reporting and migration gates must pass in the isolated QA
project. Any failed gate reopens the journey; a warning cannot be hidden or
converted into a passing result.
