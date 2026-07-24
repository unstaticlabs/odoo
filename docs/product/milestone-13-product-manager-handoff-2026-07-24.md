# Milestone 13 Accounting v1: Product Manager handoff

Date: 2026-07-24

Audience: Product Manager, Valentin, Prosper / USL accountant, finance
operations and release owner.

Branch: `19-usl-feat-accounting`

Latest technical checkpoint: `0e92eab30c8`
(`feat(accounting): add closing snapshots and monthly trend`)

## Executive synthesis

Milestone 13 is technically ready for professional acceptance, but it is not
approved for production replacement.

The implementation now reconstructs the Odoo Online accounting source into
Odoo Community 19, preserves the locked historical ledger, proves native
current-period workflows, exposes accounting and review workbenches, generates
the required reports and FEC evidence, and enforces scoped manager/accountant
roles. The automated readiness assessment reports:

```text
TECHNICAL_REHEARSAL_PASSED_PROFESSIONAL_ACCEPTANCE_PENDING
```

There are no technical failures and no capability rows classified as discovery
or missing. The remaining block is decision authority, not an identified
software implementation gap:

- one P0 professional report/statutory acceptance gate;
- one P1 reconciliation-scope policy gate;
- one accountant-owned P2 historical-sequence explanation gate;
- 45 prepared but unrecorded professional decisions;
- no recorded final acceptance or hybrid-candidate promotion.

The Product Manager should therefore treat the next phase as a structured
acceptance and release-decision exercise. It should not be reopened as an
unbounded engineering phase unless a reviewer records a concrete objection or
requests a changed accounting outcome.

## Product outcome delivered

### Historical truth

The exact imported benchmark for Unstatic Labs preserves:

- `2,046` posted moves;
- `4,809` journal items;
- EUR `1,064,045.02` debit and EUR `1,064,045.02` credit;
- exact entry references, dates, journals, sequence prefixes and sequence
  numbers;
- `663` full reconciliations and `1,563` partial reconciliations within scope;
- `332` scoped accounting attachments;
- `1,877` historical EUR, USD and GBP currency rates;
- `3` fixed assets and `91` depreciation-schedule rows;
- `110` deferred-schedule evidence rows;
- source tax, analytic, payment, bank, report-structure and reconciliation-rule
  evidence.

All 31 source/target validation groups pass with no missing, extra or mismatched
records and no duplicate source traces. Posted history remains protected by
standard Odoo lock enforcement.

One confirmed source correction is deliberately represented instead of hidden:
the EUR `942` DGFiP VAT refund misclassification is reclassified through one
balanced, source-traced journal entry and native reconciliation. The source
bank entry remains intact.

### Native current-period operation

The separate Track B proof covers 1 October 2025 through 30 June 2026 using
normal Community/OCA workflows rather than cloning posted Enterprise effects:

- all `325` source expenses represented through native expense workflows;
- all `284` source commercial documents represented through native invoice,
  bill and refund workflows;
- expense and document settlement through native/OCA reconciliation;
- all `1,841` relevant bank transactions represented;
- native General Reconciliation for non-bank clearing cases;
- native asset and deferral operation;
- multi-plan analytic allocations and corrections;
- rerun/idempotence evidence for each material posting stage.

Draft and post-cutoff source records remain drafts or review boundaries. They
were not posted merely to manufacture parity.

### Hybrid replacement candidate

The hybrid candidate combines:

1. exact locked benchmark history; and
2. native Track B current-period workflows.

Historical parity remains exact. The combined candidate has `4,541` posted
moves and `10,727` posted journal items with no unbalanced moves or duplicate
source identities.

Current-period differences are classified as native cash-basis
timing/aggregation, native exchange timing/aggregation or OCA bank-allocation
segmentation. The 12 account differences net to EUR `0.00`; a EUR `2.64`
profit-and-loss timing difference remains a professional acceptance point. It
is explained technical evidence, not an unexplained defect.

The hybrid candidate has passed manager/reviewer browser journeys. It has not
been promoted as the production replacement.

