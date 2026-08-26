# Seamless Paperless replay onto saas~19.3

## Scope and preserved references

The active `codex/fix-seamless-paperless-documents` branch was rebuilt from
`origin/19-usl` at
`e3b64c209acf0c4f50baa1a9ee519d8eb2c9b621`. The pre-rewrite feature tip is
preserved locally and on `origin` as
`archive/fix-seamless-paperless-documents-pre-saas-19.3-20260824` at exactly
`d2748787c707cc947d70d0a03c2607060e311478`.

Before final publication, `origin/19-usl` advanced by fast-forward to
`f302ae6cdb43b47e1bb2c705e1f4f716a27ce7d5`. Merge commit
`8658e0bec4a2ca0783b15f73b8b1701e343ec4a9` integrates that exact successor.
Its first parent is the completed rebuilt feature and its second parent is the
current mainline tip, so both histories remain directly reviewable and no
divergent saas~19.2 ancestry was introduced.

The rebuild does not merge the feature branch's divergent saas~19.2 ancestry.
Its product intent is replayed as eight ordered commits:

| Original commit | Replayed commit | Intent |
| --- | --- | --- |
| `2218a5a93ee` | `212536de3e3` | Archive native business attachments |
| `9c3f8960145` | `a388a929e6b` | Reconcile native attachment archives during migration |
| `48ba6e6df24` | `f67d367f4f0` | Define the seamless archive operating model |
| `1aa149d34bb` | `54db907d7ce` | Harden archive reconciliation |
| `bb1d62107ab` | `47672317cee` | Preserve contextual QA tags |
| `cfc07641ffc` | `3c76468b133` | Merge duplicate archive policy safely |
| `afc5269bfcf` | `13387b3d51a` | Clear superseded archive warnings |
| `d2748787c70` | `ae98a401fcd` | Match archive content and metadata |

Follow-up commits correct replay-specific integration defects without changing
the feature boundary: migration-stage ordering, saas~19.3 attachment binary
access, isolated recovery validation, and Trash ownership handoff from the
temporary migration identity to the runtime integration identity. Repeated
finalization also initializes its installed-only module arrays explicitly for
the Bash version shipped by the remote Mac.

## Conflict-resolution decision

Three credible approaches were considered:

1. Merge the archived branch. This preserves Git ancestry mechanically, but it
   also imports divergent saas~19.2 mainline history and makes it difficult to
   prove which newer distribution changes win. It was rejected because the
   branch must remain a descendant of current `19-usl` without a divergent
   mainline merge.
2. Transplant the archived final tree as one squashed commit. This makes the
   resulting tree straightforward to compare, but discards the meaningful
   product sequence between native attachment archiving, migration support,
   operational documentation, and later hardening. It was rejected because it
   would reduce reviewability and obscure conflict decisions.
3. Replay the eight branch-specific commits in order and resolve each against
   saas~19.3. This was selected. It preserves product history while allowing
   newer mainline behavior to remain authoritative.

Two credible alternatives were then considered when current `19-usl` advanced:

1. Rebase every already-reviewed replay and follow-up commit onto `f302ae6cdb43`.
   This would produce a linear history, but it would rewrite the active feature
   a second time and require another forced remote replacement. It was rejected
   because the current mainline is a direct fast-forward successor and the
   rebuilt feature history is already independently reviewable.
2. Merge the exact fast-forward successor once. This was selected because it
   preserves both parent histories, keeps the eventual feature publication a
   normal fast-forward from its current remote tip, and still excludes the
   archived branch's divergent saas~19.2 ancestry.

Shared conflicts used current mainline infrastructure and French wording while
retaining the newer Documents product implementation and module-version
sequence. The release-clone and portable-candidate sanitizers were combined
because they protect different delivery paths; dropping either was rejected.
Paperless import/export writers newly added by mainline were aligned with the
existing runtime-user ownership rule. Mainline's task-count guard was retained
but corrected to use Paperless 3.0.5's native `PaperlessTask` model rather than
the unavailable `django_celery_results` package. Mainline remained authoritative
for Expense behavior, assets, security, translations, and version history. The
feature's pre-merge `usl_documents` dependency was retained because its
`models/documents.py` adapter directly inherits the Documents link mixin.

## Retained and superseded changes

The following branch state was retained:

- native business-attachment archiving and linked-record behavior;
- one-shot archive reconciliation under `migration/`, including resumability
  and technical evidence outside the delivered product registry;
