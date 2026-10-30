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
| Repository tooling | 795 tests passed; 20 explicit host/environment skips |
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
| Sign/PDF | Exact rebuilt image, Python resolver check, six Maven tests, synthetic mTLS signing and validation, tamper/unsigned/untrusted inputs and incremental-PDF preservation passed |
| Document rendering | Repeated HTML and LibreOffice rendering passed; configured request-size boundary and JavaScript-disabled behavior checked |
| Ollama/Documents | Exact pinned BGE-M3 model produces finite normalized 1,024-dimensional vectors identical to the previous service for synthetic inputs; Documents alias checked |
| Runtime policy | Canonical clean registry passes: 165 modules, 48,860 runtime actions; 321 changed records explicitly reviewed across accounting, foundation and operations |
| Browser smoke and visuals | Synthetic posted bill → journal items → label filter → reload → bill breadcrumb; desktop and mobile captures inspected, including JPEG metadata |

The canonical policy digest is
`b7dcaa214d977f9230b0363b58a53553744fabced3c6edea7a79d66a58d71ce5`.
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
pass. Optional-module inclusion and release-path implications are tracked on the
PR and CIO card; no module was silently installed and no gate was relaxed.

Authentication probes intercept the callback before Odoo: they do not claim a
physical passkey ceremony or a full Odoo callback. Document-service probes do not
claim a Paperless database restore. Provider tests use offline fixtures; French
live sending/reporting stays disabled. Browser smoke is focused and does not
claim every application or history path was exercised. The fresh reviewer audited
USL adaptations and critical overlaps, not every line of all 499 upstream commits.

Safe [final-source browser captures](../product/qa/upstream-sync-20261031/README.md)
show invented data only. Screenshots establish layout; functional outcomes were
verified separately. No backup values, private identities, filenames, credentials
or private logs are included in this public record.

## Delivery

The integration PR targets `19-usl-staging`. Merge only after unresolved decisions
and required checks are satisfied, then record the exact staging deployment and
admission receipt on CIO task #3472. Close the six absorbed dependency PRs with a
link only after integration lands. Production promotion stays with existing
automations. Odoo 20, AI Pipelines and unrelated Icebox cards remain untouched.
Stop only task-owned QA resources after delivery; preserve private evidence.