### Product experience

The Community Accounting app now exposes a coherent workflow for normal users:

- Accounting Home;
- Accounting Hygiene;
- customer, vendor, expense and journal workspaces;
- Bank Matching;
- General Reconciliation;
- Matched Items and native Undo;
- one canonical Accounting Report Workbench;
- declarations and deadline workspaces;
- month, quarter and annual closing workspaces;
- FEC generation;
- fixed assets, depreciation and deferrals;
- analytic reporting;
- currency-rate automation;
- advanced source/parity evidence for audit users.

Accounting Managers receive operational and configuration controls. The
single-company accountant reviewer receives scoped read, export, evidence and
immutable-decision access without accounting mutation, settings or unrelated
company/private-record exposure.

### Reports and analytics

All `38` active source report families have an explicit Community target or
scope decision and a Level 4 technical evidence package. None lacks a target
equivalent.

The generated capability matrix contains `56` rows:

| Status | Count | Product meaning |
| --- | ---: | --- |
| Implemented | 12 | Native or original Community capability is operational. |
| Partial | 39 | Technical evidence exists; professional acceptance remains. |
| Not applicable | 4 | Source/legal scope does not require a target feature. |
| Deferred | 1 | Electronic declaration submission is outside Accounting v1. |

The newly completed revenue-versus-spending trend derives monthly values from
posted native journal items and provides graph, pivot, list/export and
journal-item drill-down. For October 2025 through June 2026 it validates:

- `27` normalized metric rows;
- EUR `176,928.45` revenue;
- EUR `101,215.69` spending;
- EUR `75,712.76` net contribution.

### Closing integrity

Closing decisions now have a durable acceptance boundary:

- an accepted decision requires a generated XLSX or PDF closing package;
- the accepted file bytes, SHA-256, file size, package reference, conclusion,
  decision summary, evidence, reviewer and review time are copied into an
  immutable snapshot;
- the current accepted package cannot be changed, reassigned or deleted;
- only one closing decision may be current and recorded;
- superseding that decision starts a new review cycle and unlocks the working
  package;
- old snapshots cannot authorize lock dates;
- standard lock dates cannot advance until an immutable snapshot exists for the
  current recorded acceptance.

The exact target correctly contains zero accepted snapshots because no named
professional has yet recorded a real closing acceptance. Engineering did not
fabricate this evidence.

## Technical evidence summary

The final technical checkpoint passed:

- the full `85`-test `rebuild_account_migration` add-on suite;
- the focused `5`-test declaration/closing suite;
- exact-target module updates;
- all 31 exact-target validation groups;
- report generation and drill-down controls;
- the `56`-row capability matrix with zero technical gaps;
- report, manager, reviewer, FEC and reconciliation browser journeys;
- FEC structural preflight and official DGFiP source-validator route;
- target import idempotence and failure-injection guardrails;
- Python compilation and XML parsing;
- user-documentation MkDocs build;
- final readiness and evidence-index generation.

The final readiness artifact records:

- `0` technical failures;
- `1` P0 discrepancy;
- `1` P1 discrepancy;
- `1` P2 discrepancy;
- `45` draft decisions;
- `0` recorded professional decisions.

Intermediate validation failures were used to improve the product:

- a report drill-down domain initially returned non-JSON-serializable date
  objects; it was corrected and the report harness passed;
- early closing-package tests exposed weak test assumptions and were corrected;
- a focused lifecycle test exposed simultaneous recorded closing decisions;
  the model now enforces one current recorded cycle;
- final review exposed possible mutation of the original accepted attachment;
  the accepted file is now locked while its decision remains current.

Odoo emits two non-fatal reStructuredText parser warnings while loading existing
module help text. They do not fail installation or tests and are not a release
blocker. A whole-file Ruff run also reports pre-existing style debt in the
large migration harness; Ruff has not been represented as a passing gate.

## Current operational picture

The imported exact target currently reports for Unstatic Labs:

