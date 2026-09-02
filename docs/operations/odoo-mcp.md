# Odoo MCP operations

Odoo MCP is a first-class service in the USL Distribution stack. It runs as a
non-root Node 24 container on the private Compose network, exposes Streamable
HTTP at `/mcp` and `/mcp/<profile>`, and calls Odoo over the private
`http://odoo:8069` origin. Odoo remains authoritative for users, companies,
access rights, record rules, public methods, and transactions.

## Release identity

The accepted external source is pinned in `deploy/odoo-mcp/release.json` by
repository, ref, full commit, build tag, immutable image digest, and
compatibility-contract digest. The ref and commit both contain the exact MCP
commit so the release remains valid after the MCP repository's `main` branch
advances. Update every identity together; never retag an existing image.

Verify, test, and build without switching or modifying the external checkout:

```bash
scripts/odoo-mcp verify --repository /absolute/path/to/odoo-mcp
scripts/odoo-mcp test --repository /absolute/path/to/odoo-mcp
scripts/odoo-mcp build --repository /absolute/path/to/odoo-mcp
```

The helper exports the pinned Git object into a temporary build context. Dirty
or unrelated work in the external checkout cannot enter the image. The image
must carry matching OCI source and revision labels and resolve to the recorded
digest.

The compatibility gate compares the pinned MCP server version, required Odoo
modules, and exact specialized RPC calls with the qualified Odoo action
surface. It fails before deployment when either repository changes its shared
contract without updating `deploy/odoo-mcp/compatibility.json`.

## Runtime and authentication

The release runtime resolves the MCP port, public origin, Odoo target,
database, image, source identity and secret-file paths once. Local macOS uses
`http://localhost:<port>` on loopback. Production uses an HTTPS origin
through the existing ingress network; only the reverse proxy should reach the
container port.

Each MCP request uses a governed Agent credential through either:

- `X-Odoo-Url`, `X-Odoo-Database`, and `X-Odoo-Api-Key`; or
- the hosted OAuth flow backed by the encrypted SQLite vault.

Direct keys are request-local and are not stored. OAuth enrollment stores the
Agent credential in the encrypted vault. The human authorizes the client, but
the distinct Agent remains the Odoo actor for every request. Human API keys,
suspended Agents and expired credentials are rejected before tools are
exposed and again before each capability runs.

OAuth credentials are encrypted with a dedicated 32-byte key in the persistent
`odoo-mcp-oauth-data` volume. The Better Auth and encryption secrets are
independent mounted files. Never place either secret or an Odoo API key in Git,
runtime URLs, logs, or deployment instructions.

Create and rotate credentials in **My Agents**. Application permissions belong
to the Agent, not to its individual keys. The Agent's delegated application
groups, its owner's current authority, active companies, Odoo record rules and
the Distribution safety policy jointly determine what MCP tools may read or
change. Agents cannot manage identities or credentials and cannot perform
protected irreversible actions, even when Settings is delegated.

New Agents default to universal owner-scoped read-only access. In that mode MCP
publishes only tools backed by exact `read_only` action-policy entries, plus the
guarded message, activity, self-follow and Documents download-grant tools.
Generic create, update, archive, delete and arbitrary-action tools are absent.
Odoo independently enforces the same boundary, so a stale client catalogue or
crafted JSON-2 request cannot restore write access. Secrets are filtered from
explicit fields, default reads, exports, settings helpers and nested results;
safe configuration status and health metadata remain available.

## Readiness and acceptance

The container is ready only when `/readyz` reports `status=ready`, the default
surface remains within its tool/schema budget, and the OAuth vault is ready.
An unauthenticated `/mcp` request must return 401.

```bash
scripts/odoo-mcp smoke --origin http://localhost:<port>
```

Release acceptance additionally requires the MCP repository's complete check
and evaluation suite, a live authenticated initialization/tool-list exchange,
and bounded reads through both the generic and advanced profiles. Run live
integration tests only against a disposable Odoo database or with a temporary
key that is revoked immediately afterward.

The production environment must use an immutable registry digest, retain the
same source and revision OCI labels, mount independent mode-0600 Better Auth
and credential-encryption secrets, and declare a dedicated OAuth volume. The
cutover gate checks readiness, the unauthenticated 401 boundary and the
`usl.agent.current_identity` compatibility contract. Its reviewed journey
evidence separately proves a complete OAuth connection and confirms that
`odoo_describe_environment` identifies the Agent, owner, credential expiry and
effective companies.

## Backup, transfer, and rollback

Compose deploys Odoo and MCP as one release cohort. Local production
checkpoints archive the MCP OAuth volume with every other owned volume. The
sanitized production-transfer cohort keeps the pinned MCP commit, image
digest, and compatibility digest, but does not carry local OAuth grants or
keys: production receives new MCP secrets and users reconnect once. Odoo
business records remain in the Odoo database and are not duplicated in MCP
state.

Runtime rollback is an image change. Keep the preceding immutable image and,
when an OAuth schema changed, its coordinated SQLite backup and matching
secrets. Do not retry a mutation after an ambiguous connection failure; use
the correlation ID to inspect Odoo before deciding whether a new attempt is
safe.
