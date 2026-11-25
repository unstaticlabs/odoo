# Accounting overview read contract

The company overview distinguishes three scopes:

- `posted_move_count` covers all dates.
- `open_annual_closing_id` identifies the latest ended annual workspace still
  open; `annual_posted_move_count`, `annual_journal_count` and
  `annual_archived_journal_count` cover that workspace.
- `latest_closing_period_id` keeps the existing operational-period selection.
  It does not replace the separate annual-workspace relation.

`draft_expense_count` retains its historical meaning: expenses to process in
`draft`, `submitted` or `approved` state. The additive
`draft_only_expense_count`, `submitted_expense_count` and
`approved_expense_count` separate those states in the same company SQL view.
An approved-only company therefore has zero draft-only expenses, while its
legacy work count remains nonzero. Posted, paid and refused work is outside
these state counts.

These are workflow-state aggregates, never permission to edit an expense.
Native model access, company rules and expense workflows remain authoritative.
Reading the breakdown requires only the same overview read access and does not
introduce additional access to individual expenses. Overview navigation,
existing KPI values and accounting history remain unchanged.

The MCP companion negotiates the additive fields through native `fields_get`
before its single overview `search_read`. Old servers keep their existing
response fields; unavailable detail is omitted and identified explicitly,
rather than represented as zero. No synchronized server/MCP rollout is required.
