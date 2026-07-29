# Configurable Accounting Controls

## Purpose

Accounting Controls are the shared, company-scoped policy catalogue behind
Accounting Hygiene and Closing readiness. They make every active accounting
condition discoverable without turning business configuration into executable
code.

The catalogue is stored in
`rebuild.account.closing.control.definition`. The technical name is retained to
avoid a migration-sensitive model rename; the product name is **Accounting
Controls**.

## Architecture

Each definition separates:

- business purpose, accounting consequence and expected resolution;
- use in daily Hygiene, Closing, or both;
- Closing period scope;
- responsible role and accountant visibility;
- dynamic, informational, advisory or blocking impact;
- standard/OCA/USL/company-specific origin;
- an installed, whitelisted evaluator key and technical boundary.

Evaluators are Python extension points registered by installed modules. The
configuration screen does not execute arbitrary Python. Accounting Managers can
govern business policy; only Technical Administrators see evaluator details.
Editing business policy changes the origin to **Company-specific**.

The Control form provides one canonical business-purpose surface: **What this
checks**, **Why it matters** and **Expected resolution**. These values are
mirrored into the framework snapshot fields when edited; duplicate technical
purpose fields are deliberately not exposed in the normal form.

The initial USL implementation groups the deterministic Hygiene checks in one
evaluator and retains one evaluator per Closing check. This preserves the tested
accounting queries while removing the hidden execution list. A maintained OCA
or standard Odoo module can later register an evaluator without changing the
business-facing result contract.

## Result contract

Closing results are refreshed for a workspace and retain their definition,
status, responsible role, evidence summary and next action. Hygiene results
retain their definition, source links, first and last detection, resolution and
dismissal history.

Dismissal acknowledges one detected occurrence; it never disables its Control.
The occurrence fingerprint uses the Control version, company-scoped related
record IDs, affected amount, detection date, result kind and severity rather
than mutable display text. An unchanged population therefore stays dismissed,
while a new related record or another material evidence change reopens the
result. Each dismissal keeps its user, timestamp, evidence snapshot and the
time at which later evidence superseded it.

Accounting outcomes and technical failures are distinct:

- an accounting result reports a condition found by a completed evaluator;
- a technical failure reports that no accounting conclusion was produced;
- a technical failure prevents a false Ready result;
- informational results remain visible but do not create Closing warnings;
- changing policy never posts, reconciles, changes declarations or applies
  lock dates.

Disabling a definition resolves its currently open Hygiene result on the next
refresh and removes its Closing result from the next workspace refresh. History
remains available.

These lifecycle actions are deliberately distinct:

- resolving means the underlying accounting condition is no longer detected;
- dismissing hides only the reviewed occurrence while unchanged;
- disabling is an Accounting Manager configuration decision that prevents the
  Control from running in its configured workflows.

## Extension rules

Prefer standard Odoo behavior or maintained OCA controls when their semantics
match the product requirement. A new code-backed control must:

1. register a stable evaluator key rather than expose arbitrary code;
2. read within the active company and declared date scope;
3. return the shared result contract;
4. avoid posting or mutating ledger data while evaluating;
5. include focused tests for accounting outcome, technical failure and policy
   mapping;
6. document assumptions and recommended resolution in the catalogue seed.

No migration or compatibility model belongs in the normal Accounting menu.
