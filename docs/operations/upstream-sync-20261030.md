# October 30 Odoo 19.3 and dependency catch-up

This integration starts from staging `df3ad851dca8a78c54285a9fe8c2465ff06be6c0`
and merges upstream `saas-19.3` at
`df8dc02eec50088135399c48644c5f929cd2b60a`. The previous import is
`8a5848530ac47edbdc28ae93f53b29b97b47b031`: 499 upstream commits,
4,062 changed paths, including 1,019 outside translation directories.
Upstream ancestry is retained. Odoo 20 remains parked in CIO task #3471.
Delivery and actual qualification are tracked separately in CIO task #3472.

## Conflict resolution and customization audit

Six upstream paths overlap with the fork's changes. Only
`addons/web/static/tests/search/search_from_url.test.js` conflicts textually:
retain the USL test that rejects portable state owned by another browser action,
and enable upstream's repaired desktop hotkey-sharing regression. One upstream
trailing-whitespace line in the sale template widget is removed to satisfy the
commit helper. All other literal upstream changes merge without textual conflict.

The audit retains the fiscal-sequence, portable-navigation, purchase attachment
grouping and unknown-character-encoding fixes: this range does not replace their
contracts. The Community journal change removes an unavailable Enterprise wizard
reference; no custom code depends on it.

Upstream changes posted invoice residual calculation to use payment-term journal
items instead of recomputed invoice totals, and improves floating-point comparison
when retaining a manual exchange rate. Qualification must cover those native
regressions together with the distribution's payment-rate and settlement flows.

Two French e-invoice changes require compatibility work:

- The new CII advance-payment importer receives the same matching rules as the
  existing UBL importer: company, commercial supplier, currency and eligible
  supplier-document state/type. It checks duplicate financial evidence before
  copying, creates deductions on the destination only after resolving identity,
  and retains source history, manual tax signs and target exchange-rate semantics.
  This small core patch deliberately parallels the existing UBL patch. At the
  next sync compare both native importers and remove patches only when their
  behavioral regressions pass without them; avoid superclass bypasses.
- Native CII billing-period configuration now handles absent optional deferral
  fields correctly and exports a period only when complete evidence exists.
  Remove the obsolete USL fallback, which incorrectly treated payment due dates
  as service-period ends. Tests exercise native configuration through XML nodes,
  including absent, partial, complete and incomplete deferral data.

The new PDP sending-method computation performs directory lookup and writes
partner e-invoicing identifiers. Runtime review must trace those effects and
preserve native actor/company permissions; a compute name is not proof of a
read-only action. No Agent-only restrictions or new approval gates are added.

## Dependency decisions

All six candidate changes are included in separate scoped commits. The upstream
Odoo merge preserves ancestry; dependency PR patches are deduplicated, rather
than claiming their branch histories were merged:

| PR | Delivered candidate | Qualification focus |
| --- | --- | --- |
| #356 | Immutable GitHub Action updates | Real tag identity and hosted workflows |
| #264 | Python requirements and runtime constraints | Resolver checks, exact image build, supported interpreter markers |
| #263 | Overlapping Python pins plus Sign's urllib3 update | Deduplicate Odoo pins; retain the separate pyHanko requirement |
| #261 | Jackson 2.21.7 | DSS Maven verification and signing validation |
| #257 | Impeccable 4.1.0 | Node >=22.18, native CLI packaging and existing command compatibility |
| #249 | Pocket ID 2.19.1, Ollama 0.40.11, Gotenberg 8.38, Alpine 3.24.3 | Authentication/API scopes, exact embedding contract, repeated document rendering |

Compose, current environment examples, bootstrap defaults and release assembly
must agree on immutable service images. Two regressions cover this consistency;
the original update left twelve mismatches, including the release Ollama digest.
Pocket ID's notification setting becomes `EMAIL_LOGIN_NOTIFICATION_MODE=disabled`.
The unregistered historical Pocket ID source patch remains unchanged.

