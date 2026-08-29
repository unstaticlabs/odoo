# Post-migration continuous operations

## Status and authority

This is the permanent release, coordinated-backup, rehearsal, deployment and
recovery foundation after Community production admission. On this branch it is
**implemented but inactive**: `agent/policy.json` remains
`migration-transition`, continuous deployment is disabled, no Komodo schedule
is enabled, and no live GitOps repository is changed.

The one-shot Online-to-Community migration remains separate. The historical
Online dump is never a rollback source after admission. Every copied,
rehearsal, recovery and non-production Odoo runtime keeps
`USL_EINVOICE_LIVE_ENABLED=0` and `USL_EREPORTING_LIVE_ENABLED=0`.

## Chosen ownership model

The controller and validators live in this repository and ship in the same
immutable operations image as backup/restore tooling. Komodo invokes one
versioned stage at a time. The alternative was to encode the state machine in
the GitOps repository. That would be shorter initially, but would duplicate
window, rollback and evidence semantics outside the qualified release. The
chosen model keeps GitOps responsible only for credentials, the append-only
ledger, Resource Sync, stack deployment, schedules and alerts.

State and evidence are atomic JSON files outside Odoo. Infisical values are
injected at runtime and never copied into releases, cohorts or run records.

## Immutable release contract

Post-merge workflow `.github/workflows/product-image.yml` runs only for a push
to `19-usl`. GitHub does no pull-request or merge-group compute and receives no
production credential or SSH access. It publishes all repository-built
production runtimes with commit tag `sha-<source-sha>` and records only digest
references as deployable identities:

| v3 role | OCI repository | Source |
| --- | --- | --- |
| `odoo_distribution` | `ghcr.io/unstaticlabs/usl-odoo` | root `Dockerfile`, `distribution` target |
| `operations_tool` | `ghcr.io/unstaticlabs/usl-odoo-operations` | `docker/operations.Dockerfile` |
| `paperless_overlay` | `ghcr.io/unstaticlabs/usl-paperless-ngx` | `deploy/documents/paperless-ngx/Dockerfile` |
| `document_renderer` | `ghcr.io/unstaticlabs/usl-document-renderer` | pinned renderer gitlink |
| `native_sign_dss` | `ghcr.io/unstaticlabs/usl-sign-dss` | `services/usl-sign-dss/Dockerfile` |

External Pocket ID, PostgreSQL, Valkey, Ollama, Gotenberg and Tika are pinned
upstream dependencies, not repository-built artifacts. The QA-only patched
Pocket ID image is not part of the external-Pocket-ID production topology.

Artifact `distribution-release-<source-sha>/distribution-release.json` has
schema `usl-distribution-release/v3`. Validate it with:

```bash
python3 scripts/distribution_release.py validate distribution-release.json \
  --commit <expected-source-sha>
```

The root has exact keys `schema`, `source`, `artifacts`, `product`,
`component_sources`, `build`, `artifact_plan`, `upgrade_plan`, `prior_release`
and `contract_sha256`. `artifacts` has exactly the five roles above. Each value
has exact keys `name`, `tag`, `digest`, `digest_reference`,
`source_commit_sha`, `identity_sha256`, `origin`, and `attestations`. The root
checksum covers the canonical JSON contract with `contract_sha256` removed.

`USL_DEPLOYED_RELEASE_RUN_ID` is the sole optional reuse input. It is a
non-secret GitHub repository variable containing the exact successful Actions
run ID admitted by the preceding deployment. The planning job fetches that
run's unexpired `distribution-release-<head-sha>` artifact through the scoped
Actions API, verifies its workflow, branch, success, ancestry, source/build
identity, contract checksum and complete v3 structure, and then supplies its
source SHA to both planners. A missing, malformed, expired, unreachable or
inconsistent input produces build-all plus full-upgrade fallback. It never
causes tag inspection or partial trust.

