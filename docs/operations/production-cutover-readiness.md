# Production cut-over readiness register

## Purpose

This is the live human checklist for moving the USL Distribution from Odoo
Online to production. It complements, but does not replace, the executable
[portable-candidate runbook](portable-production-migration.md). A checked item
must point to retained evidence; confidence or a successful older rehearsal is
not evidence for the final frozen source.

The release is **not ready to cut over yet**. The current baseline includes
the validated upstream catch-up, B2C and monthly bank-statement ingestion, but
it is not the final release until Native Sign and the Templating system have
been reviewed and merged into `19-usl` or explicitly rejected, followed by a
fresh complete qualification.

### Current local rehearsal input

The package currently present at `/Users/roger/projects/odoo/usl-online-dump`
is a rehearsal input, not the final frozen source:

- `dump.sql` modified 23 August 2026, 118,560,508 bytes;
- dump SHA-256
  `ad313e28586fafa27a4f6a266df57080456613dff1c8c2c6d7e012732bf633b1`;
- package size approximately 403 MiB, including approximately 290 MiB of
  filestore and 2,029 filestore files.

The repository candidate status reports that no current production migration
candidate exists. This is expected before the final merge/freeze cycle. Recheck
the package structurally through the normal reconstruction source gates; the
size and digest observations above do not establish migration completeness.

On 26 August, the B2C-integrated branch completed a fresh full reconstruction
from this package and published a sanitized reusable QA seed. The finalized
`odoo_dev` target passed the database boundary for the then-current fourteen
delivered product modules with no migration registry/schema residue. Accounting remained
balanced at EUR 2,900,936.82 debit and credit; B2C retained 304 canonical
orders, 457 source lines, 1,821 payment/refund/fee events, 261 fulfilments and
109 governed SKU aliases (nine exactly verified and 100 explicitly not
applicable), with zero unexplained pending mappings. All 180 critical source
moves have monthly session relationships, and all 40 B2C files plus 2,893
immutable evidence rows have durable archive links. Paperless archived 645
source groups and synchronized 638 live authorized mappings. A separate
empty-database installation passed for those fourteen product modules, and two
isolated full-seed hydrations matched
the sealed Accounting, Documents and Paperless controls with zero OCR
submissions. This is current rehearsal evidence, not a final frozen-source
production candidate.

The schema-v4 migration-cache/Documents-performance work and Paperless 3.0 are
now merged into local `19-usl`. All five affected product modules passed clean install,
upgrade and identical repeated upgrade. `usl_documents` passed 187 Python
post-tests, the 13/20/6 query ceilings, asset compilation, and desktop/mobile
Chromium suites (39/268 and 33/248). The merged tree also passed all 121
Documents migration-safety tests.
The exact Paperless overlay built for `linux/amd64` at manifest digest
`sha256:a30e826e471f097df1cb941b69d7379ebb800f4bf07a1daff45f2359d5cb079d`;
the full release-cohort restore remains separate. The fresh locked-source
reconstruction now finalizes canonical `odoo_dev` with 15 product modules, no
migration residue, 1,148 live Documents, nine Trash records and 8,654 indexed
chunks across the same 1,148 live documents. The main checkout published seed
`6e3f2120a19f35edc34010054fb2f1162341a7e58cbbfcb3f56be26d7a91ed6a`.
The exact committed tree then passed a 393-second cold hydration with zero OCR
submissions and a 16-second fail-closed warm hit with zero downloaded bytes and
zero OCR submissions. Evidence is retained in QA reports
`usl-odoo-qa-dc187ada-20260827T204619Z.json` and
`usl-odoo-qa-dc187ada-20260827T205300Z.json`. This remains rehearsal evidence;
no older seed or feature database is final frozen-source release evidence.

