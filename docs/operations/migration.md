# Migration operations

`migration/manage` is the only public interface for reconstruction, migration
QA, transition, portable candidates, evolved cohorts, and production cutover.
Do not call files under `migration/internal/` directly.

## Safety boundary

- Mount the frozen Online source database and filestore read-only.
- Never start target Odoo against the source database.
- Keep outbound mail, external providers, bank polling, e-invoice reception,
  and e-reporting disabled during reconstruction.
- Keep private state under `private/migration/` with directories at `0700` and
  secret or identity files at `0600`.
- Stop on a source checksum mismatch, incomplete coverage, unbalanced
  Accounting, unsafe access, failed archive work, foreign Docker ownership,
  or a dirty release identity.
- `stop` preserves data. Destructive operations require an exact confirmation
  value and exact recorded Docker ownership.

The command records each runtime at:

```text
private/migration/runtimes/<runtime-id>/runtime.json
```

The state fixes the Compose project, database, ports, URLs, source digests,
images, Compose files, release commit, resource IDs, Paperless worker settings,
Odoo MCP source/image identity, and Ollama topology. Child stages cannot replace these values with ambient
environment variables. Secrets are stored separately and accept only the
documented allowlist; project names, ports, URLs, database names, and image
identities are rejected in secret files.

## Command surface

```text
migration/manage qa adopt|status|stop|destroy|refresh|login-link
migration/manage transition reconstruct|mark-live|freeze
migration/manage candidate build|verify|status
migration/manage cohort capture|restore|verify|encrypt
migration/manage cutover preflight|stage|configure|gate|admit|reset
```

Use `migration/manage <domain> <action> --help` for exact arguments. The
examples below show the lifecycle; replace identifiers and private paths with
the approved values.

## QA runtime

Adopt an existing runtime without recreating or restarting it:

```bash
migration/manage qa adopt \
  --id <runtime-id> \
  --project <exact-compose-project> \
  --database odoo_dev \
  --source /absolute/path/to/frozen-source \
  --source-sha256 <dump-sha256> \
  --identity-env /absolute/path/to/identity.env \
  --personal-ai-key-file /absolute/path/to/personal-ai-keys.json \
  --odoo-port <port> \
  --gevent-port <port> \
  --pocket-id-port <port> \
  --paperless-port <port> \
  --mcp-port <port> \
  --mcp-repository /absolute/path/to/odoo-mcp \
  --release-commit <40-character-runtime-commit> \
  --ollama native
```

Adoption inspects exact Compose labels, the repository working directory, and
existing resource IDs. It copies only allowed secrets into private runtime
state. It does not restart services or change volumes.

Inspect the full preflight and create an eight-hour link:

```bash
migration/manage qa status --runtime <runtime-id>
migration/manage qa login-link --runtime <runtime-id> --user valentin --ttl 8h
```

The one-time link is printed once. Do not open it for the recipient.

Stop a runtime while preserving all data:

```bash
migration/manage qa stop --runtime <runtime-id>
```

A fresh QA reconstruction never reuses a database, checkpoint, OCR output,
vector state, candidate, or source-derived seed:

```bash
migration/manage qa refresh \
  --runtime <runtime-id> \
  --fresh \
  --confirm REFRESH:<runtime-id>
```

Permanent deletion requires `--confirm DESTROY:<runtime-id>` and is refused
for protected transition state.

## Reconstruction acceptance

The fixed reconstruction sequence covers:

1. source inventory, dump and filestore identity, and attachment disposition;
2. target initialization with outbound work paused;
3. identity, product, HR, Projects, Accounting, TESE, Platform Billing,
   Documents, Sign, and collaboration restoration;
4. migration finalization and delivered-registry cleanup;
5. source-wide, attachment, Accounting, access, multi-company, business,
   Documents, Sign, queue, and product-boundary gates;
6. repeated upgrade, restart, and coordinated recovery evidence.

The source-wide acceptance must include exact Project, task, and stage IDs;
safe sequences; duration ledgers; chatter; attachments; activities; parents;
dependencies; company identities; financial controls; Paperless originals and
derivatives; OCR; Tantivy; vectors; and the pinned BGE model identity.

Browser review is limited to changed user journeys and important cross-system
flows. Automated checks remain the authority for complete data coverage.

## Ollama topology