`artifact_plan` is `usl-artifact-build-plan/v1`. Changed owned inputs rebuild
only their roles; unchanged roles retain the exact previously qualified
descriptor. Foundation/ownership changes and ambiguous paths rebuild every
role. A built artifact uses `built_for_release`. A retained artifact uses
`reused_from_release` and binds the current release, prior source SHA and prior
contract checksum. Its name, commit tag, digest/reference, artifact identity
and digest-bound attestations must exactly equal the complete validated prior
contract. The metadata artifact carries that input as
`candidate-prior-release.json` so downstream validation repeats the proof. No
validator or deployer infers a digest from any tag.

`product.modules` is the sorted canonical product perimeter with manifest
versions. `product.oca` carries the bundle SHA-256 and every pinned repository
identity. `product.action_risk.policy_sha256` carries the reviewed action-risk
identity. `component_sources.document_renderer` carries the separate renderer
gitlink. Every artifact requires OCI SBOM, BuildKit provenance and GitHub
provenance objects with status `generated` and a `subject_digest` exactly equal
to the artifact digest.

## Upgrade plan

`scripts/upgrade_plan.py` emits `usl-upgrade-plan/v1`. Given an explicit prior
deployed SHA it maps changed paths to canonical modules and computes the reverse
dependent closure from target manifests. It emits `none`,
`dependency_closure`, or `full_fallback`. The fallback contains the complete
canonical perimeter when the prior release is unavailable, a foundation or
ownership path changed, a product path is ambiguous, or the dependency graph
cannot be proven.

Foundations include Odoo/addons, requirements, the Distribution Dockerfile,
OCA pins/bundle and action-risk policy. Manifest, hook or security ownership
changes also force the full perimeter. The validated deployed release input is
the ordinary `from_commit_sha`; absent or stale input deliberately falls back
to the full perimeter.

## Coordinated cohort

The cohort manifest is `usl-production-cohort/v1`; validate it with:

```bash
python3 scripts/production_cohort.py recovery-cohort.json
```

The exact root keys are `schema`, `cohort_id`, `created_at`, `release`,
`storage`, `models`, `queues`, `restore_evidence`, `secrets`, and
`contract_sha256`. The checksum is SHA-256 of canonical JSON (UTF-8, sorted
keys, separators `,` and `:`) after removing `contract_sha256`.

`release.artifacts` contains exactly the five v3 digest references. `storage`
contains exactly these units, each with `snapshot_id`, `sha256`, and
`size_bytes`:

```text
odoo_postgresql              odoo_filestore
paperless_postgresql         paperless_media
paperless_data               paperless_search
paperless_vector             paperless_export
paperless_trash              ollama_models
native_sign_step_ca          native_sign_evidence
```

`models` records exact Ollama model names, model digests and archive checksums
and must include the qualified BGE model. `odoo_jobs` and `paperless_broker`
must be drained, have zero pending work and be explicitly non-authoritative.
Brokers are rebuilt from canonical data. Every storage unit needs independent
verified restore evidence from `fresh_isolated_volumes`. `secrets` must equal
`{"provider":"infisical","copied":false}`.

## Deployment run and supervised GitOps hooks

Run state is `usl-deployment-run/v1`; validate it with:

```bash
python3 scripts/deployment_run.py validate deployment-run.json
```

The root keys are `schema`, `run_id`, `mode`, `source`, `schedule`, `state`,
`writers`, `mutation_started`, `production_reopened`, `stages`, `pins`,
`rollback`, `incident`, `created_at`, `updated_at`, and `contract_sha256`.
It uses the cohort checksum rule. `schedule` is fixed to `Europe/Paris`, cutoff
`03:45`, window `04:00`–`07:00`.

`source` keeps both `recovery_cohort_id` (the last independently verified
rollback point) and the current `active_cohort_id` plus
`active_cohort_sha256`. After the snapshot hook writes
`/evidence/snapshot-cohort.json`, the controller validates its exact keys
`cohort_path`, `cohort_id`, `cohort_contract_sha256`, validates the referenced
cohort under `/evidence`, and updates the active identity. Alerts therefore
name the newly captured cohort rather than the prior recovery point.

