# Pre-production release

## Outcome

The qualified release is one commit-tagged Docker image containing Odoo core,
the USL product add-ons, the patched pinned OCA bundle and user documentation.
The running Odoo service has no checkout source mounts. Its reconstructed
`odoo_dev` database records the same commit, image ID, OCA digest, installed
module versions and source-dump SHA-256.

## Local deterministic release

Start from a clean dedicated release branch with Docker available and the
latest Online package containing `dump.sql` and `filestore/`. Run:

```bash
scripts/preprod-release all /absolute/path/to/usl-online-dump
```

The command derives a stable worktree-specific Compose project and isolated port
block, creates checkout-local ignored secrets, forces both regulatory live
guards to `0`, synchronizes exact OCA pins, builds the `distribution` image,
recreates `odoo_dev`, finalizes migration modules out of the registry and
schema, records release identity and starts the no-bind-mount runtime. The final
release gate also requires each enabled Documents persona to complete the
individual Paperless identity handshake described below.

Evidence is written below ignored `artifacts/release/`. The command refuses a
dirty Git tree, a foreign Compose working directory, an untagged or `latest`
image, an incomplete source package, an OCA pin mismatch, installed migration
modules, migration models/fields/XML IDs/schema residue, module-version drift,
a runtime source mount, a missing or unverified individual Paperless mapping,
or unsynchronized Paperless object permissions.

The stages can be repeated independently with the same source path:

```bash
scripts/preprod-release build /absolute/path/to/usl-online-dump
scripts/preprod-release clean-install /absolute/path/to/usl-online-dump
scripts/preprod-release reconstruct /absolute/path/to/usl-online-dump
scripts/preprod-release start /absolute/path/to/usl-online-dump
scripts/preprod-release gate /absolute/path/to/usl-online-dump
```

`clean-install` installs every delivered product module into a disposable empty
database using the immutable image, applies and verifies the release identity,
runs the full database product/migration boundary, and then removes both the
database and its filestore. This qualification is deliberately separate from
the migrated `odoo_dev` reconstruction.

Do not set `USL_RELEASE_ALLOW_DIRTY=1` for qualification. That switch exists
only to test release tooling while it is being developed.

### Complete individual Paperless acceptance

Pocket ID proves identity but deliberately does not create Paperless business
authorization. After `start`, each enabled Documents persona must sign in to
Paperless once with Pocket ID. Paperless creates the individual remote account
through its supported OIDC flow. A Documents administrator then opens
**Documents > Configuration > User access**, maps the Odoo user to that numeric
Paperless user, and runs **Verify identity**. Verification checks the same
immutable Pocket link and synchronizes every visible document object grant.

Run `scripts/preprod-release gate ...` after all personas complete this step.
The gate writes `artifacts/release/documents-identity-boundary.json` and fails
with the exact incomplete users. Do not seed Paperless users locally, reuse a
shared administrator, mark mappings verified in SQL, or weaken this gate. A
fresh `all` run may therefore stop at this intentional human identity
checkpoint; complete the handshakes and rerun only `gate` against the unchanged
qualified image and database.

## External pre-production deployment

1. Push the already qualified commit-tagged image to the approved registry;
   do not rebuild on the target host.
2. Copy `deploy/preprod.env.example` outside the checkout and replace
   every placeholder from the secret manager. Use the qualified image
   reference, exact database name/filter, HTTPS Odoo/Pocket ID/Paperless URLs,
   distinct OIDC clients and both live guards set to `0`.
3. Render and run the `paperless-preflight` service with `compose.yaml`,
   `compose.pocket-id.yaml` and `compose.preprod.yaml` before starting the
   stack.
4. Restore the qualified database/filestore/Paperless volumes together or run
   the deterministic reconstruction in an isolated rehearsal environment,
   then start the qualified image with `--pull never`.
5. Complete each direct Documents persona's first Pocket ID login and verified
   Paperless mapping, then repeat the database boundary, release-identity check,
   direct-access boundary and critical browser journeys before admitting users.

Pocket ID issuer URLs, TLS, registry credentials, production-grade secret
storage, provider eligibility and the deliberately separate French
electronic-invoice/e-reporting activation remain environment-owner steps.

## Rollback

Before any non-disposable deployment, back up PostgreSQL, the Odoo filestore,
Paperless PostgreSQL/media/data and the exact environment/secret references as
one recovery point. If validation fails before user writes, stop the candidate
and restore that complete recovery point with the previously qualified image.
Do not run an older image against a database after forward module changes
unless its rollback compatibility was explicitly rehearsed.

For the disposable local `odoo_dev` rehearsal, rollback is reconstruction:
stop only the dedicated release Compose project and rerun the last qualified
commit against the same dump. Never delete or reset the shared canonical
project from a linked worktree.
