# Continuous operations hook interface v1

This is the fail-closed host integration boundary for
`ghcr.io/unstaticlabs/usl-odoo-operations`. The branch ships no live hook
implementation, credential or external-resource overlay and keeps the Komodo
schedule disabled. A manual non-dry-run fails at `validate` unless every
mode-required executable and mount exists. Infrastructure may implement these
hooks but must not change their order or state semantics.

## Invocation and evidence

Each hook is an executable `/hooks/<name>`. It receives the complete current
run in `USL_DEPLOYMENT_RUN_JSON`, must be idempotent for its `run_id`, must not
print secrets and must atomically write
`/evidence/stage-results/<name>.json` before exiting zero:

```json
{
  "stage": "<exact hook name>",
  "run_id": "<current run_id>",
  "status": "succeeded",
  "evidence_sha256": "<64 lowercase hex>"
}
```

The evidence digest identifies a detailed append-only dossier under
`/evidence`, not stdout. A missing, stale, mismatched or unsuccessful result
fails the stage.

Specialized atomic outputs are additional to the generic result:

- `snapshot` writes `/evidence/snapshot-cohort.json` and its referenced valid
  `usl-production-cohort/v1` file;
- `upgrade_production` writes `/evidence/candidate-gitops.json` with exact keys
  `expected_commit`, `observed_commit`, and writes
  `/evidence/upgrade-result.json` with exact keys
  `expected_deployed_commit`, `observed_deployed_commit`,
  `odoo_upgrade_sha256`;
- `admit` writes `/evidence/admitted-gitops.json` with exact keys
  `expected_commit`, `observed_commit`;
- `rollback_restore_pins` writes `/evidence/recovery-gitops.json` with exact
  keys `expected_commit`, `observed_commit`.

## Required host overlay

The canonical Compose already mounts contracts read-only, state/evidence
read-write, hooks read-only and the GitOps checkout read-only. The Infra overlay
adds only explicitly named production/rehearsal resources:

- Odoo PostgreSQL network and database credential file;
- Odoo filestore volume;
- Paperless PostgreSQL network/credential and media, data, search, vector,
  export and Trash volumes;
- Ollama model volume and qualified BGE identity;
- Native Sign Step CA and evidence volumes;
- append-only backup object-store credential files;
- fresh run-owned rehearsal database/volume/network targets;
- file-backed Komodo and GitLab credentials only for hooks listed below.

No hook receives an Infisical export or secret bundle. The overlay maps each
secret to one mode-0400 file and passes only its path. The controller persists
neither path contents nor credentials. Do not mount the Docker socket; stack
control uses the scoped Komodo API.

Canonical path environment is:

```text
USL_OPERATIONS_CONTRACT_ROOT=/contracts
USL_OPERATIONS_STATE_ROOT=/state
USL_OPERATIONS_EVIDENCE_ROOT=/evidence
USL_STAGE_RESULT_DIR=/evidence/stage-results
USL_GITOPS_CHECKOUT=/gitops
```

Resource/secret path names supplied by the overlay are:

```text
USL_ODOO_DB_PASSWORD_FILE
USL_PAPERLESS_DB_PASSWORD_FILE
USL_BACKUP_RESTIC_PASSWORD_FILE
USL_BACKUP_R2_ACCESS_KEY_FILE
USL_BACKUP_R2_SECRET_KEY_FILE
USL_KOMODO_API_KEY_FILE
USL_KOMODO_API_SECRET_FILE
USL_GITLAB_LEDGER_TOKEN_FILE
USL_GITHUB_VARIABLE_TOKEN_FILE
```

The overlay also supplies explicit external network/volume names using the
existing production environment variables; hooks reject unset, default,
foreign-owned or mutable/floating identities. Every rehearsal/recovery Odoo
container forces both live e-invoice flags to `0`.

## Stage responsibilities

