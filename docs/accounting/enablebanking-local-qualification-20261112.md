# Banking qualification — 12 November 2026

This report records completed local checks for product source
`f239c79870fda63c9e78728cdf5c9e9951f18549` in [PR430](https://github.com/unstaticlabs/odoo/pull/430).
It includes literal qualified product `80a3a1bcadb55821eb8ef6c28f77a30facb4018b`
and `usl_accounting` version `saas~19.3.2.2.21`. The report checkpoint is separate from that frozen product
source. The original report-only checkpoint
`cd01a9928b30a2989b4547d08d501e433c65464d` changed no component input or image build context;
the later cron-policy correction below changes only backup-tool input.

## Completed synthetic qualification

| Check | Observed result |
|---|---|
| Native Odoo tests on the combined product tree | 46 passed: 39 banking methods and 7 Accounting/Settings and Agent access integration methods; zero failures/errors |
| Standalone protocol tests | 15 passed |
| Source action inventory | Passed |
| Native action inventory | Passed: 168 modules, 49,382 runtime actions |
| Product asset compilation | Passed: 168 modules, 37 bundles, 64 generated nodes |
| Product/migration boundary | Passed: 23 product modules, no migration registry/schema residue |

The native tests ran with the normal Dockerfile runtime/core image and the exact
combined add-on checkout. The complete distribution image was packaged from the
same frozen inputs and independently compared with them. No broad test-suite
rerun after packaging is implied. The optional private-evidence source check was
skipped because the synthetic checkout contains no supplemental private evidence.

The complete local image is **linux/arm64**, with OCI revision f239:

- Image index: `sha256:ef42d0baa82484efd4d5ccfebd63900572ba8a93e3fd02cf1abc0df8ebba2158`.
- Distribution component input: `cd3ba7761a9fcab91a18f1682171e76f42b420736006d88149a035eab005f829`.
- Independent offline comparison passed **30,285 packaged files**: 29,466
  immutable-commit files, 801 resolved pinned OCA files and 18 qualified exported
  files, using the image's declared English/French catalogue filter.
- Dependency metadata confirmed Pillow 13.0.0, httpx 0.28.1 and python-jose 3.5.0.
  A literal offline command using all three read-only public source overlays also
  passed the same full-file check, with network disabled and no environment file.

The runtime inventory's reviewed-policy digest is
`d5e0632bf26068570ca72f471b0639f8ee517edda113f779dfa73d5238ccd272`.
The complete delivered policy-artifact seal is
`4925e1ab3fbec5f84b2a22c3162b26dfda3e9e65fb8d0f42ef5e3f034f985fff`;
these identify different scopes. Existing foreign decisions remain preserved.

## CI registration correction

[Hosted run 43197466120](https://github.com/unstaticlabs/odoo/actions/runs/43197466120)
for report-only checkpoint cd01 failed the existing pinned-OCA cron coverage
check. It identified the missing policy registration for
`account_statement_import_online.ir_cron_account_pull_online_bank_statements`.
The correction adds that exact hourly OCA job to the existing `always` policy;
it adds no gate or provider configuration. Native scheduling selects active,
due configured providers, and the adapter checks account availability and
consent before fetching. An installation without configured providers makes
no bank request.

After the correction, the 9 existing cron-policy tests and the complete
1,031-test compatibility suite passed locally, with 25 skips. The first local
suite attempt hit sandbox restrictions on 13 temporary loopback HTTP tests;
the normal-permission rerun passed. Source/action checks, product/migration
source boundary, governance contracts, action pins, documentation, XML and
skill-lock validation also passed. The separate MCP repository documentation
check was not rerun locally; hosted qualification remains responsible for it.
The optional private-evidence check remained skipped.

Recomputing all six component identities changes only **backup-tool**, from
`b4eae4804f86e70a71a2224766b1d430508814b05d3b1ea4f7d48b4da111e7f5`
to `8734f14fe58b3272c5cf5556e903f887a91b06ba75004355b8f89dd1369b7f22`,
because that component packages the cron-policy file. Distribution and the
other four component inputs are unchanged. No backup-tool image build is
claimed. At that cron-policy checkpoint the frozen f239 Distribution image and
source overlays were unchanged. Later QA-only program corrections are described
below.

## Representative private qualification and provider handoff

**Representative retained-private installation and two repeat upgrades passed.**
The fixture is the retained 9 November restored production snapshot, already
qualified on product revision `80a3` and Accounting version 21; it is not a fresh/current production
copy. The candidate f239 code ran as verified read-only source overlays on the
admitted official linux/amd64 foundation:
`ghcr.io/unstaticlabs/usl-odoo@sha256:b9c17b9b6e6e9679bdeca5d90a7654507837a619948609b1ba4f8f01bf15e906`.
Its foundation component input is
`f3cd48ac327515cbc88ed84905242a79c434c409d9f6bf4916d0bcdff5c9caef`.
The actual official-image/overlay comparison passed all 30,285 source files.
This is candidate-source qualification on an admitted foundation, separate from
the complete local ARM64 banking image; it is not an official banking release.

The native installation and two planned addon/OCA upgrades reached 168 installed
modules, Accounting version 21 and zero pending modules. Original frozen business rows,
financial aggregate controls, history and approved delegation passed. Every
original row/column in the fixed pre-bank financial/control scope matched
after installation, both upgrades and final checks. The original baseline was
never replaced. New source ACL model/group/permission fields and company-rule
model/domain fields matched. That metadata probe does not compare rule groups,
active flags or permission defaults; the separate local native cases cover
role/company behavior. The final product/migration boundary passed.

Two earlier QA failures remain part of the evidence. The first stopped before
PostgreSQL start because private extraction permissions prevented traversal of
public source directories. The second stopped after native installation because
three inherited active QA cron flags predated the banking installation. Read-only
checks found no financial or frozen-row change. A reviewed native QA repair
deactivated only those three exact known cron flags while verifying and keeping
the new OCA cron inactive. It kept
cron threads disabled and external egress unavailable, without broad
neutralization, credential reset, reinstall or business-data repair.

The explicit **same-fixture continuation** reused the original pre-install
financial baseline, metadata scope and PostgreSQL growth budget, then completed
the outstanding post-install authority/access/preservation checks and both
upgrades. The whole baseline receipt SHA256 is
`a203136d5bb2b265205eea74643453cf294098e1c3105ac62c83745c5b342ca8`.
The whole successful continuation receipt SHA256 is
`088a53fcff8a9ea4356ba700b578539490ce6bd268f393792ee0f79f6d706526`.
The retained failed receipts are
`f423370b3a6e7a1b9b151d8741a63ca304a9071d024dc11e57f7cb7a2f3f26d9`
and `34cb9752bc4c6a42ec5203b74e8aa670e00970f53f854fb07266d1744e8e2fa9`.
This does not claim a clean first-attempt pass. Raw evidence stays private on the
host. Independent cleanup verification found no task app running and the started
QA PostgreSQL stopped; production was unchanged.

No real Enable Banking application, signing key, consent or Fortuneo payload was
obtained. A live bank import and its payload identity contract remain unproved.

## Capture context

The PR's 12 inspected synthetic desktop/mobile captures were taken on
12 November 2026 from earlier feature revision
`4e63774e5fec4a2d805f62048e9843c199c79e68`. A broader source comparison confirms
identical trees across 4e, f239 and b2 for native `odoo/`, native `addons/`, the
complete banking addon and the existing Accounting/access model/view/static
scopes. The native trees are `d22d72eead5049f5fa2693495df6f90f2e3715cd`
and `5bb02297d6697c84c0adf8a4a0e225fe246f85b3`; the banking tree is
`f7fbc89dbebfdb8593e006db33b6adaf664e334b`. The whole seven-scope comparison
receipt SHA256 is
`9efd58e03966175591c75471876059485df904a6b0734d1464a14ebd1a853b59`.

They illustrate unchanged native/banking UI source. Final browser replay remained
blocked pending a manual refresh of the preview's connection-error page; it was
not bypassed. No final-runtime browser execution, pixel identity or functional
proof from still images is claimed. This report adds no captures.

## Evidence binding

Private local logs remain outside the public repository. This report was
personally inspected and contains selected code/image/proof identities,
synthetic test counts and scope limits only. The inspected local qualification
receipt SHA256 is `5dd6ab92c359a3dee1218b70d74ea5c7a7f9d2a2a8ca34a39ed2df161553b1fd`.

[Hosted qualification 43199905499](https://github.com/unstaticlabs/odoo/actions/runs/43199905499)
passed for exact cron-policy head `b2d197bb9e92288edf5352b8d355fa87fd8355f8`.
Compatibility and final qualification ran; the staging-PR event skipped
clean-install/repeated-upgrade and publication jobs. This report-only follow-up
needs its own ordinary PR check; consult the actual check before claiming that
head passed. The f239 Distribution source/image and all six component inputs
remain unchanged from b2 by this report update.