- `4,840` posted moves and `11,386` journal items through the source snapshot;
- `31` journals;
- `3,037` bank transactions;
- `207` unmatched bank transactions;
- `37` draft vendor documents, all stale and missing their main attachment;
- `74` open payable items totalling EUR `31,749.82`;
- `7` unusual-balance controls totalling EUR `50,860.26`;
- `15` overdue declaration tasks and `1` upcoming task;
- latest closing through 30 June 2026: `4` blockers and `7` warnings;
- `355` Accounting Hygiene attention items;
- `45` accountant actions and `2` Valentin actions.

These figures are operating queues, not all migration defects. The PM should
separate:

- acceptance blockers, which prevent release;
- normal accounting work, which continues after release;
- stale source drafts, which require an explicit keep/exclude/complete
  decision;
- technical evidence, which is already complete.

## Release blockers and required decisions

### B1 — P0 report and statutory acceptance

Status: blocked.

Facts:

- `38/38` active source reports have Level 4 technical evidence;
- `0/38` have recorded accountant acceptance;
- no active source report lacks a target equivalent;
- `36` report-parity decisions and `2` scope-exclusion decisions are prepared
  in Odoo.

Decision required:

- accept;
- accept with a documented difference/risk;
- require a specific change; or
- reject.

Named owner: Prosper / USL accountant for accounting and statutory semantics;
Valentin for business usefulness and the two deliberate association-report
scope exclusions.

PM recommendation: time-box a report acceptance session around the 23 mandatory
reports first. Do not accept “needs more work” without a report key, expected
value/presentation, legal basis and severity.

Exit evidence: every mandatory report and scope exclusion has a recorded
decision, reviewer identity, evidence summary and remaining risk where
applicable.

### B2 — P1 cross-boundary reconciliation policy

Status: blocked.

Facts:

- `75` source reconciliation relationships cross the exact posted-history
  boundary;
- their missing endpoints are drafts, future records or otherwise outside the
  exact posted baseline;
- manager and reviewer can inspect balanced endpoint previews and record
  decisions;
- a rollback-only native reconciliation probe proves the technical mechanism;
- the exact historical baseline has not been mutated.

Decision required for each class of endpoint:

1. import/include it in an authorized broader scope;
2. leave it as a review-only boundary;
3. explicitly exclude it with an accounting rationale; or
4. request a controlled application workflow after the underlying draft is
   approved.

Named owner: Prosper / USL accountant, with Valentin approving any scope
expansion that changes the replacement boundary.

PM recommendation: preserve review-only treatment as the default. Expand scope
only where a specific business document is expected to become authoritative.
Do not bulk-apply draft-endpoint reconciliations merely to remove the P1.

Exit evidence: the 75 relationships have a recorded policy/classification, and
any authorized application has separate before/after evidence.

### B3 — P2 historical sequence and chronology anomalies

Status: investigating.

Facts:

- locked benchmark: `2` sequence gaps and `3` sequence-ordered date decreases;
- full source snapshot: `16` gaps and `104` date-order decreases;
- source and target profiles match exactly;
- there are no target-only anomalies, blank references or duplicate sequence
  numbers.

Decision required:

- accept these as faithfully preserved source history with an explanation; or
- require an accountant-directed correction process outside the locked import.

Named owner: Prosper / USL accountant.

PM recommendation: accept preserved history unless the accountant identifies a
legal defect requiring a separately authorized correction. Do not resequence
posted imported history.

Exit evidence: one recorded accountant decision naming the cause/acceptable
treatment and confirming that no target resequencing is required.

### B4 — FEC professional sign-off

Status: technically passed, professionally pending.

Facts:

- FEC generation passes;
- local structural preflight passes;
- the official DGFiP source-validator route passes;
- manager, accountant reviewer and finance operator download journeys pass;
- one FEC validation decision remains draft.

Decision required: accountant approval of the FEC dossier for the benchmark
period, or a precise objection.

Named owner: Prosper / USL accountant.

Exit evidence: recorded FEC acceptance with the reviewed file/hash and any
remaining caveat.