Impeccable's existing check/detect commands work with version 4; vendored skill
snapshots are not regenerated. Existing Python 3.12 lxml/cleaner compatibility
pins remain intact. Historical manifests and documentation are not rewritten.

## Upgrade compatibility and maintenance

The native foundation now explicitly reloads the native and OCA modules actually
installed in the target when core/OCA source identities change. The previous plan
updated only managed custom modules, leaving old native XML behind. It does not
use `-u all`, guess optional modules or install missing native integrations.
Policy-only changes do not unnecessarily reload the foundation. Existing plan
schemas and historical receipts remain valid.

Full native reload exposed two preservation failures. Eight native manager-group
XML seeds tried to reapply default users, and the generated report stylesheet
replaced an existing attachment. During update only, the exact owning native
manager seeds retain their configured memberships while other fields still load.
For the exact generated web report asset, changed CSS creates a derivative and
rebinds the XML ID atomically; the original row and blob remain intact. Fresh
installs, custom overrides, ordinary writes and business attachments retain native
behavior. Wrong provenance fails before effects. At the next upstream sync,
compare these eight seeds and the report-asset loader; remove this bounded core
patch only when the preservation regressions pass without it.

New native PDP lookup helper names retain the existing offline/non-French/demo
boundary before provider activation. Native stored wizard computes use sudo;
reviewed context accurately describes that behavior rather than claiming ACL
checks that do not occur there. New IAP token rotation uses the existing
irreversible-action control before either provider call or the local secret
change. It applies equally to people and Agents; no Agent-only rule is added.

Pocket ID 2.19 generates a client secret automatically. Provisioning previously
mistook that for the environment-owned secret being present, breaking real token
exchange. Provisioning now checks dedicated secret metadata and adds the intended
secret when absent while retaining existing credentials.

## Completed qualification

Product source revision: `d38b64be186d`. Evidence was collected 30–31 October 2026.
These results are local qualification, not a claim of staging deployment.

| Area | Observed result |
| --- | --- |
| Repository tooling | 800 cases: 780 passed, 20 explicit host/environment skips |
| Release planning and evidence | 48 focused tests passed, including actual installed native/OCA selection and a causal old-controller failure |
| Clean database | Full installation, two upgrades, affected tests, runtime contracts and product/migration boundaries passed |
| Recent private restore | Qualified October 30 backup verified, isolated and neutralized; forward upgrade and repeat upgrade both preserve original rows, files, financial and authority controls against the same pre-upgrade baseline; no pending module changes |
| Financial and access behavior | 92 CII/UBL, billing-period, posted residual and access cases passed |
| Integrated financial/stock behavior | 201 cases passed across purchase file grouping, expense matching, immediate settlement, taxed exchange rates, stock and MRP |
| Native ORM | 46 domain, prefetch/access, ordering, collation and property cases passed |
| PDP lookup | 7 offline/native/helper cases passed; reverting the helper adaptation reproduces failures |
| Native XML preservation | 16 cases passed; reverting the converter adaptation reproduces 9 failures |
| Checkout availability | 4 native HTTP tests passed, including 8 negative subcases; removing the upstream check reproduces all 8 failures |
| Navigation | 153 desktop and 20 mobile tests passed in native and full-distribution contexts; one stale assertion was corrected to retain existing portable filters, with no navigation product change |
| Pocket ID | 12 provisioning tests and 15 live browser/PKCE/token/JWT/userinfo cases passed, including replay and origin rejection |
| Sign/PDF | Exact rebuilt image, Python resolver check, six Maven tests, packaged pyHanko synthetic signing, actual mTLS validation and field preparation/filling, tamper/unsigned/untrusted inputs and incremental-PDF preservation passed |
| Document rendering | Repeated HTML and LibreOffice rendering passed; configured request-size boundary and JavaScript-disabled behavior checked |
| Ollama/Documents | Exact pinned BGE-M3 model produces finite normalized 1,024-dimensional vectors identical to the previous service for synthetic inputs; Documents alias checked |
| Runtime policy | Canonical clean registry passes: 165 modules, 48,860 runtime actions; 321 changed records explicitly reviewed across accounting, foundation and operations |
| Browser smoke and visuals | Synthetic posted bill → journal items → label filter → reload → bill breadcrumb; desktop and mobile captures inspected, including JPEG metadata |

