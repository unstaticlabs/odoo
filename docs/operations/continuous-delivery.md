# Continuous delivery

The permanent delivery path starts from two protected branches:

- `19-usl-staging` is the integration branch;
- `19-usl` is the production branch.

Feature work reaches staging through a pull request and merge queue. Production
accepts only `19-usl-staging` or an `urgent/**` pull request. An urgent change is
also mirrored back to staging so the two lines cannot silently diverge.

## Release contract

The `Distribution release` workflow publishes `usl-release/v3` as an immutable
OCI artifact. The artifact binds the source ref and commit, every component
image digest, renderer and tested MCP identity, SBOM/provenance subjects, the
complete product-module inventory, foundation inputs, Ollama model contract,
qualification evidence, and the Odoo-provided MCP support surface. The tested
MCP commit and image remain qualification evidence; they do not permanently
lock an environment to that MCP build. A separately released MCP may advance
when its declared modules, methods, actions, Agent identity schema, Odoo series
and major version all fit the support contract in the admitted Odoo release.

Every installable product module records its version, dependencies, source
digest and stored-model digest. Release construction fails for missing
dependencies. Comparing two v3 releases yields one exact upgrade plan:

- changed installed modules are upgraded;
- installed product modules that depend on them are included;
- a foundation change upgrades every affected installed product module;
- an unknown installed module fails closed;
- source or stored-model changes without a module version change fail closed.

The plan is applied by a one-shot Odoo command. The normal Compose definition
does not contain a permanent `--update` list.

After the candidate passes staging health and read-only control checks,
staging signs that exact plan with a dedicated Ed25519 key. Production holds
only the public key and rejects unsigned, modified, cross-release or
non-staging-qualified plans.

Qualification derives its test plan from the same owned-module dependency
graph. A changed module runs its suite and the suites of owned modules that
depend on it. A core, OCA, Python-constraint or other foundation change runs
every shipped product-module suite. Documentation-only changes still perform
the clean install, repeated upgrade and runtime-boundary checks, but do not
invent unrelated module tests.

`usl-release/v2` remains readable for historical recovery verification. The
first transition to v3 is deliberately conservative: because v2 contains no
module inventory, every installed owned product module is upgraded once. All
later upgrades use exact v3-to-v3 digest comparison.

## Operations interface

`scripts/usl-stack` is the only public runtime interface:

```bash
scripts/usl-stack --target production release plan \
  --active-release /path/to/active.json \
  --candidate-release /path/to/candidate.json \
  --output /path/to/upgrade-plan.json

scripts/usl-stack --target staging release reconcile \
  --source production \
  --snapshot <qualified-production-snapshot> \
  --candidate-release /path/to/candidate.json \
  --upgrade-plan /path/to/upgrade-plan.json

scripts/usl-stack --target production release status
scripts/usl-stack --target production release abort
scripts/usl-stack --target production backup create
scripts/usl-stack --target production backup list
scripts/usl-stack --target production backup verify --snapshot <snapshot>
scripts/usl-stack --target staging health
scripts/usl-stack --target staging smoke
scripts/usl-stack --target staging cleanup plan
```

The operations image includes a pinned Docker client and Compose plugin, so the
fixed host launcher does not depend on whatever client happens to be installed
inside another application image.

## Restore and admission

A restore creates labelled volumes and a private materialization network for a
new generation. It verifies the source snapshot and original release, restores
the databases and durable resources, reuses compatible OCR, previews, Tantivy
and vectors, applies the exact candidate plan, neutralizes staging, and starts
the candidate only after offline materialization succeeds.

Production Sign secrets are restored only to a production generation. Staging
keeps its own Pocket ID, Sign and runtime secrets and never mounts production
Sign PKI or MCP OAuth grants.

Admission compares captured before/after controls rather than merely checking
that tables are non-empty. Controls are deliberately separated into three
classes:

- business-history controls must remain exactly equal through restore and
  upgrade, including posted Accounting and reconciliation fingerprints;
- release-owned access controls may change, but production must match the
  exact digest signed after the staging upgrade;
- known pending queues may drain while writers are stopped, but may not grow,
  and every failed queue or cron count must remain zero.

Unknown or missing control fields fail closed. The current manifest also
checks attachments and filestore coverage, Documents/Paperless state,
Projects, Platform Billing and TESE. Extending it with semantic Pocket ID
mappings, per-account/currency balances, journal/lock-date controls, detailed
Paperless permissions and Trash, Sign identities, MCP OAuth state, and the
complete cron identity/lag policy remains an activation gate. Production
admission is read-only; mutation journeys belong in CI and staging.

One operation lock is held per exact target. Backup stages already persist
checksummed evidence and support bounded resume. The release state-machine
library validates ordered, checksummed transitions and the pre/post-reopen
recovery boundary. Public status and abort operations reject tampered or
malformed state instead of rewriting it. A release backup can leave all cohort
writers stopped after its successful capture; capture failure always restarts
them. The fixed production launcher uses that mode and keeps the gateway in
maintenance on every later failure. Full stage-by-stage fixed-launcher
integration and interruption drills remain an activation gate: a partially
materialized restore is not yet a supported unattended resume point.

## Failure boundary

Before user access reopens, a failed candidate is stopped and the untouched
previous generation is restored. The public gateway remains in maintenance
mode until that previous runtime passes health checks. After access has
reopened, automation must never discard current data by restoring an older
database; recovery requires a forward fix or explicit incident approval.

Capacity admission measures the active generation's allocated persistent
volume and required Sign-state bytes after candidate images are pulled, then
requires that candidate size plus a 15 GiB safety reserve. Existing active and
rollback generations are already charged against measured filesystem free
space and are not double-counted. If the host cannot satisfy the result,
production remains running and no candidate resources are created.

## Activation boundary

The workflows and GitOps procedures are intentionally shipped disabled first.
The desired GitHub branch protection is versioned in two payloads. The common
`USL Distribution` ruleset protects both permanent branches and requires the
aggregate qualification. The additional `USL Production Admission` ruleset
targets only `19-usl`; it separately requires the source-policy and Odoo–MCP
compatibility jobs plus a successful `staging-release` deployment for the
exact candidate commit. A repository administrator applies them after both
permanent branches and the `staging-release` environment exist:

```bash
GH_CONFIG_DIR=/path/to/authorized/gh \
  gh api --method PUT \
  repos/unstaticlabs/odoo/rulesets/21452332 \
  --input operations/contracts/github-usl-distribution-ruleset.json

GH_CONFIG_DIR=/path/to/authorized/gh \
  gh api --method POST \
  repos/unstaticlabs/odoo/rulesets \
  --input operations/contracts/github-usl-production-ruleset.json
```

If `USL Production Admission` already exists, look up its ID and use `PUT` on
`repos/unstaticlabs/odoo/rulesets/<id>` instead of creating a duplicate. The
payloads preserve merge commits, resolved conversations and the all-green
merge queue while preventing an untested staging commit from reaching
production.

Enable them only after the GitHub/GitLab protection and credentials are in
place, the fixed Komodo launcher is installed, and all of these drills pass:

1. qualified production backup and clean staging restore in under 30 minutes;
2. release-A to release-B upgrade using the staging-produced exact plan;
3. injected staging failure with the old generation still available;
4. injected pre-reopen production failure with verified rollback;
5. maintenance routing and all notification destinations;
6. canonical product database boundary and read-only admission.

The Odoo Online export is never a release or rollback source. The admitted
production database remains authoritative.
