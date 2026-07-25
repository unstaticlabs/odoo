# SaaS 19.2 alignment register

Status: automated alignment and focused SaaS browser acceptance passed  
Source: `61f479cb44104bacfeaca927869bc3fd51c48285`  
Target: `8a44ecc8da96e341ac472fec27352d138ed2edd7`  
Target branch: `saas-19.2-usl-feat-accounting`  
Rollback branch: `archive/19-usl-feat-accounting-61f479c`

## Frozen inputs

The target was fetched and resolved before branch creation. No floating pull is
part of the implementation:

```text
$ git rev-parse refs/remotes/upstream/saas-19.2
8a44ecc8da96e341ac472fec27352d138ed2edd7

$ git log -1 --format='%H%n%cI%n%s' refs/remotes/upstream/saas-19.2
8a44ecc8da96e341ac472fec27352d138ed2edd7
2026-07-25T14:27:31+00:00
[FIX] l10n_fr_pdp: fixes ubl_21_fr format not being selectable when using pdp

$ git merge-base 61f479cb441 refs/remotes/upstream/saas-19.2
101027acc286596c956839deccab6a1b26e33e9d

$ git rev-list --left-right --count \
    61f479cb441...refs/remotes/upstream/saas-19.2
7867 10859
```

`odoo/release.py` at the target reports `('saas~19', 2, 0, FINAL, 0, '')`,
Python 3.12 minimum and PostgreSQL 16 minimum. The branch therefore uses the
same runtime generation as the source workflow while remaining an upstream
SaaS branch, not a separately supported Community stable series.

The USL final-state diff from
`b4f01111807a12977991d28acb3bf482bc05d248` modifies only `.gitignore`,
`CONTRIBUTING.md`, `README.md` and `requirements.txt` in the upstream tree. All
product runtime behavior is added under `custom-addons/`; no Odoo accounting
core file is replayed.

## Component decisions

| Component | Initial classification | Decision and evidence gate |
| --- | --- | --- |
| Docker, Compose and Dev Container | Minor adaptation | Retain Python 3.12/PostgreSQL 16, but use the isolated `usl-odoo-saas-19-2` project and SaaS-specific database names. Image build must pass from the target source. |
| Product and user documentation | Unchanged replay plus target annotation | Preserve the accepted product decisions. Mark historical Odoo 19 evidence as historical and record the exact SaaS target and rollback point. |
| `usl_bootstrap` | Minor adaptation | Keep only as a disposable fixture. Empty install on `odoo_saas_19_2_empty_01` is the evidence gate. |
| `rebuild_account_migration` manifests and module structure | Minor adaptation | Retain the isolated add-on. Manifest parsing is only a static gate; full dependency closure, install and tests are required. |
| Accounting models and source tracing | Minor adaptation | Retain source provenance and product fields. Validate every inherited model/field against the SaaS registry and run focused module tests. |
| Transactions cached metadata alias | Unchanged replay | Keep `rebuild_linked_document` in `models/account_reconcile_compat.py` until list/view and browser checks prove stale clients no longer request it. |
| Accounting Overview, navigation and roles | Minor adaptation | Retain the USL product shell. Validate inherited XML IDs, manager/reviewer ACLs and multi-company isolation on the SaaS registry. |
| Declarations, closing and hygiene | Minor adaptation | Retain because these are USL workflow decisions. Test lock dates, evidence protection and role transitions. |
| Interactive reports and PDF/XLSX | OCA compatibility work | Retain the canonical USL interactive reports. Keep only OCA engines still required after install and export tests; MIS remains retired. |
| FEC | Minor adaptation | Retain the standard `l10n_fr_account` wizard extension and USL evidence wrapper. Validate the inherited wizard and generated FEC. |
| OWL actions and controller patches | OCA compatibility work | Retain with explicit SaaS adaptations. Debug-assets browser validation found and corrected the renamed mail Chatter import and removed the obsolete `showButtons` view prop. |
| Reconstruction and native replay | Migration-only | Retain outside normal Accounting navigation. Rehearse only on explicitly disposable SaaS databases. |
| Exact 19.0 OCA pins | OCA compatibility work | Test exact pins before changing them. A parsed manifest or version edit is not compatibility evidence. |
| Private dumps and generated evidence | Retired from replay | Keep ignored. Never stage `usl-online-dump/`, `accounting_compat/private/`, generated private artifacts, `output/` or `tmp/`. |

## Alternatives and architectural choices

### Upstream integration

1. Merge the 19.0-derived branch into SaaS. This retains two divergent
   upstream histories and makes later upstream comparison difficult.