The canonical policy digest is
`5697604d4cd90c3363c019b15a9adbeb9ea39235b12bc857b55ee804ac273cf4`.
Action review covers 46 additions, eight removals and 267 changed records. It
corrects context, effects and recovery guidance without introducing new authority
gates. The guarded token rotation is the existing irreversible-control boundary.

Initial failed rehearsals are retained privately and are not counted as passes:
incomplete predecessor-addon recognition, the old managed-only upgrade plan and
native seed reload. Both successful private runs start from a fresh original
restore and the same baseline; no post-upgrade rebaseline or preservation
exemption was used. Neutralization used pinned predecessor SQL and recognizable
addon metadata because registry access could not pull the old image; it was not a
run of that predecessor image. Only the Odoo filestore was mounted, never live
recovery credentials, and outbound access/crons/queues were disabled.

### Historical runtime variance and limits

The additional exact runtime comparison on the private upgraded registry reports
four historical differences from the canonical clean fixture. Read-only comparison
with the original backup proves all four predate this upgrade: the optional
`sale_project_stock_account` module is absent; a PDP server action has an older
stored display label; and two Feedback contexts use different numeric IDs that
resolve to the same declared Product Feedback XML ID. The original and upgraded
action fields are identical. These are not reported as an exact private-runtime
pass. The current GitOps staging path does not invoke that exact-cohort comparator;
it validates compiled policy artifacts and runs health, smoke, preservation and
control checks. Clean CI retains the strict canonical comparator. Preserve the
installed cohort for this release and track optional-module inclusion and semantic
context normalization separately on the CIO card. No module was silently installed
and no gate was relaxed.

Authentication probes use a fresh Pocket ID 2.19 database and intercept the
callback before Odoo: they do not claim an old identity-database upgrade, physical
passkey ceremony or full Odoo callback. DSS seal, data-to-sign/embed and
manifest/sign endpoints were not exercised. Gotenberg size probes use explicit
temporary 2 MB configuration. Ollama comparisons use a fresh exact-model cache,
not an in-place cache upgrade. Document-service probes do not
claim a Paperless database restore. Provider tests use offline fixtures; French
live sending/reporting stays disabled. Browser smoke is focused and does not
claim every application or history path was exercised. The fresh reviewer audited
USL adaptations and critical overlaps, not every line of all 499 upstream commits.

Safe [corrected-source browser captures](../product/qa/upstream-sync-20261031/README.md)
show invented data only. Screenshots establish layout; functional outcomes were
verified separately. No backup values, private identities, filenames, credentials
or private logs are included in this public record.

## Attachment-fixture recovery

The first hosted full run after PR #359 reached 2,223 tests and reported one
failure: an archive-history fixture supplied the removed `ir.attachment.datas`
field. The previous upstream code silently discarded that key; the newer code
rejects it. Comparing two false checksums could therefore pass without retaining
any file bytes. This was missed by the focused local selection.

The first full run spent about 35 minutes before the remaining browser phase.
The retry allows 60 minutes instead of 40 for the full database job; every test
and admission requirement remains. This adds execution headroom, not a skip.

The bounded repair uses exact `raw` bytes in the archive fixture, development
bootstrap create/update helper and Expense Batch QA receipt helper. It asserts
nonempty checksum and exact bytes before and after archive recovery, and covers
bootstrap UTF-8 content and same-record updates. The repaired archive test and
full access suite pass locally (278 cases). Real ORM helper probes verify exact
create/update UTF-8 bytes, receipt bytes and stable identity on repeat; their
transaction is rolled back. A variant audit found no other
active owned writer. Keep the accounting protected-field denylist and the adapter
that removes an inherited OCA legacy key: those occurrences are intentional.
Dormant OCA legacy callers are outside the delivered module closure.

