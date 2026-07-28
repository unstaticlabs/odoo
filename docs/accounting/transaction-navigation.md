# Transactions navigation contract

Status: implemented and regression-tested on `saas~19.2`.

## Product purpose

**Transactions** is the complete bank-statement-line history and investigation
workspace. It is intentionally distinct from **Bank Matching**, which is the
operational reconciliation queue, and from **General Reconciliation**, which
starts from reconcilable non-bank ledger accounts.

Every navigation target in Transactions has one meaning:

| Interaction | Required target | Purpose |
| --- | --- | --- |
| Click the transaction row outside an explicit link or button | The selected `account.bank.statement.line` | Inspect the bank transaction. |
| Click **Linked document or entry** | The related `account.move` | Open the matched invoice, bill, refund or journal entry. It must never fall through to the statement-line row action. |
| Click **Open Entry** in the list | The bank statement line's own `move_id` | Open the full journal entry directly from the compact history. The transaction form already displays its journal items. |
| Click **Match** | The selected line in Bank Matching | Match or categorize an unreconciled transaction. |
| Click the matching-reference chip | Every journal item sharing that matching code | Inspect both sides of the reconciliation without changing it. |
| Click **Undo Match** | The reopened bank transaction and affected journal items | Remove a completed match after explicit confirmation. Accounting users only. |

The linked-document column is a primary navigation shortcut, not an exhaustive
reconciliation graph. When several moves are connected, business documents
are preferred over miscellaneous journal entries. Matching references,
residuals, the bank entry and Matched Items/Undo remain the complete evidence
paths.

## Transaction form states

The transaction form is an investigation surface. It shows the bank fact and
the accounting entry together; it does not duplicate the Bank Matching
workbench.

| State | Primary status | Available accounting action | Evidence shown |
| --- | --- | --- | --- |
| Unmatched | **To match** | Set or correct the partner, then use **Match** or the linked **Still to match** residual | Journal items, amount still to match, running balance, partner evidence and bank-source details |
| Partially matched | **Partially matched** | Use **Match** or the linked residual to continue | Residual, matching-reference drill-down and linked document or entry |
| Fully matched | **Matched** | **View matching** inspects the OCA result; **Undo Match** remains an explicit Accounting-user action | Matching reference, linked document or entry, related payment and bank entry |
| Any entry flagged for review | Separate **To Review** or **Anomaly** badge | Review remains governed on the journal entry or in Bank Matching | Matching state remains visible independently; “matched” never hides a review obligation |

The scoped read-only accountant sees the same accounting evidence and can open
the full bank entry, linked document, matched items and read-only OCA matching
view. Match, undo and partner-changing actions are absent. Accounting users can
edit the partner only while the transaction is still unmatched. Odoo's native
statement-line write synchronization updates the generated entry; partial or
completed matches are kept read-only to protect their accounting links.

## Accounting and access invariants

- The linked record is derived from native partial-reconciliation edges on the
  statement move's counterpart lines.
- The statement move itself is excluded from the linked-record candidates.
- A customer or vendor document is preferred when both a business document and
  a miscellaneous entry are connected.
- The link is company-scoped through normal Odoo record rules and uses the
  standard `account.move` form action.
- The navigation performs no write, posting, matching or reconciliation.
- A missing related move leaves the column empty; it must not manufacture a
  target from a label or reference.
- Matching status and review status are separate. A reconciled transaction can
  still require review.
- The amount, date, partner and bank evidence stay company- and
  currency-scoped through native fields and record rules.
- Entering Bank Matching never replaces the preceding Accounting route during
  automatic first-line selection. Browser Back therefore returns to Overview,
  Transactions or the source document that opened the queue.
- Selecting or advancing to another bank line updates the current Bank Matching
  route synchronously. It does not create one browser-history entry per line
  and cannot apply a delayed route update after the user has navigated away.

## Implementation decision

Two list-navigation approaches were considered:

1. patch the global list renderer or add a custom click handler for the
   Transactions column;
2. declare the computed relation as Odoo's native `many2one` widget.

