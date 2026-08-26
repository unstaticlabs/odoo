# Production cut-over readiness register

## Purpose

This is the live human checklist for moving the USL Distribution from Odoo
Online to production. It complements, but does not replace, the executable
[portable-candidate runbook](portable-production-migration.md). A checked item
must point to retained evidence; confidence or a successful older rehearsal is
not evidence for the final frozen source.

The release is **not ready to cut over yet**. The current baseline and the
migration-performance work are not the final release until all remaining
feature branches have been reviewed and merged into `19-usl`, followed by a
fresh complete qualification.

### Current local rehearsal input

The package currently present at `/Users/roger/projects/odoo/usl-online-dump`
is a rehearsal input, not the final frozen source:

- `dump.sql` modified 23 August 2026, 118,560,508 bytes;
- dump SHA-256
  `0b9916db4807206f63b654bd2933ac89b0aab30ba7e0a1004edc4c060490238f`;
- package size approximately 403 MiB, including approximately 290 MiB of
  filestore and 2,029 filestore files.

The repository candidate status reports that no current production migration
candidate exists. This is expected before the final merge/freeze cycle. Recheck
the package structurally through the normal reconstruction source gates; the
size and digest observations above do not establish migration completeness.

On 26 August, the B2C-integrated branch completed a fresh full reconstruction
from this package and published a sanitized reusable QA seed. The finalized
`odoo_dev` target passed the database boundary for all thirteen delivered
product modules with no migration registry/schema residue. Accounting remained
balanced at EUR 2,900,936.82 debit and credit; B2C retained 304 canonical
orders, 457 source lines, 1,821 payment/refund/fee events, 261 fulfilments and
109 unresolved SKU aliases. Paperless archived 645 documents and synchronized
636 live authorized mappings. A separate empty-database installation passed
for all thirteen product modules, and two isolated full-seed hydrations matched
the sealed Accounting, Documents and Paperless controls with zero OCR
submissions. This is current rehearsal evidence, not a final frozen-source
production candidate.

## Non-negotiable boundaries

- Preserve all supported product data and every migration-critical unsupported
  source object in a native or explicit stable USL destination. No silent loss.
- Build the final candidate only from the exact source package captured after
  Odoo Online becomes read-only. A rehearsal candidate cannot be promoted.
- Keep Odoo Online read-only and retained as the migration reference after
  admission.
- Keep `USL_EINVOICE_LIVE_ENABLED=0` and
  `USL_EREPORTING_LIVE_ENABLED=0` through reconstruction, stage, gate,
  admission and initial stabilization.
- Do not import or copy passwords, sessions, API/OAuth/OIDC tokens, Pocket
  subjects, local identity audit events, Paperless integration tokens or
  environment secrets.
- Production consumes an immutable `distribution` image by digest. It does not
  run from a mutable checkout or a development bind mount.
- Pocket ID, ingress, firewall, network and host policies remain externally
  owned. The application deployment may join explicitly approved networks but
  must not create, restore, provision or mutate Pocket ID.
- Mail, bank ingestion, provider jobs, webhooks and other external side effects
  remain paused until their individual post-admission activation gate.

## Current release train

The following work must remain independently reviewable. Merge only approved
final state into `19-usl`; never qualify production from the feature worktree.

| Workstream | Required release outcome | Status on 26 August 2026 |
| --- | --- | --- |
| Migration performance and portable candidate | optimized reconstruction, sealed candidate, external-Pocket cut-over tooling, Distribution image | merged through `61580c1704c`; reusable full seed published from the integrated rehearsal |
| Expense Analytics | expense-batch analytics/product behavior and migration parity | merged through `aae5994a7ec` |
| B2C sales and inventory | canonical order/payment/refund/fulfilment/accounting/stock links and historical B2C parity | merged through `368812b2868`; clean full reconstruction passed, governed source gaps remain blocking |
| Paperless 3.0 | final Documents behavior, identity, export/import and full archive parity | active feature branch; review and merge pending |
| Native Sign | final signing workflow and retained evidence | active feature branch; review and merge pending |
| Monthly bank statement ingestion | idempotent statement ingestion from approved mail sources with visible failures | active workstream; review and merge pending |

For every merge:

1. record the reviewed commit and conflicts resolved;
2. run clean install, update and repeated update for affected modules;
3. run affected role, multi-company, browser and migration-boundary suites;
4. update module versions and source-to-target disposition coverage;
5. invalidate any QA seed, evidence or candidate produced by an earlier commit;
6. verify the complete target module set, not only modules touched by the merge.

