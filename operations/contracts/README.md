# Continuous-operations contracts

Published structural schemas:

- `distribution-release-v3.schema.json`;
- `production-cohort-v1.schema.json`;
- `deployment-run-v1.schema.json`;
- `odoo-backup-manifest-v1.schema.json`.

The host-specific, fail-closed stage boundary is
`continuous-operations-hooks-v1.md`.

The executable validators are authoritative because they additionally enforce
cross-field identities, canonical module/role perimeters, ordering, checksums,
queue semantics and state invariants that JSON Schema cannot fully express:

```bash
python3 scripts/distribution_release.py validate <release.json> \
  [--prior-release <complete-prior-release.json>]
python3 scripts/production_cohort.py <cohort.json>
python3 scripts/deployment_run.py validate <run.json>
```

Exact valid fixture factories are committed as `release()` and `cohort()` in
`scripts/tests/test_continuous_operations.py`; the complete initial run fixture
is produced by `deployment_run.initialize()` in the same test. Tampering,
missing-unit, queue, state, deadline and every-stage failure examples are kept
beside them so a consumer can port the same acceptance cases without inventing
a parallel contract.

`usl-distribution-release/v3` has its own canonical `contract_sha256`. A
candidate that declares `reused_from_release` is valid only with the complete
prior v3 contract supplied to the executable validator. The GitHub metadata
artifact names that sidecar `candidate-prior-release.json`; controller cutoff
passes it through `run init --candidate-prior-release`. Historical deployed
contracts can be structurally/checksum validated without recursively fetching
their ancestors, but their own prior pointer and every reuse origin must still
agree exactly.

Hook bridge result files are deliberately small exact-key objects:

```json
{"expected_commit":"<40 lowercase hex>","observed_commit":"<same SHA>"}
```

The snapshot hook atomically writes `/evidence/snapshot-cohort.json`:

```json
{
  "cohort_path": "/evidence/cohorts/<cohort-id>.json",
  "cohort_id": "<cohort-id>",
  "cohort_contract_sha256": "<64 lowercase hex>"
}
```

The referenced cohort must validate, stay under the evidence root, identify the
currently deployed release and match both returned identities before the run's
active cohort is updated.

The mutation hook writes `/evidence/upgrade-result.json`:

```json
{
  "expected_deployed_commit": "<synced candidate Git SHA>",
  "observed_deployed_commit": "<same DeployStack readback SHA>",
  "odoo_upgrade_sha256": "<64 lowercase hex evidence digest>"
}
```

The controller marks mutation immediately before invoking the single supervised
hook that appends/synchronizes candidate GitOps state, performs
DeployStack/readback and applies the Odoo upgrade. A failure at any point in
that hook therefore enters coordinated recovery rather than escaping through a
standalone procedure step.