On macOS, a reachable qualified native Ollama is mandatory when installed.
An installed but unreachable service fails closed. The local Compose topology
omits the Docker Ollama service and verifies the pinned BGE manifest before
embedding work starts.

Linux production uses the pinned containerized Ollama/BGE runtime. The runtime
state records the chosen topology and prevents child stages from switching it.

## Transition, candidate, and cohort

After QA acceptance, create a new transition runtime. Never rename or promote
QA volumes. A new transition requires a full secret file and the same runtime
identity arguments as adoption:

```bash
migration/manage transition reconstruct \
  --runtime <transition-id> \
  --project <exact-new-project> \
  --source /absolute/path/to/frozen-source \
  --source-sha256 <dump-sha256> \
  --secrets-file /absolute/path/to/runtime-secrets.env \
  --personal-ai-key-file /absolute/path/to/personal-ai-keys.json \
  --odoo-port <port> --gevent-port <port> \
  --pocket-id-port <port> --paperless-port <port> --mcp-port <port> \
  --mcp-repository /absolute/path/to/odoo-mcp \
  --ollama native \
  --confirm RECONSTRUCT:<transition-id>
```

After validation:

```bash
migration/manage transition mark-live \
  --runtime <transition-id> --confirm MARK-LIVE:<transition-id>
migration/manage transition freeze \
  --runtime <transition-id> --confirm FREEZE:<transition-id>
```

Once the transition is protected, routine local operations stay behind the
same interface:

```bash
migration/manage transition status --runtime <transition-id>
migration/manage transition start --runtime <transition-id>
migration/manage transition stop --runtime <transition-id>
migration/manage transition login-link --runtime <transition-id> --ttl 8h
migration/manage transition checkpoint \
  --runtime <transition-id> --label before-upgrade
```

`stop` preserves all data. A checkpoint briefly quiesces the exact recorded
runtime, captures both databases, every owned persistent volume, local Sign
and renderer state, and the qualified native Ollama model, then independently
restores both database dumps and verifies every table count. Checkpoints are
private mode-0700 runtime data; they are exact local recovery points, not
sanitized production-transfer cohorts.

`transition-live` and `frozen-read-only` states block reconstruction and test
helpers independently of the Compose project name.

Build and verify the immutable Online-source candidate from a clean checkout
matching the runtime release commit:

```bash
migration/manage candidate build --runtime <runtime-id>
migration/manage candidate verify \
  --runtime <runtime-id> \
  --candidate-dir <candidate-directory> \
  --fingerprint <fingerprint>
```

After the local working period and final freeze, capture the evolved,
coordinated Odoo/Paperless/Ollama/Sign cohort. Verify it, independently restore
it into fresh runtime-owned storage without OCR, re-ingestion, vector rebuild,
or model download, then encrypt it for transfer:

```bash
migration/manage cohort capture --runtime <runtime-id> --bundle <bundle> ...
migration/manage cohort verify --runtime <runtime-id> --bundle <bundle>
migration/manage cohort restore \
  --runtime <fresh-runtime-id> --bundle <bundle> \
  --destination <runtime-private-directory>/restore \
  --fingerprint <fingerprint> \
  --confirm RESTORE:<fresh-runtime-id>
migration/manage cohort encrypt \
  --runtime <runtime-id> --bundle <bundle> \
  --destination <encrypted-output> --recipient <age-recipient>
```

## Cutover

Cutover consumes an immutable candidate or accepted evolved cohort, a recorded
fingerprint, a non-secret JSON configuration, and a separate allowlisted
secret file. `preflight` resolves and stores configuration once; later stages
cannot change it.

Run the state machine in order:

```bash
migration/manage cutover preflight ...
migration/manage cutover stage ...
migration/manage cutover configure ...
migration/manage cutover gate ...
migration/manage cutover admit ... --confirm ADMIT:<runtime-id>
```

`reset` is available only before admission, against exact runtime-owned
resources, with `--confirm RESET:<runtime-id>`. Production becomes canonical
only after admission and the first verified coordinated backup restore.

## Evidence and recovery

Keep source manifests, runtime history, fingerprints, gate results, financial
controls, module and image identities, independent-restore evidence, and
transfer checksums private. A failed stage remains inspectable; do not weaken
a gate, skip data, or silently infer missing business meaning.

Before production admission, recover by restoring the accepted candidate or
cohort into fresh owned resources. After admission, recover only from verified
production backups. The historical Online dump is not a post-admission
rollback source.