2. Replay the USL final state onto the exact SaaS commit.

Option 2 is selected. It preserves the functional source and rollback branch
without importing obsolete upstream 19.0 history.

### Accounting runtime

1. Copy Enterprise implementations observed in the source database.
2. Use SaaS Community standard behavior, maintained OCA modules and isolated
   USL extensions.

Option 2 remains selected. It minimizes provenance and upgrade risk. Enterprise
data and visible outcomes are parity references, not source code.

### OCA dependencies

1. Change every manifest version to appear SaaS-compatible.
2. Hold exact 19.0 pins, then require dependency, install, test, asset and
   workflow evidence for each retained module.

Option 2 is selected. Adaptations, if required, will be explicit pinned
integration commits.

### Container source execution

1. Install the checkout as a Python wheel with `pip install .`.
2. Install the pinned Python requirements and run the checkout's `odoo-bin`
   directly.

The first image build proved option 1 is not viable on this upstream commit:
modern `setuptools` rejects the upstream package version `saas~19.2` as
non-PEP-440. Option 2 is selected because it is the normal Odoo source-checkout
execution path and preserves upstream release metadata without a core patch.

## Database safety

This branch must not open or update:

- `odoo_dev`, the preserved Odoo 19 product database;
- `odoo_online_source_saas_19_2`, the read-only Enterprise source.

Allowed targets are:

- `odoo_saas_19_2_empty_01`;
- `odoo_saas_19_2_candidate_01`;
- `odoo_saas_19_2_validation_exact`;
- `odoo_saas_19_2_validation_native`.

The canonical `odoo_dev` name can move only after two clean reconstructions,
parity acceptance, and an explicit rollback checkpoint.

## Validation record

| Layer | Command or evidence | Result |
| --- | --- | --- |
| Source cleanliness | `git status --short --branch`; `git diff --check 61f479cb441` | Passed before fetch |
| OCA synchronization | `./scripts/sync-oca-addons` | Exact six repository pins checked out successfully |
| Python syntax | `python3 -m compileall -q custom-addons accounting_compat` | Passed |
| Ruff correctness checks | `ruff check --select E9,F63,F7,F82 custom-addons accounting_compat` in the development image | Passed; the unrestricted run reports 172 inherited/style-only findings and was not mass-formatted |
| Initial image build | `docker compose -p usl-odoo-saas-19-2 build odoo init-db devcontainer` | Failed at `pip install .`: `InvalidVersion: 'saas~19.2'`; Docker source execution adapted before retry |
| Adapted image build | same build command, with source-checkout execution | Passed; `odoo --version` reports `Odoo Server saas~19.2` |
| Clean dependency install | fresh `odoo_saas_19_2_empty_01`; `--init=rebuild_account_migration --without-demo=true` | Passed with all declared Community and OCA dependencies |
| USL module tests | `--update=rebuild_account_migration --test-tags=rebuild_account_migration_unit` | Passed: 99 tests |
| OCA reconciliation tests | `--update=account_reconcile_oca --test-tags=/account_reconcile_oca` | Passed: 47 tests after explicit SaaS patches |
| OCA browser assets | module update, then Bank Matching and Chatter in `debug=assets` | Passed with no browser warnings or errors after adapting `@mail/chatter/web_portal_project/chatter` and removing the obsolete `showButtons` prop |
| Harness tests | `python -m pytest accounting_compat/tests` in the development image | Passed: 10 tests |
| Source package | `scripts/accounting-compat source-validate` | Passed for dump SHA-256 `ee6d9789224a7a8ba1d9048c813939a41ffed77e13fad3b65be246cfc3f83c9e` and 1,762 filestore files |
| Source isolation | `source-restore`, `source-inspect`, `source-controls`, `failure-tests`, `extract` | Passed in the `usl-odoo-saas-19-2` project; source role is `NOLOGIN`, no Odoo server opened the source |
| Exact ORM reconstruction | `validation-exact-reset`, `validation-exact-import` | Completed on `odoo_saas_19_2_validation_exact` |
| Accounting parity | `validation-exact-validate` | Passed after adapting the control to SaaS `reconciled` payment state and the current dump identity |
| Lock control | rollback-only write and direct lock checks | Passed; global, tax, sales and purchase lock date `2025-09-30` blocks the protected entry |
| Second clean candidate | `dev-reset`, `dev-import`, `dev-validate` on `odoo_saas_19_2_candidate_01` | Passed; current closed slice is 2,694 moves, 6,319 lines and EUR 1,708,270.52 debit and credit |
| Native expenses and documents | `validation-native-expenses`, `validation-native-documents` | Passed: 325 expenses, 176 generated expense moves, 284 documents and their source attachments |
| Native assets and deferrals | `validation-native-assets`, `validation-native-deferrals` | Passed: 3 assets, 91 schedule lines, 28 depreciation moves; 5 schedules, 82 lines and 34 deferral moves in native scope |
| Native settlement and reconciliation | expense, document, general, categorization and external-bank replay stages | Passed; 97 company payments reconciled, 95 employee expenses paid, and all selected reconciliation edges preserved |
| Native analytics | `validation-native-analytics` | Passed for 632 analytic lines and their distributions |
| Reports and exports | `reports` | Passed: 51 implemented, 4 not applicable, 1 explicitly deferred; drill-down plus PDF/XLSX and role access probes passed |
| FEC | `fec`, structural preflight and official DGFiP source validator | Passed: 4,781 data rows and EUR 1,064,045.02 debit and credit |
| Focused manager browser | Overview, Journals, Transactions, linked entry, Bank Matching, Trial Balance and drill-down | Passed; the cached Transactions compatibility alias remains because the target view still exercises it |
| Focused reviewer browser | Overview, Transactions and Trial Balance/XLSX | Passed; Accounting Configuration and Match actions are absent while inspection and permitted exports remain available |
| Final readiness/evidence | `make accounting-readiness && make accounting-evidence` | `ready_with_documented_assumptions`; no P0 or technical failure, with the 75 cross-boundary records and chronology exceptions retained as advisory review items |

