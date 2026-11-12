# Odoo 19.3 upstream integration, 12 November 2026

This candidate starts from staging `f0fa1ace215221ddbc19ad90ae76c1387f6bc21a`.
It preserves the literal history of [#416](https://github.com/unstaticlabs/odoo/pull/416),
head `9f5eedbe34f0e43c43f5903fd3d16ea118c2c142`, and merges the next 50 upstream
commits, from `5c8347a950f207ce6d88ef2bc6b57bc7f673d516` through
`c293c03e991730c7c58dfc9bcaff007df7dcf920`. The earlier 235 commits and these
50 commits remain distinct merges. #401 and #413 remain literal ancestors.

## Integration and customization decisions

The #416 merge has no textual conflict. A separate helper commit on its tip
removes two inherited trailing-whitespace lines for the normal commit guard.
The additional merge has one conflict, in
`addons/web/static/src/search/search_model.js`: create the favorites group before
activating portable URL filters, while retaining USL's action-owned
`config.urlState` and once-only decoding. A separate compatibility commit ports
the new upstream favorites regression to `mountSearchFromCurrentRoute`.

The additional range changes 97 paths and touches 18 installed module paths;
16 module source identities change. It changes no manifest, versioned migration
or Python requirement. Three paths overlap previous fork changes: the search
model, its URL tests and `account_move.py`. The account changes add translated
tax-note dependencies and buffer-aware binary MIME handling without replacing
fiscal-year sequencing. PDP advance deduction and report-style patches remain.
The optional French PDP penalty module remains outside the delivered closure.

Incremental review covers module-install commit timing and bounded optional
module uninstalls; inaccessible relational values during NewId precomputation;
resource avatars without private employee-field access; foreign-VAT country
matching; signed COGS on refunds; partially delivered accruals; customer returns
and sales costs; unrounded billed quantities; and payment-state flushes. Native
sale-loyalty ACL and malformed website-attribute fixes remain in upstream
history; those optional applications remain uninstalled.

The integration also retains the literal qualified guide-support change from
[#423](https://github.com/unstaticlabs/odoo/pull/423),
`fb34b351e436333492a2e13e02746f376eb64e91`. Internal users gain native read access
to the abstract guide model. The server support manifest describes the runtime
qualified 166-module surface and its three existing readonly guide methods.
The embedded legacy MCP release pin and compatibility digest do not change.
Available server capabilities and a client's minimum requirements remain
separate contracts.

## Action review

Runtime discovery on the canonical 166-module database records 57,698 actions,
including 48,916 runtime actions. The upstream integration adds or removes no
action keys: 43 implementation digests and 230 source-position records change.
Every implementation change was reviewed against its source delta. The combined
guide merge's tracked surface exactly matches a subsequent real runtime
rediscovery.

Five native reset entry points actually restore XML template content and
translations: `mail.template.reset_template`, `sms.template.reset_template`,
`template.reset.mixin.reset_template`, `mail.template.reset.reset_template` and
`sms.template.reset.reset_template`. Their classification changes from
`read_only` to `operational`. Native editor, company and global-template access
remain unchanged. Delegated readonly calls now describe their actual write
semantics; writable delegates retain ordinary native authority. No additional
business approval or Agent-only restriction is introduced. The protected runtime
policy is byte-identical to the upstream integration's starting policy.

The combined qualified surface/policy digest at product source
`8b8fe485f111ffc24407d191dab7d9c86afe6151` is
`64f5e05c00b92aecbc978171eeadeeff93b46ae92c283e9d81e453fc58850a70`.

## Dependencies

Pins already included by #416 remain: Pocket ID 2.19.2, Valkey 9.1.3 and
Gotenberg 8.39. #413's GitHub login 4.8.0 and artifact 7.0.2 pins are inherited
literally. #420 overlaps on Pocket ID and Valkey, but its only-new Ollama
0.40.15 remains open: service defaults, environment examples and release fixtures
need coordinated changes and native runtime qualification. The prior red #420
run reported eight pin-consistency assertions, which does not establish an
Ollama runtime defect. The deferred Tika 4 major update remains outside this
integration. This candidate does not change Python requirements or claim to
resolve the separate Pillow or python-jose advisory work.

## Observed qualification

The unchanged 235-commit range reuses #416's observed evidence from
[run 42602534830](https://github.com/unstaticlabs/odoo/actions/runs/42602534830):
a clean install and two upgrades of 166 modules, 2,269 backend tests,
desktop/mobile HOOT across 13 modules, 11 documented journeys and runtime
rediscovery. Its evidence omitted the recent private production-restore upgrade
required for this final integration.

The following checks ran against the combined product source above. Counts
name separate runs and may overlap; they are not a deduplicated total.

| Check | Observed result |
| --- | --- |
| Canonical product install and two product-root upgrades | 166 modules; optional exclusions and product/migration boundary pass |
| Combined guide upgrade twice | Both passes preserve the same original-row baseline; no rebaseline |
| Incremental native financial, HR, MRP, EDI and template-reset cases | 29 tests, 0 failures or errors |
| Fresh foundation database: UoM, NewId and changed MIME/HTML cases | 58 tests, 0 failures or errors |
| Dependent access, accounting, EDI ingestion and project suites | 1,119 tests, 0 failures or errors |
| Five affected HOOT files | 378 desktop tests / 1,331 assertions and 261 mobile tests / 703 assertions; both suites pass |
| Combined guide API/ACL smoke | 3 tests; native readonly method documentation and denied CRUD pass |
| Repository operations suite | 998 tests; 21 explicit environment skips |
| Real non-admin Purchase browser journey | 4 native Chromium cases; shared filter, saved favorite and resulting records pass at 1280 × 800 and 390 × 844 |
| Combined runtime discovery | Exact tracked surface equality; 166 modules / 48,916 runtime actions |
| Source checks | Action inventory, product/migration boundary, Python compilation, documentation and whitespace pass |

The template-reset contract fails eight subcases with the old readonly metadata,
passes with the correction, then fails the same eight subcases when the old
compiled readonly policy is mounted into a disposable test container. These
causal controls leave the tracked final policy unchanged.

The saved-favorites regression also has a causal control: with the pre-fix
search model, 20 tests pass and one fails because the menu contains only
“Save current search”. The final model passes all 21; remounting the old model
reproduces the same failure. The unported upstream test fails under USL's URL
contract with “My favorite” instead of “Shared”, confirming the compatibility
helper is necessary. Both final desktop and mobile suites execute the new case.

Two earlier fixture limitations were isolated without changing product gates.
A UoM at-install fixture cannot use a partial registry against an already complete
accounting database; its selected cases pass in the fresh foundation database.
The unchanged MIME extension test expects `.torrent`, while both the previously
qualified image and this candidate return an empty extension from their OS MIME
registry. That unchanged test is excluded only from the focused 58-case run;
this record makes no claim that it passes. The first disposable mobile browser
harness assumed a list renderer; correcting the harness to accept Odoo's native
mobile kanban renderer yields all four passing journeys. Product code is unchanged.

Screenshots were captured on 12 November 2026 from the tested product source,
using only invented records and an ordinary Purchase user. Both selected images
and PNG metadata were personally inspected before publication. They contain no
private data, credentials, browser address bars or local paths. Images illustrate
UI state; the native assertions establish behavior. They are retained in the
[task captures branch](https://github.com/unstaticlabs/odoo/tree/captures/codex/burn-upstream-193-20261112).

## Required private restore and release boundary

The host-only representative restore qualification is still pending. The
approved fixture is a three-day-old qualified production snapshot, not a fresh
or current backup. Private payloads, identifiers and detailed logs remain in
task-owned isolated scratch on the production host; no private backup is exported
to a local checkout or temporary directory.

The approved rehearsal uses exact candidate source on the cached production
foundation only after its source binding and immediate resource budget are
verified. Dockerfile, requirements, constraints, entrypoint, pre-upgrade helper,
configuration templates and setup.py are byte-identical between production
`622ac94fe16977b90307d76fe7ec21d6cf377f5a` and this candidate. All old code,
add-ons, documentation and bytecode must be removed before the exact public
candidate trees are copied. The resulting source tree and resolved OCA bundle
must be verified independently. This proves candidate source on that foundation;
it does not establish admission of the final published image.

No production mutation, deployment or new backup capture is part of this
qualification. Host work remains serialized behind staging recovery admission,
a free QA slot and fresh memory/disk checks. Bulk storage retains its 15 GiB
reserve and existing rollback resources; root storage retains its reserve.
Forward and repeated upgrades must preserve the original baseline and financial
controls. A later production mutation still requires the existing automations to
verify a current qualified, restorable backup. Delivery uses the normal protected
staging-to-production path and the native generation rollback procedure.