- content and metadata matching, duplicate consolidation, warning cleanup,
  and contextual QA-tag behavior;
- user-facing documentation and French translations for the retained product
  behavior;
- the original eight-commit product sequence.

The following old-branch details were superseded rather than replayed literally:

- saas~19.2 manifest versions and migration directories were retargeted to the
  current saas~19.3 module versions and migration sequence;
- old single-company assumptions were dropped in favor of current multi-company
  visibility, stable cross-company archive identity, and metadata-cache reuse
  after archive reset;
- Paperless 3.0.4 text and assertions were dropped because current mainline
  pins and validates Paperless 3.0.5;
- older reconstruction-stage ordering and Docker compatibility assertions were
  dropped where current `19-usl` already owns the newer accounting,
  infrastructure, and reconstruction workflow;
- legacy attachment byte access was replaced by the saas~19.3 `BinaryValue`
  contract rather than weakening current binary-field behavior;
- existing saas~19.3 translations were kept and the feature translations were
  merged additively instead of replacing the catalog.

Accounting and Expense code already on `19-usl` remains unchanged except where
the Documents integration explicitly consumes it. Native Sign and Expense
Analytics are outside this replay and were not part of conflict resolution or
validation.

## Parity evidence

Use both history and tree checks before publishing:

```bash
git range-diff e0ab11489fcadfccf23699fcb7af6577760fdd6d..d2748787c707cc947d70d0a03c2607060e311478 \
  e3b64c209acf0c4f50baa1a9ee519d8eb2c9b621..ae98a401fcd
git rev-list --merges e3b64c209acf0c4f50baa1a9ee519d8eb2c9b621..HEAD
git diff --check e3b64c209acf0c4f50baa1a9ee519d8eb2c9b621..HEAD
```

The range comparison accounts for every original branch-specific commit.
Before `8658e0bec4a`, the empty merge list proves that no divergent mainline
ancestry was introduced during replay. The one final merge has exactly
`f302ae6cdb43` as its second parent; it is the required current `19-usl`
fast-forward successor, not the archived saas~19.2 lineage.
An independent union-of-changed-paths comparison leaves only the intentional
saas~19.2-to-saas~19.3 migration-directory renames and the old
`accounting_compat/tests/test_release_identity.py` path. That former assertion
targeted a Dockerfile stage/layout superseded by the current distribution
infrastructure; it carries no Documents product state. No old branch-specific
product path is absent from the replay.
The final tree must additionally pass the product/migration boundary, module
install and repeated-update checks, Documents unit/browser tests,
multi-company acceptance, deterministic smoke reconstruction, and recovery
rehearsal before the active remote ref is replaced.

## Source-completion corrections

Four scoped follow-up commits preserve the replay boundary while correcting
issues exposed by the authorized full source run:

- `581138cdd19` reconciles an old failure only when a later archive has the
  exact checksum and metadata, preserves remote failure details, polls the
  oldest work first, classifies unsupported HTML and tiny placeholder images,
  and separates migration access authority from the historical submitter;
- `931d61be05a` accounts for both field-backed and ordinary native attachments
  with explicit domains and limits elevated access to the trusted migration
  operation environment;
- `6b3d548e06a` keeps all raw operation failures visible but blocks release only
  for a failed operation whose attachment has no archived or explicitly
  excluded final ledger outcome.
- the final dependency correction restores `usl_documents` to
  `usl_expense_batch` on top of mainline's newer module version and assets. A
  whole-manifest mainline resolution was rejected because it left the retained
  Documents adapter importing a model before its owning module loaded. Moving
  that adapter to a new optional bridge module was also considered; it would
  avoid making Documents mandatory for Expense Batches, but would introduce a
  new production module, data-ownership transition, and broader review surface
  during a bounded history replay. Restoring the adapter's original explicit
  dependency is the smaller and already-proven final-state resolution.

Credible alternatives were rejected at their authority boundaries: global ACL
bypass versus a sudo-only backfill context, implicit attachment filtering
versus explicit exhaustive domains, broad failed-root reuse versus exact
fingerprint reconciliation, and mutating audit rows versus a final-ledger-aware
qualification gate. The selected corrections add no old mainline ancestry and
do not replace current saas~19.3 multi-company identity, metadata-cache,
translation, dependency, module-version, Accounting, Expense, infrastructure,
or reconstruction state.