## Current source and parity baseline

The freshly validated source package is newer than the numeric checkpoint
quoted in the initial execution request. Acceptance therefore uses the
source-derived controls keyed by dump SHA-256, not stale lower counts. The
current source contains 5,044 moves across both companies: 4,849 posted, 193
draft and 2 cancelled. Exact replay imports the 4,849 posted moves and 11,404
accounting lines; the one posted display-only note remains explicit review
evidence rather than a fabricated accounting line.

The passed broad controls include:

- 97 move-backed payments plus 13 no-entry payment review records;
- 3,046 bank statement lines;
- 2,531 fully-contained partial and 1,210 fully-contained full
  reconciliations, plus the preserved 39 partial and 36 full cross-boundary
  review records;
- 1,889 historical currency rates;
- 632 analytic lines;
- 3 assets, 91 depreciation schedule lines and 28 imported depreciation moves;
- 110 deferred lines and 37 posted deferral entries;
- 332 accounting attachments and 224 source-designated main attachments.

The closed benchmark slice currently present in this source dump is 2,046
posted moves, 4,809 accounting lines, and EUR 1,064,045.02 debit and credit.
Source and target account, journal, tax, report-structure, reconciliation,
attachment, analytic, asset, deferral and chronology comparisons all have zero
unexplained differences. The separately approved EUR 942 DGFiP VAT-refund
reclassification is the only normalized bank-line difference and its
source-traced correction control passes.

The first failed target control was useful evidence rather than a waived
failure. It showed that the old harness expected `account.payment.state =
paid`; SaaS 19.2 correctly computes the matched payments as `reconciled`.
The control and importer now preserve that SaaS state. The same run exposed a
hardcoded prior snapshot ID in the lock probe; all snapshot-dependent probes
now derive `source-<first 12 dump SHA characters>` from the selected package.

Native expense replay required two deliberate migration-only adaptations.
Receipts are materialized before the standard submit/approve workflow, while
the `rebuild_source_materialization` context preserves source-approved records
whose historical receipt was not retained without weakening normal UI policy.
The source category price is frozen during draft recomputation so a current
product cost cannot replace the historical expense price.

Foreign-currency expense settlements also preserve the source journal entry's
historical company-currency liquidity balance before the OCA matcher runs.
This is done through balanced ORM writes, not direct SQL mutation. SaaS then
uses its terminal `reconciled` payment state. Bank imports containing source
sub-cent values are compared at the journal currency's display precision;
784 such normalizations are recorded, while the debit, credit and company
currency ledger controls remain exact.

The focused browser pass exposed two OCA client incompatibilities that normal
production asset bundling did not make sufficiently visible: the mail Chatter
module moved from `web_portal` to `web_portal_project`, and OWL now rejects the
obsolete `showButtons` view property. Both are fixed in the pinned integration
source and passed a clean debug-assets reload of the reconciliation detail and
Chatter tab.

The alignment implementation gates are now complete. The result remains an
internal development candidate, not authorization to replace `odoo_dev` or
the Odoo 19 product. The preserved source anomaly review, the 75
cross-boundary reconciliation decisions and professional accountant
acceptance remain required before any production promotion.