At 03:45, service `release-cutoff` initializes `release` mode only when
`/contracts/candidate-release.json` exists and is a valid explicit v3 input.
If the candidate retains any artifact, `/contracts/candidate-prior-release.json`
must contain the complete prior contract and `run init` validates it through
`--candidate-prior-release`; a pointer alone is rejected. Otherwise the
controller records `backup_only`. Invalid present input fails closed. Starting
a release after the cutoff records `deferred`. Before production mutation,
reaching 07:00 also defers. After mutation starts, the controller finishes
admission or recovery rather than reopening uncertainty.

`release-cutoff` needs no per-invocation environment. It derives the run ID as
`usl-continuous-YYYYMMDD` from the Europe/Paris service date. Repeating the
service is an idempotent no-op for a compatible existing run and refuses to
overwrite state whose run ID, mode, source identities, or schedule differ.

Canonical Compose source is
`deploy/continuous-operations/compose.yaml`. GitOps vendors it byte for byte and
checks it with:

```bash
continuous-operations compose verify --vendored <vendored-compose.yaml>
```

Every service has profile `operations`; an ordinary stack deployment starts
none. RunStackService invokes exactly one service in this order:

```text
03:45  release-cutoff
04:00  validate -> drain -> quiesce -> snapshot -> restore
       -> rehearse-upgrade -> qualify -> prepare-pins
       -> upgrade-production [candidate commit + Resource Sync +
          DeployStack/readback + Odoo upgrade]
       -> admit [qualification + admitted commit + Resource Sync/readback]
       -> reopen -> record
```

Candidate and admitted GitOps operations are not standalone Komodo procedure
steps. They run inside their named controller hooks so any failure is recorded
and invokes the same recovery boundary. The controller sets
`mutation_started=true` before entering `upgrade-production`; failure during
candidate commit or sync therefore restores the recovery cohort and pins even
if DeployStack was never reached.

In `backup_only`, rehearsal, qualification, pin preparation, production
upgrade and admission are explicit skipped stages. The remaining path still
drains queues, snapshots a cohort, restores fresh isolated volumes, verifies,
reopens writers and records the run.

The shared service mounts are:

| Host environment | Container path | Access |
| --- | --- | --- |
| `USL_OPERATIONS_CONTRACT_DIR` | `/contracts` | read-only releases/cohort |
| `USL_OPERATIONS_STATE_DIR` | `/state` | atomic run state |
| `USL_OPERATIONS_EVIDENCE_DIR` | `/evidence` | pins, GitOps results, evidence |
| `USL_OPERATIONS_HOOK_DIR` | `/hooks` | read-only audited stage executables |
| `USL_GITOPS_CHECKOUT` | `/gitops` | read-only inspection only |

`USL_OPERATIONS_IMAGE` is the exact operations digest. Alert inputs are
`USL_ALERT_RELAY_URL` and file-backed
`USL_ALERT_RELAY_SECRET_HOST_FILE`, mounted at
`/run/secrets/alert_relay_secret`. The request uses `X-Relay-Secret`; its JSON
has exact public fields `schema`, `run_id`, `stage`, `outcome`,
`candidate_release_sha256`, `cohort_id`, and `recovery_status`.

`prepare-pins` emits `/evidence/gitops-pins.json` with exact keys `candidate`,
`deployed`, and `recovery`. Each value has the exact five artifact-role digest
references. `pins.patch_sha256` is the SHA-256 of that canonical JSON object.
It is not an alternative release schema.

The generic controller code never commits or pushes GitOps. Its audited Infra
hooks consume the pin file verbatim inside supervised controller stages and
atomically write exact-key result files:

```text
/evidence/candidate-gitops.json
/evidence/admitted-gitops.json
/evidence/recovery-gitops.json
```