The complete affected frontend selection also passes locally: 344 desktop and
327 mobile cases. Final synthetic screenshots were recaptured from corrected
product source `abdf398a1575` mounted (identical native code from the rebuilt
`4a60a1664a00` image), inspected and metadata-audited.

The failed run published no release and did not change deployed staging. The
repository recovery protocol calls for reverting #359 to the exact previous
qualified tree, then qualifying a separate corrected catch-up. Final delivery
results belong on CIO #3472 and the corrected PR; this record does not claim the
first hosted full run passed.

## Final compatibility repairs

A project-only collaborator could not load Odoo's API metadata because a
reconciliation-review access-domain hook attempted to read accounting journals
before checking that person's existing access. The same failure affected a human
and an Agent with equivalent delegated access. The hook now returns the native
restricted domain early for callers without journal/account access, and looks up
partners only when readable. It grants no accounting permission and adds no
Agent-only rule. Twenty-seven focused cases pass; the three causal cases fail
against the previous hook. Actual human and Agent metadata requests return HTTP
200 with project methods visible, accounting methods absent and direct accounting
review still denied.

The canonical runtime surface was rediscovered after that repair. Eleven action
records move source locations; their reviewed digests and classifications remain
identical. The accounting source hash changes, so the aggregate policy digest
above is new. Strict clean-runtime and source validation pass.

Inventory publication previously truncated the destination before writing its
replacement. The CLI now writes and syncs a same-directory temporary file before
atomic replacement. Readers observe complete old or new JSON. Tests exercise
partial writes, failed sync/replacement, permission preservation and caller umask;
the 800-case tooling suite passes (780 passed, 20 explicit host/environment
skips). This is a single-file publication guarantee, not a transaction across every policy artifact
or a claim of directory crash durability.

The MCP attachment tool also used the removed field. Companion
[PR #192](https://github.com/unstaticlabs/odoo-mcp/pull/192) sends native `raw`
base64 and permits the tool's documented 512 KiB payload through a bounded
internal request budget; generic request limits, native authority and one-attempt
mutation behavior remain. End-to-end protocol tests against governed disposable
Odoo verify exact UTF-8 and 512 KiB opaque bytes, stored checksum, size and task
ownership. The old field fails causally. All 289 MCP tests, build, evaluation
validation and image build pass. The temporary QA credential was revoked after
the checks. These fixtures do not claim every image-processing path was tested.

[MCP PR #193](https://github.com/unstaticlabs/odoo-mcp/pull/193) also corrects
published compatibility metadata that omitted a module and three native methods.
The validator now recognizes multiline calls and generic types containing
semicolons and verifies declared modules. All 13 declared modules and 50 literal
source actions are represented. Four omission regressions fail against the old
validator; all 293 MCP tests pass. Runtime authority is unchanged.

A final retained-restore forward and repeat upgrade uses the final mounted
backend source. Both pass the original financial, authority, strict preservation
and filestore checks; original baseline hashes and earlier evidence are
unchanged. The exact private QA PostgreSQL container was stopped afterwards.
Paperless remains outside this rehearsal.

## Service ownership

These dependency changes update this repository's development/test/default pins
and qualify candidate service images. They do not independently upgrade separately
owned GitOps services. Pocket ID belongs to `com_unstaticlabs_auth`; Ollama belongs
to `svc_odoo_ollama`. The Odoo promoter configures Odoo's external identity provider
and embedding contract, not those services' images or databases. The GitOps Odoo
stacks also retain a separately pinned Gotenberg image unless changed there.
Sign/DSS is a promoted Odoo component. Any separate identity-database upgrade needs
its own existing-data migration/rollback qualification; fresh-fixture results do
not substitute for that rehearsal.

## Delivery

The integration PR targets `19-usl-staging`. Merge only after unresolved decisions
and required checks are satisfied, then record the exact staging deployment and
admission receipt on CIO task #3472. Close the six absorbed dependency PRs with a
link only after integration lands. Production promotion stays with existing
automations. Odoo 20, AI Pipelines and unrelated Icebox cards remain untouched.
Stop only task-owned QA resources after delivery; preserve private evidence.