The current database boundary expects the thirteen delivered modules listed by
`scripts/odoo/product_database_boundary.py`, including `usl_b2c`. Pending
features may extend that set. The script is authoritative after all merges; a
partial `odoo_dev` installation is not final-target evidence.

## Phase A — finish the release before freezing Online

- [x] Merge the approved migration-performance candidate into `19-usl`.
- [x] Merge Expense Analytics after independent review and validation.
- [x] Merge B2C sales/inventory after independent review and validation.
- [ ] Merge Paperless 3.0, Native Sign and monthly bank-statement ingestion
  after independent review and validation.
- [ ] Confirm no active release branch contains unmerged product or migration
  final state.
- [ ] Run static Python, JavaScript, shell, XML, Compose, manifest, French and
  migration-boundary checks from clean `19-usl`.
- [ ] Build the immutable GHCR `distribution` image, verify its revision/OCA
  labels and record the digest.
- [ ] Install every delivered product module into a fresh empty database; run
  update and repeated update without migration modules on the runtime path.
- [x] Run a fresh full local reconstruction from the most recent available
  Online dump and filestore while Online is still active. The 26 August
  rehearsal passed; it must be repeated after the remaining merges and again
  from the frozen source.
- [ ] Compare whole-source model/field dispositions, attachments, users,
  Projects, Accounting, Expenses, B2C, inventory, Paperless, Sign, payroll and
  Platform Billing against the source.
- [x] Record exact total and stage durations, including Accounting and full
  Paperless reconstruction, for the 26 August rehearsal. Repeat for the final
  candidate.
- [x] Publish one schema-compatible full-profile reusable QA seed from the main
  checkout, then hydrate it twice in separate projects with identical controls
  and zero OCR submissions. Completed for the 26 August rehearsal; repeat after
  the remaining merges and do not call this production evidence.
- [ ] Complete the browser matrix for administrator, manager, normal user,
  read-only accountant, multi-company isolation and each feature journey.
- [ ] Complete Accounting Manager acceptance and schedule the required
  professional accounting/FEC review.
- [ ] Rehearse preflight, stage, configure, gate, reset, restage and admit on an
  isolated host/tenant. Prove external Pocket state is unchanged.
- [ ] Resolve every data-loss-, accounting-, security-, privacy- and
  migration-critical discrepancy. No blanket waiver is permitted.

## Phase B — production inputs and owners

Record values in the private change record, never in Git.

| Input/decision | Required evidence | Owner | Ready |
| --- | --- | --- | --- |
| Release | exact Git commit, upstream base, OCA digest, module versions, immutable image digest | Technical Architect | [ ] |
| Source freeze | approved start time, user notice, write lock/read-only proof, source version and UTC timestamp | Product/Operations | [ ] |
| Final source | database dump and complete filestore, SHA-256 values, size/count controls, protected storage path | Migration lead | [ ] |
| Candidate fingerprint | independent verification of unchanged mode-0600 files in mode-0700 storage | Independent approver | [ ] |
| Production host | capacity, disk headroom, Docker/Compose versions, time sync, patch state and dedicated volumes | Infrastructure owner | [ ] |
| DNS/TLS/ingress | production HTTPS names, certificates, existing ingress network and activation/rollback owner | Infrastructure owner | [ ] |
| Pocket ID | existing issuer, separate Odoo/Paperless clients, redirect URIs and before/after read-only state hashes | Identity owner | [ ] |
| Secrets | non-default DB/master/app secrets and client secrets supplied from approved storage, all files mode 0600 | Security/Operations | [ ] |
| Identity policy | every Odoo/Paperless identity, companies, roles, object grants, break-glass decision and cron allowlist | Product/Security | [ ] |
| Backups | RPO/RTO, schedule, retention, separate failure domain, encryption/access, alerts and restore-test owner | Operations | [ ] |
| Email | outbound SMTP and inbound aliases, SPF/DKIM/DMARC ownership, catch-all/bounce policy and activation test | Operations | [ ] |
| Scheduled jobs | reviewed allowlist, cadence, company/timezone, idempotency, timeout/retry and failure owner | Product/Operations | [ ] |
| Bank email ingestion | approved mailbox/sender rules, duplicate identity, historical boundary and manual fallback | Accounting/Operations | [ ] |
| Monitoring | health/error/queue/disk/backup/mail/bank freshness alerts and destinations | Operations | [ ] |
| Decision window | named technical, accounting, infrastructure and final business go/no-go authorities | Change owner | [ ] |

Do not start the final freeze until all Phase A checks pass and every Phase B
row has an owner. Secrets themselves need not exist during early rehearsal,
but their creation, transfer and rotation procedure must be approved.