Each has `expected_commit` and `observed_commit`, both full lowercase Git SHAs,
and they must match. Before invoking `upgrade-production`, the controller marks
mutation started. That single hook appends the candidate ledger commit,
Resource Syncs (`latest_hash`), DeployStacks/readbacks and performs the Odoo
upgrade. It writes the candidate result plus
`/evidence/upgrade-result.json` with exact keys
`expected_deployed_commit`, `observed_deployed_commit`, and
`odoo_upgrade_sha256`; the controller requires both commits to equal the synced
candidate before the stage succeeds. The `admit` hook appends/syncs the admitted
ledger identity and writes the admitted result before admission succeeds.
Automatic rollback validates the recovery file after restoring the cohort and
previous exact pins.

Only in the post-reopen `record` hook may the governed bridge update the
non-secret `USL_DEPLOYED_RELEASE_RUN_ID` repository variable to the admitted
release's `build.workflow_run_id`. It revalidates candidate plus prior sidecar,
matches the run checksum, uses a separately scoped GitHub variable-write token
and reads the value back. Failure is incident-only because writers are already
open; it never triggers automatic rollback. Candidate, admitted and recovery
transitions never advance the variable. This activation is not present on this
branch.

## Stage hooks and accounting admission

Every non-dry-run stage requires executable `/hooks/<stage>` and receives the
current run as `USL_DEPLOYMENT_RUN_JSON`. Hooks are idempotent and write
evidence outside Odoo. `drain` verifies Odoo and Paperless queues; `quiesce`
proves all writers paused; `snapshot` creates the cohort; `restore` uses fresh
isolated volumes; `rehearse_upgrade` runs the planned `-u` set; and `qualify`
runs affected clean-install, upgrade and repeated-upgrade checks.

Qualification and admission include database/filestore parity, multi-company
isolation, attachment access, balanced posted moves, immutable posted entries,
fiscal/tax lock dates, journal sequencing, reconciliation links, analytic and
currency semantics, tax exigibility, evidence/chatter, representative reports
and FEC, plus action-risk admission. No control is relaxed to pass deployment.

The exact mount, network, credential-file, generic evidence and per-hook
responsibility contract is
[`operations/contracts/continuous-operations-hooks-v1.md`](../../operations/contracts/continuous-operations-hooks-v1.md).
This branch intentionally ships no live host overlay or hook implementation;
the controller's `validate` stage refuses an incomplete set, so disabled
scaffolding cannot masquerade as a protected deployment.

## Failure and retention rules

Before mutation, failure reopens safely and records `failed_pre_mutation`.
When writers were paused, this requires successful `pre_mutation_verify` and
`pre_mutation_reopen` hooks; the controller never flips the JSON state alone.
Because production data has not mutated, this path verifies and reopens the
current state instead of performing a needless destructive restore.
After mutation but before candidate writers reopen, the controller calls
`rollback_restore_cohort`, `rollback_restore_pins`, `rollback_verify`, and
`rollback_reopen`. Recovery uses the previous cohort and exact recovery pins.
If rollback fails, writers remain paused and an incident decision is required.
After candidate writers reopen, automatic rollback is forbidden and later
failure records `incident_required`.

At or after 07:00, a paused but unmutated run performs the verified
pre-mutation reopen and defers. A mutated, not-yet-reopened run enters automatic
cohort/pin recovery immediately. A reopened run never performs automatic
destructive rollback.

`scripts/retention_policy.py` produces plans only. It retains 14 daily, 8
weekly and 12 monthly cohorts. It refuses a prune plan if any input lacks an
independent verified restore, and lists an expired cohort for deletion only
after its append-only deadline. Deletion remains a separate audited operation.

## Authoritative commands

```bash
make dev-up
make dev-down
make test MODULES=usl_accounting,usl_sign
make qa
make release-verify
make migration-legacy-verify
```

The last command is legacy-only and is delivered by the gated post-migration
cleanup through `scripts/migration-legacy`; this branch reserves the interface
without introducing a competing overlay. Detailed historical targets remain
compatibility helpers, not a second operator command surface.