On 28 August the clean consolidated baseline at
`4de70cebe61fd69a21dfd7dd7dbb70fdf0e2f0ee` passed
[Distribution image workflow run 33120390343](https://github.com/unstaticlabs/odoo/actions/runs/33120390343).
That run qualified the finalized 150-module registry, the 50,041-action source
policy, the 42,669-action runtime policy and compiled product assets, then
published
`ghcr.io/unstaticlabs/usl-odoo:4de70cebe61fd69a21dfd7dd7dbb70fdf0e2f0ee`.
The workflow verified the release, OCA, action-risk and Distribution-runtime
labels plus an immutable repository digest. Copy the final digest from the
successful workflow summary into the private change record after the two
remaining feature decisions; this baseline image is qualification evidence,
not the eventual cut-over image.

The same consolidated runtime was then requalified on finalized `odoo_dev` at
commit `e8772f2b4be533082f2624d237f290f7860a5d73`. Accounting, B2C,
Projects, TESE, Platform Billing, Collaboration, multi-company acceptance,
identity reconciliation, assets, outbound-safety and the product/migration
boundary all passed without replaying unchanged source extraction. This
published schema-v4 seed
`302bb448494fab08162303cbac26ae007db657d49daa5df23f9733abfc20df29`.
An isolated full hydration from that seed passed in 391 seconds with exact
sealed controls and zero OCR submissions; its immediate fail-closed warm reuse
passed in 15 seconds with zero downloaded bytes and zero OCR submissions.
Evidence is retained in QA reports
`usl-odoo-qa-dc187ada-20260827T222548Z.json` and
`usl-odoo-qa-dc187ada-20260827T223250Z.json`. The disposable QA containers and
writable volumes were removed after capture. This evidence is invalidated by
either remaining feature merge and is not a final frozen-source candidate.

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
- Production consumes candidate-bound immutable Odoo Distribution, Paperless
  overlay and Ollama images by digest. It also requires an absolute mode-`0600`
  Personal Gemini key ring before preflight. It does not run from a mutable
  checkout, mutable image tag or development bind mount.
- Pocket ID, ingress, firewall, network and host policies remain externally
  owned. The application deployment may join explicitly approved networks but
  must not create, restore, provision or mutate Pocket ID.
- Mail, bank ingestion, provider jobs, webhooks and other external side effects
  remain paused until their individual post-admission activation gate.

## Current release train

The following work must remain independently reviewable. Merge only approved
final state into `19-usl`; never qualify production from the feature worktree.

| Workstream | Required release outcome | Status on 28 August 2026 |
| --- | --- | --- |
| Migration performance and portable candidate | optimized reconstruction, sealed candidate, external-Pocket cut-over tooling, Distribution image | merged through `61580c1704c`; reusable full seed published from the integrated rehearsal |
| Migration cache and Documents performance | content-qualified schema-v4 seed, verified warm reuse, batched/lazy Documents, bounded Odoo runtime | merged into `19-usl` from reviewed tip `3d2b2b49382`; affected suites, optimized reconstruction, v4 publication, exact-tree cold hydration and zero-download/zero-OCR warm reuse pass |
| Expense Analytics | expense-batch analytics/product behavior and migration parity | merged through `aae5994a7ec` |
| B2C sales and inventory | canonical order/payment/refund/fulfilment/accounting/stock links and historical B2C parity | merged through `368812b2868`; clean full reconstruction and complete source dispositions passed; physical opening stock remains separate |
| Paperless 3.0 | final Documents behavior, identity, export/import and full archive parity | merged into `19-usl` from reviewed tip `2ba19d6fa90`; clean suites, AMD64 overlay build, full archive and vector parity pass; release-cohort restore and signed-in browser evidence pending |
| Native Sign | final signing workflow and retained evidence | active feature branch; review and merge pending |
| Templating system | governed business-document templates and rendered output | active feature worktree; close to merge readiness, but no uncommitted feature state is release evidence |
| Collaboration History | source-backed business collaboration history with explicit attachment dispositions and no migration residue | merged into `19-usl`; clean reconstruction, repeated import and final product-boundary requalification passed |
| Distribution Access Control | final named-persona, company, recoverability and irreversible-action policy across delivered applications | merged into `19-usl`; 50,041 source actions and 42,669 runtime actions pass on the final reconstructed registry |
| Post-baseline migration-performance cache | bounded worker budgets, reusable qualified state and additional Documents hot-path batching | consolidated into the merged migration-cache/Documents work; no separate active feature remains |
| Monthly bank statement ingestion | idempotent statement ingestion from approved mail sources with visible failures | merged through `64c1f2b1207`; clean product/OCA suites and repeated `odoo_dev` upgrade passed; private OFX adoption and real inbound routing remain cut-over gates |
| Project task history titles | preserve the project-specific action label in browser tabs and breadcrumbs through Back/Forward restoration | merged into `19-usl` through merge `602df379352`; focused desktop webclient suite passed; signed-in Projects acceptance pending |

Native Sign and the Templating system are the remaining product heads to review
on this consolidated candidate. Feature-worktree evidence is not release
evidence; each exact reviewed tip still requires Lead Developer integration
and requalification.

For every merge:

1. record the reviewed commit and conflicts resolved;
2. run clean install, update and repeated update for affected modules;
3. run affected role, multi-company, browser and migration-boundary suites;
4. update module versions and source-to-target disposition coverage;
5. invalidate any QA seed, evidence or candidate produced by an earlier commit;
6. verify the complete target module set, not only modules touched by the merge.

The current integration-candidate database boundary expects the fifteen
delivered modules listed by `scripts/odoo/product_database_boundary.py`,
including `usl_b2c` and
`usl_documents_b2c`. Pending
features may extend that set. The script is authoritative after all merges; a
partial `odoo_dev` installation is not final-target evidence.

## Phase A — finish the release before freezing Online

- [x] Merge the approved migration-performance candidate into `19-usl`.
- [x] Merge the combined Paperless/schema-v4 Documents candidate after
  independent review and full optimized reconstruction.
- [x] Publish the shared seed from the clean reconstructed tree, run one cold
  hydration and prove the next verified reuse reports a warm hit with zero
  download/OCR work. Seed fingerprint and the two retained QA reports are
  recorded above.
- [x] Merge Expense Analytics after independent review and validation.
- [x] Merge B2C sales/inventory after independent review and validation.
- [x] Merge monthly bank-statement ingestion after independent review and
  validation.
- [x] Complete the optimized Paperless 3.0 full-archive reconstruction and
  vector-parity gate. The separate release-cohort restore remains open.
- [x] Merge the project/task browser-title fix after independent review.
- [ ] Merge or explicitly reject Native Sign after independent review.
- [ ] Merge or explicitly reject the Templating system after independent
  review.
- [x] Integrate Collaboration History after independent review and preserve its
  exact reviewed ancestry and archive ref.
- [x] Reconstruct and finalize canonical `odoo_dev` from the locked source on
  the integrated Collaboration candidate. The second Collaboration import was
  identical, the final database retained 14 product modules with no migration
  registry/schema residue, and 733 live Documents records remained stable with
  zero changes on repeated identity synchronization. The reduction from the
  earlier 798-document rehearsal is the approved removal of 66 demo Knowledge
  records plus retention of the genuine restricted strategy document. Shared
  QA-seed publication was intentionally not performed from the integration
  branch; publication remains restricted to a clean `19-usl` checkout.
  A second clean run on 27 August completed from the same locked source with
  the same 733 Documents, exact Accounting/B2C/Collaboration controls, passing
  multi-company acceptance and zero records in every outbound delivery queue.
  Timing evidence is sealed in
  `artifacts/migration/private/runs/usl-odoo-saas-19-3-reconstruct-20260827T065753Z.json`.
- [x] Integrate Distribution Access Control with reviewed ancestry preserved.
  Its final merged-registry action inventory is regenerated and passing;
  signed-in named-persona acceptance remains before admission.
- [x] Confirm no active release branch contains unmerged product or migration
  final state other than the explicitly preserved Native Sign and Templating
  workstreams.
- [x] Run static Python, JavaScript, shell, XML, Compose, manifest, French and
  migration-boundary checks from clean `19-usl`. Focused Ruff/compilation,
  backend and Chromium suites, Impeccable, shell/XML/JSON, all Compose variants,
  15 French catalogues and both product/migration boundaries pass.
- [x] Build the immutable GHCR `distribution` image for the current consolidated
  baseline and verify its revision/OCA/action-risk/runtime labels and repository
  digest. Workflow run `33120390343` passed for commit `4de70cebe61`; repeat
  after Native Sign and Templating are decided and record that final digest in
  the private change record.
- [x] Install every currently delivered product module into a fresh empty
  database; run update and repeated update without migration modules on the
  runtime path. This now passes for the 15-module access-control candidate and
  must be repeated after the remaining merges.
- [x] Run a fresh full local reconstruction from the most recent available
  Online dump and filestore while Online is still active. The 26 August
  rehearsal passed; it must be repeated after the remaining merges and again
  from the frozen source.
- [x] Compare whole-source model/field dispositions, attachments, users,
  Projects, Accounting, Expenses, B2C, inventory, Paperless, Sign, payroll and
  Platform Billing against the source. The 27 August gate covered all 19
  scopes and 226,836 records with zero blocked records, relation rows or stored
  fields; the attachment gate covered all 2,601 rows and 1,996 stored
  checksums.
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

The locked 24 August source now has explicit dispositions for all 19 audited
scopes: 226,836 source records, with zero blocked records, relation rows or
stored fields. The former five source blockers are closed by reviewed product
decisions and tested translations:

- AI configuration and Sales/Marketing configuration are discarded as
  experiments or default setup, not business history.
- Studio customizations are discarded because their business behaviour is
  rebuilt and owned by the Distribution.
- Seven source-backed saved filters are migrated; native filters and exports
  are recomputed, while AI/marketing exports are explicitly discarded.
- Nine standard dashboard definitions are recomputed where the Distribution
  has a native replacement or explicitly rejected where their Enterprise
  module is unsupported. The genuine strategy PDF is retained byte-for-byte as
  a restricted manager-only Document.
- Sixty-six default/demo Knowledge messages and 554 generated technical
  configuration events are deliberately not copied. Their exact source
  disposition is checksum-sealed outside the delivered product.
- Eighteen historical expenses retain exact dates, states, amounts, accounts,
  analytics and accounting links but point to the maintained expense-nature
  products instead of four retired trip-category products. Finalization accepts
  only this locked-snapshot, product-field-only transition; any other mismatch
  remains fatal.

These are no longer data-completeness blockers. The physical opening-stock
count remains a separate operational prerequisite because the source contains
no defensible historical quantity truth and the migration deliberately creates
no historical stock moves, quants or valuation layers.

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
| Action-risk policy | zero unclassified/stale actions; source, clean-install and reconstructed-registry checks pass; candidate/image digest agrees | Product/Security | [ ] |
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
5. Build the sanitized Odoo/Paperless portable candidate with the exact Odoo,
   Paperless and Ollama image digests. Independently approve its fingerprint.
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
5. `gate` release identity, complete product/migration boundary, exact action
   registry, source parity, Accounting, multi-company, Documents
   checksums/permissions and journeys;
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
