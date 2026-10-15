# Odoo upstream synchronization, 15 October 2026

This integration brings upstream `saas-19.3` from
`3f00ca77980bbfdb81fa9ab1f7d0bb99486f5dd9` to
`8a5848530ac47edbdc28ae93f53b29b97b47b031`: 672 upstream commits,
1,463 changed files. It retains the feature batch delivered through PR #288
at staging `07a29f33fd93228c44a41b5bba70cde234fb33b1`.
The upstream merge is literal and preserves ancestry; compatibility adaptations
and qualification metadata remain separate commits. There were no textual
upstream merge conflicts. This is not evidence of runtime compatibility by itself.

## Distribution overlap decisions

Three of the 29 core paths changed by the distribution also changed upstream:

- `addons/account/models/account_move.py`: keep the distribution fiscal-year
  sequence calculation based on `compute_fiscalyear_dates`. Upstream changes
  affect other accounting paths and do not replace that behavior.
- `addons/account/tests/common.py`: keep the explicit optional irreversible
  capability in the test-company fixture; retain the upstream database statistics
  improvement. Neither substitutes for the other.
- `addons/web/static/src/search/search_model.js`: keep action-bound URL state
  and the once-encoded query behavior. Adopt the independent upstream fallback
  for missing default generator IDs.

All 124 installed native/core module manifests were compared. No installed module
was removed and no dependency list changed. Account advances from 1.5 to 1.6;
the other manifest changes affect French PDP description and Purchase/MRP assets.
The upstream merge changes neither the distribution requirements nor constraints.
No distribution customization was removed without a native-parity proof.

## Qualification selected from the actual changes

The change surface includes ORM domain context and relational joins, prefetch
access recovery, cache/registry signaling, attachment reuse authority, accounting
partial reconciliation and matching, taxes and locks, expense manager authority,
Project portal/collaborator rules and French PDP lifecycle/downpayment/eco-charge
behavior. Qualification therefore uses clean exact-module installation, a
predecessor upgrade with preserved ledger and filestore evidence, repeat upgrade,
real runtime action discovery, affected native regressions, distribution tests,
desktop/mobile browser tests, assets, French catalogs and the product boundary.
External e-invoicing/e-reporting remain disabled; provider cases use offline mocks.

The private QA receipts record exact source and test commands/results. A restored
synthetic predecessor database contains posted accounting entries, partial and
full reconciliations and attachment files. Synthetic fixture recovery is described
as such; it is not presented as a recent production-backup rehearsal. The final
PR states separately whether that additional restore path was run.

## Compatibility corrections

The combined tests exposed three changes that require distribution compatibility
work rather than accepting a conflict-free upstream merge:

- French CA3 expressions now include `if_below(EUR(0))`. The distribution evaluator
  supports that exact negative-part operation while retaining Decimal arithmetic,
  half-up rounding and rejection of unsupported expressions. This is an isolated
  custom-add-on change; its version advances to 1.13.30.
- The native French UBL importer previously found downpayments by reference alone.
  It must match company, commercial supplier, document currency and eligible
  supplier document type/state, and validate duplicate financial evidence before
  creating any deduction. Its final phase creates lines on the destination draft
  invoice, preserving the source invoice and reconciliation history. The tests
  exercise both collected values before identity is written and the real import.
  Financial identity also includes manual-tax JSON and analytic allocations.
  Negative invoice deductions use the native manual-tax reversal helper; a
  custom tax amount of 17 stays 17 rather than becoming the ordinary rate of 20.
  Conflicting manual taxes or allocations require review before any line is copied.
- Native manufacturing end-date checks previously treated any supplied `state`
  value as initial completion. A completed order could consequently acquire a new
  production/stock date while its posted entries kept their original date. An
  unconditionally stricter check also rejected genuine initial completion because
  stock completion computes the state first. A private process-local completion
  scope distinguishes the native first-completion pipeline from ordinary edits;
  it cannot be supplied as RPC context and is reset on every exit.

The two native fixes remain small, separate compatibility patches after the
literal upstream merge. They are distribution-level corrections to upstream
financial behavior rather than new product abstractions. On the next upstream
sync, compare these exact importer and manufacturing methods with the upstream
fix and remove a patch only after its native regression tests pass without it.
The associated action inventory must be rediscovered after source changes.

A predecessor fixture initially failed preservation on the generated company CSS
attachment. Old-source `--update=all` reproduced exactly the same checksum/size/
file change: synthetic companies had been created without regenerating that
singleton. The original failed receipt is retained. Qualification settles that
fixture with the old source before capturing the fresh predecessor baseline;
it does not exclude generated rows or relax the preservation contract. A real
installation with similarly stale CSS can still encounter that refusal and needs
an explicit baseline/upgrade decision.

## Dependency decisions

No Dependabot update is absorbed in this upstream-only integration. The open
updates remain individually reviewable:

- #265: upload-artifact patch; a workflow maintenance change that can qualify
  separately without changing the application synchronization.
- #264 and #263: grouped Python dependency updates need their own exact runtime
  and compatibility evidence; they are not required by this upstream range.
- #261: Jackson update in the separate DSS signing service needs that service's
  qualification and is not an Odoo source compatibility adaptation.
- #257: Impeccable tooling update changes contributor tooling, not upstream Odoo
  behavior; it remains separate.
- #249: grouped service image updates require cross-service qualification and
  remain separate from this source integration.

Existing deliberate pins remain. No dependency PR is closed or described as
absorbed by this synchronization. No unrelated product backlog is resumed.
The target is staging; no production promotion or manual deployment is included.

## Exact action review

Runtime discovery includes all 164 delivered modules, 48,344 runtime entries and
56,937 combined entries. The review covers 608 material keys: 61 additions,
17 removals and 530 changed implementation fingerprints. Source-location-only
changes retain their existing classification; no wildcard or method-name rule
approves future changes. All changed source fingerprints were checked again after
the final compatibility corrections before refreshing the tracked surface.

The review corrects old descriptions that understated effects or promised an
unproven inverse. Removing a reconciliation is operational accounting work;
initializing backend user settings, editor collaboration notifications, generated
image copies and IAP content requests also have effects. Server-action run and
breadcrumb routes transport to the exact server-action delegate. New journal-item
navigation is read-only. Private implementation sinks retain their parent's
native access checks. Product, warehouse, analytic, mail and project workflows
keep native actor/company checks; no general capability or new human gate is added.

## Qualification record

The final compatibility selection passes 28 database cases, including all 15
French advance-import cases. Separately, 29 native ORM/base cases and 183 affected
native cases pass; three optional native cases are skipped explicitly. The native
HTML/Web selection passes 15 cases. Desktop/mobile HOOT passes 199/182 tests,
with 1,416/1,338 assertions. The two native bank/expense journeys pass serially
after warming assets.

The initial integrated run retained four errors among 903 custom tests: two CA3
errors corrected and rerun with all six tax-report tests passing, and two browser
navigation timeouts rerun serially with both journeys passing. The first combined
native suite also exposed expected fixture conflicts with distribution renderer,
receipt and provider contracts; the affected upstream behavior was qualified in
a separate native closure and through integrated probes. These initial failures
remain in the private receipts. Infrastructure-aborted runs are retained and are
not counted as passing tests.
