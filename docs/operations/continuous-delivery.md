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
staging signs that exact plan with a dedicated Ed25519 key. A production merge
creates a different release identity because its source ref and commit differ.
Staging therefore signs a second promotion envelope that preserves the original
staging signature and binds both release identities to the same distribution
input, immutable component images, module inventory, foundation, MCP contract,
renderer, and Ollama contract. User-facing release notes may describe the
branch-specific promotion and do not affect the qualified runtime tree.
Production holds only the public
key and rejects unsigned, modified, cross-release, wrong-branch, or unequal
promotion inputs. The bridge changes only the plan's candidate identity; it
cannot change its active release, installed modules, upgrade closure, or reasons.

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
  --upgrade-plan /path/to/upgrade-plan.json \
  --attempt-id <unique-attempt-id> \
  --prepare-receipt /path/to/prepare.json \
  --maintenance-receipt /path/to/maintenance.json

scripts/usl-stack --target staging release plan --promote \
  --upgrade-plan /path/to/signed-staging-plan.json \
  --staging-release /path/to/staging-release.json \
  --candidate-release /path/to/production-release.json \
  --output /path/to/production-promotion.json

scripts/usl-stack --target production release prepare \
  --attempt-id <unique-attempt-id> \
  --candidate-release /path/to/production-release.json \
  --upgrade-plan /path/to/production-promotion.json

scripts/usl-stack --target production release status
scripts/usl-stack --target production release abort --attempt-id <attempt>
scripts/usl-stack --target production backup create
scripts/usl-stack --target production backup list
scripts/usl-stack --target production backup select
scripts/usl-stack --target production backup verify --snapshot <snapshot>
scripts/usl-stack --target staging health
scripts/usl-stack --target staging smoke
scripts/usl-stack --target staging cleanup plan
scripts/usl-stack --target production storage plan \
  --generation gactive --rollback-generation grollback --snapshot <snapshot>