## Phase C — final freeze and candidate build

1. Announce downtime and make Odoo Online read-only. Record UTC time and who
   confirmed the freeze.
2. Capture a new complete database dump and filestore. Verify digests, archive
   safety, counts and readability before changing the target.
3. Rebuild from clean exact `19-usl` using a new isolated Compose project,
   full profile and production purpose. No Accounting resume and no Documents
   checkpoint reuse.
4. Require strict whole-source and attachment gates, accounting parity,
   migration finalization and all product controls to pass.
5. Build the sanitized Odoo/Paperless portable candidate with the exact image
   digest. Independently approve its fingerprint.
6. Transfer the unchanged candidate and private policy files over the approved
   SSH/storage route. Reverify on the production host.

If Online writes resume for any reason, discard the candidate and repeat this
phase from a new source package.

## Phase D — stage, configure, gate and admit

Run the commands and stop conditions in
[portable-production-migration.md](portable-production-migration.md):

1. `production-cutover preflight` against fresh dedicated application volumes;
2. `stage` Odoo, filestore and official Paperless export with OCR, cron, mail,
   providers and ingress paused;
3. `configure` from the mode-0600 external identity policy, with Pocket policy
   dry-run/apply/dry-run and no Pocket mutation API;
4. verify loopback/staging health and complete all required role/browser
   journeys;
5. `gate` release identity, complete product/migration boundary, source parity,
   Accounting, multi-company, Documents checksums/permissions and journeys;
6. take a coordinated pre-admission recovery point and prove its isolated
   restoration if the approved infrastructure backup implementation differs
   from the rehearsed one;
7. obtain recorded technical, accounting, infrastructure and final business
   go/no-go decisions;
8. `admit --confirm <fingerprint>`, which records admission, starts only the
   approved cron policy and permanently disables candidate reset;
9. have the infrastructure owner activate ingress. Do not use reset after
   admission; use coordinated backup/recovery.

## Phase E — controlled service activation

Admission means humans may use the validated application. It does not approve
all integrations.

| Capability | Initial state | Activation evidence |
| --- | --- | --- |
| Human Pocket login | enabled after ingress | admin and each governed persona pass; local break-glass policy verified |
| Odoo cron | allowlist only | every enabled job has owner, idempotency proof, last/next run visibility and failure alert |
| Backups | enabled immediately | first coordinated Odoo/Paperless backup succeeds and an isolated restore matches controls |
| Outbound mail | disabled | approved sender/domain, SMTP secret, test recipient, bounce/error visibility and no queue burst |
| Inbound mail/aliases | disabled | approved routing, attachment limits, duplicate behavior, company/record routing and rejection visibility |
| Monthly bank statement mail ingestion | disabled | approved source mailbox, checksum/reference duplicate test, Accounting review journey and manual fallback |
| Paperless mail/webhooks | disabled unless separately gated | identity, permissions, duplicate and failure/retry tests |
| Electronic-invoice reception | disabled | separate approved-platform reception activation runbook |
| E-reporting/transmission | disabled | separate legal/provider activation; never implied by reception |
| AI/agent writes | disabled | separately approved identity, least privilege, audit, retry and human-decision policy |

Before enabling outbound mail, inspect and intentionally clear or release any
mail queue reconstructed from Online. Before enabling any scheduled job, set a
safe `nextcall` where catch-up execution could create duplicate postings,
emails or provider calls. Never mass-enable all historical crons.

## Phase F — stabilization and exit

- [ ] Preserve the admitted fingerprint, release identity, source digests,
  evidence index and all go/no-go decisions.
- [ ] Monitor application errors, database/disk growth, workers, cron failures,
  Paperless queues, backup freshness, mail and bank-ingestion freshness.
- [ ] Compare key Accounting, B2C and inventory controls with the cut-over
  baseline daily during heightened support.
- [ ] Run first-day and first-week coordinated backup restores in isolation.
- [ ] Record and regression-test every production issue; avoid broad feature
  work during stabilization.
- [ ] Keep Odoo Online read-only until retention and professional-reference
  requirements are explicitly closed.
- [ ] End heightened support only after the first agreed accounting cycle and
  explicit owner acceptance.

## Go/no-go stop conditions

Stop or abort before admission for any unexplained source discrepancy, missing
attachment/original, accounting imbalance, wrong reconciliation, missing final
module, migration schema residue, release/image mismatch, unsafe archive,
identity/permission failure, non-empty or foreign target volume, altered Pocket
state, active external side effect, missing recoverable backup, or unowned
critical alert. Record the exact failure and restart from the last safe phase;
do not patch the production database ad hoc.