### B5 — French tax and external values

Status: technically represented, professionally pending.

Facts:

- annual statements, 2065/2033 and CA12 mapping evidence exists;
- fixed assets reconcile at EUR `10,430.49` gross, EUR `1,676.05`
  depreciation and EUR `8,754.44` net;
- two external-value decisions remain draft;
- reduced-rate IS eligibility, reintegrations, deductions, deficits and final
  filing-box judgment remain professional matters;
- no electronic filing client is claimed.

Decision required: accept the mapped values and external evidence, correct them
with an authoritative source, or identify the exact missing fact.

Named owner: Prosper / USL accountant; Valentin supplies administrative facts
where the ledger cannot.

Exit evidence: the two external-value decisions and applicable statutory
report decisions are recorded.

### B6 — Named-user acceptance and candidate promotion

Status: not executed.

Facts:

- automated ACL and browser journeys pass;
- a complete Prosper acceptance walkthrough exists;
- the hybrid candidate has not been promoted;
- one milestone-closure decision remains draft.

Decision required:

- Prosper confirms the accountant workflow and access boundary;
- Valentin confirms the CEO/manager workflow and operating queues;
- the release owner promotes or rejects the exact/hybrid candidate explicitly.

Named owners: Prosper, Valentin and the Product Manager/release owner.

PM recommendation: select the hybrid candidate only if the EUR `2.64`
current-period timing difference and native workflow representations are
professionally accepted. Retain the exact imported target as immutable audit
baseline regardless of promotion.

Exit evidence: completed walkthrough, recorded milestone decision, named
candidate, database identity, source snapshot/hash and rollback plan.

## Product Manager open questions

The following questions need explicit answers. Silence must not be interpreted
as acceptance.

### Scope and release

1. Is the intended production candidate the hybrid replacement, while the exact
   imported target remains the audit baseline?
2. Is professional acceptance of the EUR `2.64` native exchange-timing
   difference sufficient for hybrid promotion?
3. Must every P2 sequence exception be individually annotated, or is one
   evidence-backed accountant policy decision acceptable?
4. Are the `37` stale draft vendor documents required production work, accepted
   backlog, or explicit exclusions?
5. Is production replacement allowed with normal operating queues—unmatched
   bank transactions, open payables and declaration tasks—provided all release
   acceptance gates are closed?

### Reports and statutory outputs

6. Which of the 23 mandatory report families require pixel/presentation changes
   rather than semantic acceptance of the current Community workbench?
7. Are the two association report families accepted as out of scope for the
   USL SASU?
8. Does the accountant accept the PCG 2024 variant treatment and current
   French-statement mappings?
9. Which final tax values require external documentary evidence beyond the
   ledger-derived package?
10. Is electronic declaration submission definitively deferred beyond
    Accounting v1?

### Reconciliation

11. Should draft/future reconciliation endpoints stay review-only by default?
12. Which, if any, of the 75 boundary relationships must be applied before
    production?
13. Who has authority to approve posting or including a currently draft
    endpoint?
14. Is a class-level decision acceptable for homogeneous boundary cases, or
    must every relationship receive an individual decision?

### Access and operations

15. Does Prosper accept the single-company read/export/decision role as the
    final accountant access model?
16. Who owns the 45 prepared decision records and by what date?
17. Who owns the 15 overdue declaration tasks, 207 unmatched bank transactions
    and 37 missing vendor-document attachments after release?
18. What production policy should replace the development default that disables
    cron jobs and outbound/inbound mail side effects?
19. Who performs the real inbound bill/expense email delivery smoke test after
    domain, DNS/provider routing and incoming mail server configuration?

### Go-live governance

20. What is the acceptance meeting date and who is required to attend?
21. Who has final authority to promote the candidate?
22. What is the production cutover date and accounting freeze window?
23. What is the rollback owner and maximum acceptable rollback time?
24. Which evidence package must be archived outside the disposable environment
    at go-live?

## Recommended PM decision plan