scripts/usl-stack --target production storage status
```

The fixed controller additionally uses a release-bound internal notification
operation after production admission. It can announce only the exact active
64-character release identity. OdooBot posts the reviewed user-facing notes
from that signed release manifest in the dedicated internal **USL Distribution
Updates** Discuss channel. Current and future internal users are subscribed;
portal and public identities are excluded. The post is persistent, links to
technical evidence, and is idempotent for the release identity; it is not a
transient browser popup. Notification failure is retryable operational
evidence and never rolls back a healthy release.
Before promoting a user-visible release, update
`operations/release-notes.json` in the reviewed release PR. The v3 builder
rejects missing, empty, oversized, or structurally unknown notes and binds the
accepted content into the signed release identity.

The operations image includes a pinned Docker client and Compose plugin, so the
fixed host launcher does not depend on whatever client happens to be installed
inside another application image.

## Restore and admission

A restore creates labelled volumes and a private materialization network for a
new generation. It verifies the source snapshot and original release, restores
the databases and durable resources, reuses compatible OCR, previews, Tantivy
and vectors, applies the exact candidate plan, neutralizes staging, and starts
the candidate only after offline materialization succeeds. While the isolated
candidate database is still attached to its temporary private PostgreSQL
container, the controller applies the target's complete scheduled-action
policy through Odoo's ORM. Production receives its explicitly gated set and
staging receives no active jobs. Unknown, missing or ambiguously identified
jobs stop the candidate before the active generation is touched.

Production Sign secrets are restored only to a production generation. Staging
keeps its own Pocket ID, Sign and runtime secrets and never mounts production
Sign PKI or MCP OAuth grants.

`release prepare` is the mandatory pre-downtime gate. It validates the target
secret contract, storage paths and external networks, pulls every immutable
image, measures candidate capacity, verifies the signed plan, and renders the
exact target Compose topology with release images, resource limits, generation
volumes, Sign mounts, and production quarantine settings. It returns a
content-addressed render receipt and does not stop, recreate, or reconfigure a
running service. `release reconcile` repeats this gate so a caller cannot skip
it accidentally. Host releases must supply an immutable GitOps archive root
and its exact 40-character commit. The archive marker, Compose paths, rendered
services, and commit are validated and bound into the preparation receipt;
neither a mutable checkout nor the currently running container labels can
silently select a different topology later in the attempt.

Every rollout attempt has an identity distinct from the desired release. The
controller binds preparation, observed public HTTP 503 evidence, generation,
backup and final admission to that attempt. `release reconcile` requires a
digested `usl-maintenance-admission/v1` receipt for the same target and attempt,
with exact 503 coverage of Odoo HTTP and WebSocket plus every public Paperless
and MCP writer ingress affected by the release,
then consumes the attempt exactly once. Its immutable attempt claim binds the
source snapshot, candidate release, exact signed upgrade plan, archived GitOps
commit and Compose render, preparation and maintenance receipts, baseline, and
new generation into one operation-bundle digest. Quarantine and admission
receipts must carry that same digest; mixing evidence from retries or concurrent
promotions fails closed. In production reconciliation stops at an immutable
`usl-release-quarantine/v1` receipt. The controller must complete every
rollback-eligible check and candidate backup before it calls `release activate`
with that exact receipt. A failed or interrupted attempt is
terminal: after verified recovery, automation must create a fresh attempt and
fresh backup/generation identities. It may never reuse an old pre-release
snapshot merely because the desired release is unchanged. Only the immutable
`usl-release-admission/v1` receipt written after final health, smoke,
preservation, and side-effect checks proves admission; an active image plus a
healthy endpoint is not sufficient.

Production candidates remain quarantined behind maintenance until activation.
Odoo starts with zero cron threads, a closed SMTP endpoint, disabled regulatory
flags, and a neutralization marker. The candidate database records and disables
the exact active incoming-mail server, while the versioned cron policy may be
checked without any scheduler thread executing it. Paperless starts with every
scheduled external or destructive task disabled. After health, smoke, business
preservation, release-owned controls, and the candidate backup pass, the
controller crosses the irreversible boundary with `release activate`. That
command writes an immutable, attempt-bound forward-only receipt immediately
before its first database activation mutation. A killed invocation resumes the
same idempotent activation, but automatic rollback is permanently refused once
that receipt exists—even if the process stopped before it could write final
admission. The command then explicitly clears
the matching quarantine identity, restores only the recorded approved incoming
server, replaces the quarantine overlay with the admitted environment, recreates
the affected services, and repeats health and smoke. The side-effect admission
check runs while the quarantine overlay is still active, so an invalid SMTP,
inbound-mail, cron, or regulatory contract cannot race a newly started worker.
Any failure before `release activate` rolls back while public ingress remains
closed. Once activation begins, recovery is forward-only because an external
worker may have observed the new state.

Backup evidence records the wall-clock instants immediately before writers are
stopped and after they are healthy again. A capture that deliberately leaves
writers stopped has no synthetic completion time or SLA result; the release
controller must close that interval only when the admitted generation starts.

Admission compares captured before/after controls rather than merely checking
that tables are non-empty. Controls are deliberately separated into three
classes:

- business-history controls must remain exactly equal through restore and
  upgrade, including posted Accounting and reconciliation fingerprints;
- release-owned access controls may change, but production must match the
  exact digest signed after the staging upgrade;
- known pending queues may drain while writers are stopped, but may not grow,
  and every failed queue or cron count must remain zero.

Unknown or missing control fields fail closed. Control manifest v2 adds
fingerprints for company structure, user/company/group authority, Pocket ID
links, Agent ownership and delegated access, balances per company/account/
currency, journal controls, lock dates, group implications, cron policy, and
Paperless document identities, tags, permissions, and Trash. Historical v1
snapshots remain restorable into a v2 runtime, but a v2 snapshot may not
regress to the weaker v1 controls. Counts and fingerprints also cover
attachments and filestore coverage, Projects, Platform Billing, and TESE.
The immutable operations image carries the versioned 55-cron policy. After
candidate convergence, read-only smoke admission independently rejects
unknown, missing, ambiguously identified, unexpectedly active, failed, or
overdue scheduled actions. Production's explicit gate decisions and staging's
fully neutralized state are separate target policy.
Sign service identities and semantic MCP OAuth-vault controls are implemented
as read-only admission evidence. Their live staging, recovery, and rollback
drills remain activation gates. Production admission is read-only; mutation
journeys belong in CI and staging.

Candidate health is measured through target-host loopback ports while the
stable gateway still serves maintenance. Odoo HTTP and websocket probes use
their distinct host ports; Paperless and MCP use their private host bindings.
The fixed controller therefore runs with host networking and must not use a
public route as pre-reopen evidence. Public HTTPS and websocket checks are a
separate post-reopen control-plane gate.

One operation lock is held per exact target, and a second host-wide lock
serializes Odoo, MCP, and recovery procedures. Backup stages persist evidence
and support bounded resume. Interrupted partial capture workspaces are safely
recreated, while completed uploads are reused instead of taking a second
snapshot. The fixed launcher persists every canonical release stage as an
atomic, checksummed JSON record and resumes only where the same attempt has
remained continuously closed and its active baseline is unchanged. It rejects
tampered evidence and will not resume a post-reopen failure as a rollback.
Candidate generation names and pre/post-release backup run IDs are
attempt-derived, so a rollback followed by reopen terminally invalidates them
instead of reusing an older database snapshot for the same desired release.

Public status and abort operations reject malformed state instead of rewriting
it. A release backup can leave all cohort writers stopped after successful
capture; capture failure always restarts them. If an interruption leaves an
unadmitted candidate active, the controller restores and validates the previous
generation. If it reopens access, the attempt becomes terminal and the next run
must prepare a fresh attempt, capture fresh backup identities, and create a new
generation. Full live interruption drills remain an activation gate.

## Failure boundary

Before user access reopens, a failed candidate is stopped and the untouched
previous generation is restored. `release abort` requires the exact consumed
attempt and is permitted only while the target gateway already has its
maintenance marker. It refuses any generation with a final admission receipt,
recognizes an already-restored attempt idempotently, and reconstructs the one
recorded rollback generation from validated state, starts it, and runs both
health and read-only smoke admission. The fixed production launcher removes
maintenance only after that proof; failed or ambiguous recovery leaves the
HTTP 503 in place. After access has reopened, automation must never discard
current data by restoring an older database; recovery requires a forward fix
or explicit incident approval.

Persistent resources declare one of three storage tiers. On production hosts,
`bulk` is the Hetzner EXT4 Volume mounted at `/srv/storage`, `database` is the
local-NVMe `/srv/db`, and `local` covers small host security state. PostgreSQL,
the Paperless broker and the MCP OAuth vault use generation-specific
bind-backed Docker volumes below
`/srv/db/usl-odoo/<target>/generations/<generation>/<role>`. Images, layers,
filestores, Paperless content and ordinary named volumes remain Docker-managed
on the bulk tier. Active, candidate and rollback database paths are therefore
independent; a static singleton database bind is forbidden.

`storage plan` is read-only and measures every adopted source, its tier and the
two-copy requirement needed to create an initial active and retained rollback
generation. `storage adopt` additionally requires persistent maintenance,
stopped cohort writers, a 64-character recovery snapshot and an exact
target/generation/snapshot confirmation. It copies with archive, ACL, xattr,
hard-link, numeric-ID and sparse-file preservation, checksum-verifies each
copy, and writes `active.json` only after both generations exist. `storage
status` rejects legacy database volumes, wrong bind options, shared bulk/DB
filesystems, or Docker/containerd roots outside `/srv/storage`.

Capacity admission groups tiers by the filesystem device that actually backs
their paths. `/srv/storage` must hold the measured candidate bulk delta plus
the full 15 GiB reserve. Local NVMe must hold the transactional/local candidate
delta plus its 2 GiB hard reserve; the existing 8 GiB warning remains. A single
filesystem shared by multiple declared tiers receives its reserve once, and
active/rollback bytes already represented by free space are never added again.
Checks run before and after image pulls and again before activation. Cleanup
removes a transactional directory only after its labelled Docker volume is
removed, it is outside active/rollback, its exact generation-derived bind is
verified, and the database and bulk devices are distinct.

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
