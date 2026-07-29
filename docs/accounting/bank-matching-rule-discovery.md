# Bank Matching Rule discovery

## Product contract

**Accounting > Configuration > Bank Matching Rules > Find** gives an
Accounting Manager governed suggestions for recurring direct accounting
treatments. It extends native `account.reconcile.model` records and the
maintained OCA Bank Matching engine; it is not a parallel reconciliation
engine.

The finder is company-scoped and considers posted, reconciled bank
transactions from the preceding two years. A candidate requires at least three
transactions with the same normalized bank label, journal, counterpart account
and partner. It excludes bank and suspense accounts, receivable/payable
counterparts, tax lines, off-balance lines and transactions with an ambiguous
set of counterpart accounts.

At most twelve suggestions are created per run. An existing suggestion key or
an equivalent active or archived rule prevents duplication. A repeated run is
therefore safe and reports **No new suggestions** when nothing remains to
create. The suggestion key also has a database-enforced unique index so
concurrent UI or MCP requests cannot create parallel suggestions from the same
evidence.

## Safety boundary

A discovered suggestion is a native reconciliation-model record marked as a
proposal. OCA matching queries exclude proposals, so discovery does not:

- change or reconcile a bank transaction;
- create, post or alter a journal entry;
- activate automatic matching;
- replace an existing rule.

An Accounting Manager must inspect the source transactions and counterpart
entry, then explicitly approve or dismiss the suggestion. Approval removes the
proposal boundary and initially leaves the rule in manual-review mode.

Technical failures must surface as errors; they must not be reported as “no
suggestions.” The action is not available to Finance Operators or read-only
accountants.

## Implementation decision

Three approaches were considered:

1. require managers to discover every recurring pattern manually;
2. introduce a separate custom or probabilistic matching engine;
3. deterministically identify proven patterns and create inert native/OCA rule
   suggestions.

The third approach is retained. It saves review time without duplicating the
authoritative matching engine or allowing a score to authorize accounting
changes.

The public action remains a record-style Odoo method because a list-header
object button sends the selected record IDs as the first RPC argument, including
an empty list when nothing is selected. Regression coverage must invoke
`odoo.service.model.call_kw` with that real list-button argument shape. Direct
Python calls alone do not validate this contract.

The server regression in
`custom-addons/rebuild_account_migration/tests/test_rebuild_account_migration.py`
also protects the label and helper, manager access, deterministic result,
company assignment, sequential and database-level duplicate prevention,
no-result notification, proposal inertness and explicit approval.