| Hook | Exact responsibility | Production resources | Orchestration credentials |
| --- | --- | --- | --- |
| `validate` | Validate v3 release, recovery cohort, run, pins, volume ownership, networks, append-only repository and service health; prove all side-effect flags | read-only all | read-only Komodo API |
| `drain` | Stop intake; drain Odoo jobs and Paperless broker to zero; record brokers non-authoritative | Odoo/Paperless APIs and DB read | none |
| `quiesce` | Stop cron, consumers and every writer; prove no foreign DB writers | Odoo/Paperless/Sign services | scoped Komodo API |
| `snapshot` | Create one coordinated cohort covering every v1 unit and checksum | all canonical data volumes, DBs, object store | none |
| `restore` | Restore every cohort unit into fresh isolated run-owned volumes and networks; verify independent parity | backup store plus rehearsal resources | none |
| `rehearse_upgrade` | Apply the release upgrade plan to the restored Odoo clone; repeat identically | rehearsal Odoo DB/filestore | none |
| `qualify` | Run affected clean install, upgrade/idempotence, boundary, queue, accounting and service checks | isolated qualification resources | none |
| `prepare_pins` | No external mutation; controller writes exact pin patch | evidence only | none |
| `upgrade_production` | After the controller marks mutation started, append the candidate ledger commit from the exact pin file, Resource Sync, call DeployStack, read back the deployed hash, then apply the planned Odoo upgrade while writers stay paused | GitOps ledger, production stack and Odoo DB | scoped GitLab and Komodo APIs |
| `admit` | Verify deployed services, cohort parity, module versions and accounting/action-risk controls; append the admitted ledger commit and Resource Sync/readback before succeeding | GitOps ledger and production read/health | scoped GitLab and Komodo APIs |
| `reopen` | Re-enable verified writers, consumers and cron only after admission | production services | scoped Komodo API |
| `record` | Seal run/evidence and send non-secret outcome; in an activated release run, revalidate candidate plus prior sidecar, require its contract checksum to equal `run.source.candidate_release_sha256`, update/read back only `USL_DEPLOYED_RELEASE_RUN_ID` from exact `build.workflow_run_id` after reopen | evidence/alert relay and GitHub Actions repository variable endpoint | scoped GitHub variable-write token only |

`pre_mutation_verify` and `pre_mutation_reopen` verify unchanged production and
reopen paused intake, writers, consumers and cron after a pre-mutation
failure/defer. They run once `drain` has started even when the writer-state
marker is still `open`, because a partial drain may already have stopped
intake. They need the same read access and scoped Komodo credential as
`quiesce`/`reopen`, never GitLab.

`rollback_restore_cohort` restores the exact recovery cohort using data/object
store credentials. `rollback_restore_pins` is the only hook that needs
`USL_GITLAB_LEDGER_TOKEN_FILE`, `USL_KOMODO_API_KEY_FILE`, and
`USL_KOMODO_API_SECRET_FILE`: it appends the
recovery pin commit, Resource Syncs, DeployStacks, reads back the commit and
writes `recovery-gitops.json`. `rollback_verify` uses production read access;
`rollback_reopen` uses the scoped Komodo credential.

Komodo Core authentication always reads both header values from files:
`X-Api-Key` from `USL_KOMODO_API_KEY_FILE` and `X-Api-Secret` from
`USL_KOMODO_API_SECRET_FILE`. Neither value is accepted inline.

There are no standalone candidate or admitted bridge procedure stages. The
`upgrade_production` and `admit` hooks receive both scoped credentials because
the controller must supervise their failures. Both hooks no-op when they are
skipped in `backup_only`. No other forward stage receives the GitLab token.

The GitHub token is file-backed and scoped only to the repository Actions
variable endpoint. It is mounted only into `record`, never into another forward
or recovery hook. `record` must read back the variable before succeeding. A
failure occurs after production has reopened, so the controller records
`incident_required`; it must not automatically roll production back. The old
run ID remains safe because the next publisher conservatively rebuilds and
full-upgrades from a stale or unavailable input. Candidate, admitted, rollback
and `backup_only` paths never update the variable. This credential and hook are
an activation requirement, not an enabled resource on this branch.
