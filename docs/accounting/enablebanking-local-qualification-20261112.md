# Banking local qualification — 12 November 2026

This report records completed local checks for product source
`f239c79870fda63c9e78728cdf5c9e9951f18549` in [PR430](https://github.com/unstaticlabs/odoo/pull/430).
It includes literal qualified product `80a3a1bcadb55821eb8ef6c28f77a30facb4018b`
and `usl_accounting` version `saas~19.3.2.2.21`. The report checkpoint is separate from that frozen product
source and changes no component input or image build context.

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

## Pending private and provider proof

**Representative private banking first-install and repeated-upgrade proof is
PENDING.** Earlier bank restore/upgrade checks used synthetic data. The proposed
additional proof uses the retained representative 9 November restored snapshot
and its original business, financial, history and approved-delegation baselines.
It awaits the admitted official AMD64 foundation and a serialized idle host
window after the existing deployment sequence. This report records no host
execution, private-data restore result or official AMD64 banking qualification.

No real Enable Banking application, signing key, consent or Fortuneo payload was
obtained. A live bank import and its payload identity contract remain unproved.

## Capture context

The PR's inspected synthetic desktop/mobile captures were taken on
12 November 2026 from earlier feature revision
`4e63774e5fec4a2d805f62048e9843c199c79e68`. The visible banking source is
byte-identical in f239. They illustrate that tested UI; no new f239 browser run
or functional proof from still images is claimed. This report adds no captures.

## Evidence binding

Private local logs remain outside the public repository. This report was
personally inspected and contains selected code/image/proof identities,
synthetic test counts and scope limits only. The inspected local qualification
receipt SHA256 is `5dd6ab92c359a3dee1218b70d74ea5c7a7f9d2a2a8ca34a39ed2df161553b1fd`.

Hosted qualification of the report head is **pending at this checkpoint**;
consult the actual PR check before claiming a hosted pass. The product source,
normal image and prepared private-proof program remain frozen independently of
this report-only commit.