Option 2 is used. The native widget renders an `o_form_uri` link, stops the
row-click event and asks `account.move.get_formview_action` for the target.
This stays aligned with Odoo's navigation and access-control behavior and
avoids a custom frontend action service.

Two data representations were also considered:

1. retain the former display-only character value and infer a target in the
   client;
2. expose the selected target as a computed `Many2one("account.move")`.

Option 2 is the governed contract because the model and record ID are explicit.
`rebuild_linked_document` remains only as a temporary cached-client metadata
alias and is not used by the current view.

Two form approaches were considered:

1. keep inheriting OCA's generic statement-line form and add more XPath
   fragments to its single grid;
2. use a dedicated Transactions form while retaining OCA's form exclusively
   for Bank Matching.

Option 2 is used. The generic form is intentionally minimal and reusable, but
its single grid is not a suitable investigation layout; injecting suggestion
buttons into that grid caused later labels and values to shift into unrelated
columns. The dedicated form therefore owns only the investigation layout: bank
identity, partner evidence and progressive disclosure.

The accounting-line presentation is not duplicated. Transactions uses a
read-only template inherited from OCA's `account_reconcile_oca_data` component.
Both screens therefore consume the same `reconcile_data_info` contract and the
same amount, currency, counterpart and open-balance formatting. On an open
transaction this represents the current matching proposal; on a matched
transaction OCA rebuilds it from the posted entry. The Transactions variant
removes line selection and deletion because operational matching remains in
Bank Matching. This shared-component approach was preferred to both a separate
`account.move.line` table and a custom split-view engine: it follows OCA
changes, refreshes through Odoo's form model and avoids parallel client state.

For Bank Matching history, two approaches were compared:

1. retain OCA's `pushState` call for every automatically or manually selected
   line;
2. leave the entry route untouched during automatic selection, then
   synchronously replace only the current Bank Matching route when the
   selection changes.

Option 2 is used. Option 1 makes users press Back once for every line inspected
before they can return to the feature that opened Bank Matching. Removing line
IDs from the route entirely was also rejected because refreshes and direct
links should retain an explicitly selected transaction.

Implementation locations:

- target computation:
  `custom-addons/rebuild_account_migration/models/account_reconcile_compat.py`;
- list and transaction-form declarations:
  `custom-addons/rebuild_account_migration/views/rebuild_account_migration_views.xml`;
- shared OCA presentation variant:
  `custom-addons/rebuild_account_migration/static/src/js/reconcile_data_presentation.js`
  and `static/src/xml/reconcile_data_presentation.xml`;
- Bank Matching route ownership:
  `custom-addons/rebuild_account_migration/static/src/js/reconcile_navigation.js`;
- model and view regression:
  `custom-addons/rebuild_account_migration/tests/test_rebuild_account_migration.py`;
- click-propagation regression:
  `custom-addons/rebuild_account_migration/static/tests/transactions_navigation.test.js`.

## Deterministic regression gates

Run the narrow server-side test:

```bash
scripts/odoo-dev test-tag \
  '/rebuild_account_migration:TestRebuildAccountMigration.test_transactions_list_explains_match_residual_and_linked_entry'
```

Run the module's frontend unit tests:

```bash
scripts/odoo-dev test-js rebuild_account_migration
```

`bank_matching_navigation.test.js` protects both sides of the route contract:
automatic selection performs no history write, while later selection performs
one synchronous replacement of the current entry.

The frontend helper uses the repository's dedicated `test` image, which
provides Chromium and pins `websocket-client==1.9.0`. Both focused-test helpers
stop the normal web process to isolate the test transaction and restore the
healthy development service even when a suite fails.

The server test constructs a real reconciliation edge and proves both the
selected `account.move` and the view widget declaration. The frontend test
mounts a deterministic statement-line list, proves that the cell is an
`/odoo/account.move/<id>` link, clicks it, and requires
`account.move.get_formview_action`. A focused browser smoke is useful only when
Odoo changes the list, many-to-one or action-service APIs; it is not required
for every backend accounting change.
