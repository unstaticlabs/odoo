# Seamless Paperless replay onto saas~19.3

## Scope and preserved references

The active `codex/fix-seamless-paperless-documents` branch was rebuilt from
`origin/19-usl` at
`e3b64c209acf0c4f50baa1a9ee519d8eb2c9b621`. The pre-rewrite feature tip is
preserved locally and on `origin` as
`archive/fix-seamless-paperless-documents-pre-saas-19.3-20260824` at exactly
`d2748787c707cc947d70d0a03c2607060e311478`.

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

The range comparison accounts for every original branch-specific commit. The
empty merge list proves that no divergent mainline ancestry was introduced.
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

Three scoped follow-up commits preserve the replay boundary while correcting
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

Credible alternatives were rejected at their authority boundaries: global ACL
bypass versus a sudo-only backfill context, implicit attachment filtering
versus explicit exhaustive domains, broad failed-root reuse versus exact
fingerprint reconciliation, and mutating audit rows versus a final-ledger-aware
qualification gate. The selected corrections add no old mainline ancestry and
do not replace current saas~19.3 multi-company identity, metadata-cache,
translation, dependency, module-version, Accounting, Expense, infrastructure,
or reconstruction state.
