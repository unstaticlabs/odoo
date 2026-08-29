# Evolved migration cohort promotion

This migration-only workflow promotes the coordinated state that has evolved
during the approved local working period. It does not replace or amend the
immutable Online-source migration candidate. The source candidate remains the
proof that the frozen Online package reconstructs deterministically; the
evolved cohort is the actual Odoo, Paperless, Ollama and Native Sign state to
transfer after the final local cutoff.

The workflow is not part of the delivered Odoo runtime. It adds no model,
field, module dependency or source provenance to `custom-addons/`.

## Architecture decision

Three approaches were compared.

1. A new outer, digest-bound cohort over the existing Documents cohort plus
   Native Sign state is selected. Current post-work Accounting, Documents,
   security and queue controls are bound to the exact source-candidate
   fingerprint and exact merged release identity. An independent restore must
   prove parity from fresh volumes without OCR, re-ingestion, vector rebuild or
   model download.
2. Reusing the immutable Online-source candidate as the transfer artifact was
   rejected. Its pre-work counts and fingerprints correctly describe the
   deterministic migration result, not Valentin's later authorized business
   changes.
3. Editing the old candidate's baselines or accepting an evolved database by
   count tolerance was rejected. Both approaches erase provenance and can hide
   lost Accounting or Documents state.

For macOS capture, the nested Documents cohort uses the qualified native
Ollama service and archives only the exact BGE alias and referenced blobs. The
Linux restore recreates the containerized Ollama volume from that archive.
This preserves model identity while avoiding Docker inference on the Mac and
does not transfer native Ollama identity keys or unrelated models.

## Private bundle layout

Create the unencrypted staging root under a private temporary directory with
mode `0700`. Every directory and file in it must remain respectively `0700`
and `0600`.

```text
<bundle>/
  documents/                    accepted Documents release cohort
  sign/                         deterministic Step CA, DSS and evidence archives
  evidence/
    source-candidate-manifest.json
    release-identity.json
    current-controls.json
    sanitation.json
    security-gates.json
    independent-restore.json
  configuration/
    non-secret-runtime.json
    required-secret-names.json
    restore-instructions.md
    admission-instructions.md
    rollback-instructions.md
```

The configuration directory is declarative and non-secret. Private keys,
passwords, tokens, Pocket ID state, browser sessions, local identities and
provider credentials are rejected. `required-secret-names.json` lists only
uppercase environment-variable names. Production values are provisioned from
the target secret store after restore.

## Evidence contract

`current-controls.json` has schema
`usl-evolved-transition-controls-v1`, status `passed`, and binds:

- the source candidate fingerprint and manifest SHA-256;
- the exact clean release-identity digest;
- the nested Documents manifest digest;
- balanced posted debit and credit totals;
- zero unexplained outbound, Documents, Paperless, Sign, authorization and
  multi-company blockers.

Sanitation, security-gate and independent-restore evidence bind the same
candidate, release, Documents digest and canonical current-controls digest.
Sanitation must be clone-only: the evolving local source is never modified.
The independent restore must use fresh, distinct volumes and report exact
Accounting, Documents, Paperless, Sign, vector and Tantivy parity with zero
OCR or re-ingestion submissions, no vector rebuild and no model download.
It also binds the pre-seal component fingerprint, including the exact Sign
manifest digest.

This current evidence deliberately does not compare post-work business counts
to the obsolete pre-work candidate counts.

## Capture and seal

Writers must already be frozen and the nested Documents cohort must be built,
independently restored and accepted. Capture Native Sign only from coordinated,
quiesced mode-`0700` state directories. Then inspect the unsealed component
identity:

```bash
scripts/migration-cohort-promotion capture-sign \
  /private/tmp/<evolved-bundle> \
  /private/path/step-ca \
  /private/path/dss \
  /private/path/sign-evidence \
  /private/path/release-identity.json

scripts/migration-cohort-promotion inspect-components \
  /private/tmp/<evolved-bundle>
```

Before writing `independent-restore.json`, rehearse those exact components into
fresh recovery resources. Confirm the displayed component fingerprint:

```bash
USL_DOCUMENTS_RESTORE_ENV_FILE=/private/path/rehearsal.env \
USL_DOCUMENTS_RESTORE_DATABASE=odoo_dev \
scripts/migration-cohort-promotion rehearse \
  /private/tmp/<evolved-bundle> \
  usl-odoo-recovery-<rehearsal-id> \
  /private/path/fresh-rehearsal-sign-root \
  <component-fingerprint>
```

Run the current Accounting, Documents, Paperless, Sign, Tantivy/vector and
security parity gates against that recovery project. Only after they pass,
write the bound `independent-restore.json`; complete the remaining evidence
and non-secret configuration; then seal:

```bash
scripts/migration-cohort-promotion seal /private/tmp/<evolved-bundle>
scripts/migration-cohort-promotion verify /private/tmp/<evolved-bundle>
scripts/migration-cohort-promotion accept /private/tmp/<evolved-bundle>
```

Capture rejects empty state, links and special files. Archives normalize
ownership, time and modes so unchanged Sign state has a stable digest. Seal
and acceptance fail on missing files, mode drift, checksum drift, dirty or
mismatched release identity, stale component bindings, incomplete recovery,
or secret-shaped transfer configuration. The distinct pre-seal rehearsal path
breaks the proof cycle honestly: it accepts the nested Documents cohort and
requires exact component-fingerprint confirmation, but it cannot admit or
restore a production-named project. The production restore below still
requires the sealed outer cohort.

Only an accepted bundle may then be encrypted to Roger's approved `age`
recipient, checksummed and transferred over authenticated SSH. Never publish
the unencrypted staging directory.

## Fresh restore and admission

Use a new project name and a private state path outside the cohort. The restore
wrapper permits only `usl-odoo-production-*` or `usl-odoo-recovery-*` projects.
The exact accepted fingerprint is a required human-visible confirmation.

```bash
scripts/migration-cohort-promotion preflight \
  /private/tmp/<evolved-bundle> /private/path/admission-state.json <fingerprint>

USL_DOCUMENTS_RESTORE_ENV_FILE=/private/path/restore.env \
USL_DOCUMENTS_RESTORE_DATABASE=odoo_dev \
scripts/migration-cohort-promotion restore \
  /private/tmp/<evolved-bundle> \
  /private/path/admission-state.json \
  usl-odoo-recovery-<id> \
  /private/path/fresh-sign-root \
  <fingerprint>
```

The nested restore creates fresh Odoo/Paperless/Ollama resources. Native Sign
is extracted into a fresh root with private modes. A failed or partial restore
is preserved for diagnosis and is never overwritten; dispose of it only after
proving exact ownership, then start again with new destinations.

After operators provision production identities and external secrets, record
the ordered gates with private mode-`0600` JSON evidence:

```bash
scripts/migration-cohort-promotion record-configured \
  /private/path/admission-state.json <fingerprint> /private/path/configured.json
scripts/migration-cohort-promotion record-gated \
  /private/path/admission-state.json <fingerprint> /private/path/gated.json
scripts/migration-cohort-promotion record-admitted \
  /private/path/admission-state.json <fingerprint> /private/path/admitted.json
```

Configuration evidence must prove new target identities, external secrets,
that no local Pocket state was transferred, and outbound integrations disabled.
Gate evidence must pass release identity, product boundary, Accounting, Documents,
security, multi-company, Sign, queue and regulatory-off checks. Admission
requires production ingress, rollback readiness, explicit go/no-go approval
and a first coordinated production backup restore.

The state order is fail-closed:
`preflight -> restored -> configured -> gated -> admitted`. Admission
records reset as permanently disallowed, and this interface exposes no reset
operation at any stage. This Coding workflow does not deploy production or
authorize traffic.

## Rollback

Before admission, abandon a failed target as a whole and restore again into
fresh resources. After admission, rollback only to the preceding accepted
coordinated cohort: Odoo PostgreSQL and filestore, Paperless PostgreSQL,
media/data/search/Trash/export, Tantivy/vector state, Ollama/BGE model,
Native Sign Step CA/DSS/evidence and exact images move together. Never roll
back one database, one index or one model volume independently.
