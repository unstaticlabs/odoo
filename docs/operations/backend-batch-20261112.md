# Backend batch qualification, 12 November 2026

Settings administrators can open failed queue jobs and edit priority under
native company rules. Ordinary backup pauses use the existing maintenance
response, and release status reports the fixed launcher's actual progress.
This batch also carries the native 19.3 fixes, including saved favorites, and
the Pillow 13 TIFF encoder correction.

## Source and artifacts

The tested product is `80a3a1bcadb55821eb8ef6c28f77a30facb4018b`.
Literal ancestry preserves [PR 425](https://github.com/unstaticlabs/odoo/pull/425)
report `2f13d88`, [PR 429](https://github.com/unstaticlabs/odoo/pull/429)
`bda982` (including [PR 428](https://github.com/unstaticlabs/odoo/pull/428)),
[PR 426](https://github.com/unstaticlabs/odoo/pull/426) `862385`, and the
qualified [PR 408](https://github.com/unstaticlabs/odoo/pull/408) integration
`c37df6c`, which retains its original `8399e725`. The merges have no resolution
edits. Accounting stays at `saas~19.3.2.2.21`; classifications and compiled
policies are unchanged. MCP PR 200 and banking/UI PR 430 are separate.

The complete normal AMD64 Distribution image is
`sha256:9a56368dacdac9309d0c560836a3992ff011548fa0afe2bbef4a8633af503241`.
[Image receipt](../../artifacts/final-batch-20261112/image.json) records its
AMD64 manifest/config, component inputs and independent verification of all
30,232 source files and 700 native add-on resolutions. No source bind mounts,
cached-foundation replacement or partial-wheel substitution qualified it.
Python 3.12.16, Pillow 13.0.0 and libtiff 4.7.2 passed offline codec/PDF/Sign
renderer probes and `pip check`.

## Native qualification

[Canonical receipt](../../artifacts/final-batch-20261112/canonical.json): clean
product installation, reviewed optional-module exclusion, two upgrades,
166-module / 48,916-action exact runtime inventory, product DB boundary and
zero pending modules pass on the final image. Three DB-backed Pillow probes
and 25 native image/PDF/Sign cases pass with image-native application paths.
The repository suite passes 1,031 tests with 25 explicit opt-in skips; source
inventory, static product boundary, documentation and diff checks pass.

[Private receipt](../../artifacts/final-batch-20261112/private.json): the same
complete image updates all 166 modules twice on the retained qualified
representative restore from 9 November. Each phase preserves the same original
four-root row baseline, scoped native financial controls and frozen original
Agent authority snapshot. Three DB-backed Pillow probes, two native Sign
appearance tests and the strict private product DB boundary pass; 166 modules
remain installed with no pending changes. This is an Odoo DB/filestore proof,
not a fresh/current-backup claim or a requalification of retired Paperless
media, object-store DR, live providers or production admission. Business
values, identifiers, per-file payload hashes and raw logs remain on the host;
public receipts contain only safe outcomes, software identities and evidence
hashes.

The private exact inventory comparator still observes three historical UI
context/label digests. Their sealed entries and check script are unchanged
across baseline `622ac94`, admitted `a843d304` and this product. Canonical
strict inventory passes separately. No private IDs were resealed, no records
were rewritten and no gate was weakened. The existing
[semantic inventory follow-up](https://odoo.unstaticlabs.com/odoo/project.task/3473)
tracks those differences; the existing v3 admission contract remains required.

## Cleanup and the next release

[Cleanup receipt](../../artifacts/final-batch-20261112/cleanup.json) records the
verified root-tier QA filestore relocation, stopped QA execution, exact
unreferenced QA image retirement and optional unused Restic reader-cache
retirement. Both PG datasets, original baselines, materialized source/cache
snapshot, evidence and current/staging/production/cached rollback images remain.
No global prune or production mutation occurred.

Two component inputs change: Distribution `f3cd48ac327515cbc88ed84905242a79c434c409d9f6bf4916d0bcdff5c9caef`
and backup-tool `b4eae4804f86e70a71a2224766b1d430508814b05d3b1ea4f7d48b4da111e7f5`.
The other four component inputs are unchanged. Do not assume hosted publication
will reproduce the QA layer hashes. The capacity plan credits no QA image or
unproven immutable-base reuse: full measured Distribution plus active
backup-tool reference sizes are 2,735,430,726 bytes, with a combined 256 MiB
growth/filesystem/log allowance, for 3,003,866,182 bytes. Published registry
pulls need no extra full OCI input archive. After scoped cleanup, observed
bulk free space was 19,153,113,088 bytes, covering that plan above the existing
15 GiB reserve by 43,119,546 bytes. Recheck fresh capacity and the actual
published artifacts; stop and replan if the envelope or native guard refuses.
Do not relax the reserve or delete foreign or rollback resources.

This report-only change is outside the Docker source context and component
inputs. Product/source manifest proof belongs to `80a3`; the later report head
records evidence only. Actual staging and production admission remain delivery
work. The deployed Pillow advisory remains open until the fixed runtime is
admitted; the separate python-jose advisory is unaffected.