### Session 1 — scope and candidate, 30 minutes

Participants: Product Manager, Valentin, technical owner.

Decide:

- exact target versus hybrid candidate roles;
- whether the EUR `2.64` difference is an acceptance item or change request;
- treatment of stale drafts;
- acceptance calendar and named owners.

Output: written candidate statement and assigned decision queue.

### Session 2 — accountant acceptance, 90–120 minutes

Participants: Prosper, Valentin, Product Manager, technical owner available for
evidence navigation.

Review in this order:

1. Trial Balance, General Ledger and locked history;
2. Balance Sheet, Profit and Loss, annual statements, SIG/CAF;
3. VAT/CA12 and tax-package mappings;
4. FEC dossier;
5. reconciliation boundary classes;
6. sequence/chronology evidence;
7. closing package and immutable acceptance snapshot.

Record decisions in Odoo during the session. Do not rely on meeting notes as a
substitute for durable decision records.

### Session 3 — named-user and release rehearsal, 60 minutes

Participants: Prosper, Valentin, Product Manager/release owner.

Execute:

- Prosper acceptance walkthrough;
- Valentin Accounting Home and action-queue review;
- candidate identity and source hash confirmation;
- backup/rollback confirmation;
- final milestone and promotion decision.

## Definition of done for Product acceptance

Milestone 13 can be closed only when:

- P0 and P1 are resolved or formally accepted;
- the accountant-owned P2 has a recorded explanation/acceptance;
- all mandatory report and statutory decisions are recorded;
- FEC acceptance is recorded;
- the two external-value decisions are recorded;
- Prosper and Valentin complete their named-user walkthroughs;
- the selected candidate is explicitly identified and promoted;
- the accepted closing package is captured as immutable snapshot evidence;
- production environment, cron/mail/network and rollback policies are approved;
- the readiness assessment is regenerated with no open release blocker;
- the milestone-closure decision is recorded.

Passing tests, an empty technical-failure list or a successful module update
does not independently satisfy this definition.

## PM recommendation

Move Milestone 13 to **Professional acceptance / release decision**, not back to
**Engineering implementation**.

The technical product is sufficiently complete to expose the remaining
judgments accurately. Reopening engineering without a concrete professional
objection risks replacing accountable decisions with more software and could
damage the preserved historical boundary.

The recommended default decisions are:

- retain the exact imported database as immutable audit evidence;
- use the hybrid database as the production candidate;
- accept explained native timing/segmentation differences only after Prosper
  reviews them;
- leave draft/future reconciliation endpoints review-only unless individually
  authorized;
- preserve historical sequence anomalies without resequencing;
- keep electronic filing outside Accounting v1;
- require real recorded package acceptance before any final lock/promotion.

## Source documents and evidence

Public project documents:

- [Accounting core](accounting-core.md)
- [Milestone 13 current progress report](../accounting/milestone-13-current-progress-report.md)
- [Milestone 13 checkpoint, 2026-07-23](../accounting/milestone-13-checkpoint-2026-07-23.md)
- [Configuration capability matrix](../accounting/milestone-13-configuration-capability-matrix.md)
- [Declaration and closing workflow](../accounting/milestone-13-declaration-closing-workflow.md)
- [Accounting compatibility harness](../accounting/accounting-compat-harness.md)
- [Screenshot and journey matrix](../accounting/milestone-13-screenshot-parity-matrix.md)
- [Prosper acceptance walkthrough](../users/tutorials/prosper-accounting-acceptance.md)
- [Deployment runbook](../operations/deployment-runbook.md)
- [Backup and recovery runbook](../operations/backup-and-recovery-runbook.md)

Private generated evidence remains under
`artifacts/accounting-compat/private/` and must not be committed. The controlling
readiness files are:

- `readiness-assessment.json`;
- `readiness-assessment.md`;
- `reports-status.json`;
- `parity-matrix-v1.json`;
- `target-validate-status.json`;
- `replacement-validate-status.json`;
- `fec-validation-status.json`;
- `evidence-index.json`.
