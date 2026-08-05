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
scripts/preprod-release recovery-rehearsal /absolute/path/to/usl-online-dump
scripts/preprod-release gate /absolute/path/to/usl-online-dump
```

`clean-install` installs every delivered product module into a disposable empty
database using the immutable image, applies and verifies the release identity,
runs the full database product/migration boundary, and then removes both the
database and its filestore. This qualification is deliberately separate from
the migrated `odoo_dev` reconstruction.

Do not set `USL_RELEASE_ALLOW_DIRTY=1` for qualification. That switch exists
only to test release tooling while it is being developed.

`recovery-rehearsal` briefly stops the isolated candidate while it captures
Odoo PostgreSQL/filestore and Paperless PostgreSQL/media/data as one recovery
point. It restores them under a timestamped Compose project, verifies the
services independently, checks document checksums and links, and compares
company, active-user, journal-entry, move-line and posted debit/credit controls.
The restored project, volumes and sensitive temporary backup are removed after
success. This explicit path is the only recovery helper allowed to use the
local `odoo_dev` source database, and only when its Compose project has the
isolated `usl-odoo-preprod-*` identity.

The rehearsal requires the restored permission-failure ID set to match the
source recovery point exactly. A qualified candidate has no permission
failures because target finalization has already reconciled all governed
Paperless identities and synchronized their object grants.

### Complete individual Paperless acceptance

Target finalization provisions each governed Paperless account through the
supported application models, attaches the immutable Pocket subject, verifies
the Odoo mapping, and synchronizes the exact document-object grants. It does
not import source credentials, passwords or sessions and does not rely on a
person's first Paperless login. **Documents > Configuration > User access** is
the inspection surface; `make paperless-users` safely reapplies reconciliation
after a governed identity, Documents role or company assignment changes.

The local QA deployment is HTTP, so WebAuthn passkeys are unavailable. Exercise
the real Pocket ID authorization-code flow with the one-hour, single-user links
created by the existing Make target. Select the release environment explicitly
from this linked worktree:

```bash
POCKET_ID_ENV_FILE="$PWD/.pocket-id-preprod.env" make login-link USER=valentin
POCKET_ID_ENV_FILE="$PWD/.pocket-id-preprod.env" make login-link USER=roger
POCKET_ID_ENV_FILE="$PWD/.pocket-id-preprod.env" make login-link USER=prosper
```

Open each link only in the intended person's isolated browser session, complete
both Odoo and Paperless SSO journeys, and discard the link after use. Never put
the URL in a commit, ticket, screenshot or evidence artifact. The release gate
writes `artifacts/release/documents-identity-boundary.json` and fails with the
exact incomplete or unsynchronized identities; the separate browser acceptance
record proves that each person can actually authenticate and reaches only their
authorized surfaces.

Do not create Paperless users in SQL, reuse a shared administrator, or mark
mappings verified manually. Passkeys become the required human authentication
mechanism after deployment behind HTTPS; QA one-time links must not be carried
into that deployment.

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
5. Reapply the governed Paperless identity plan, then repeat the database
   boundary, release-identity check, direct-access boundary and critical browser
   journeys before admitting users. Require each person to use their Pocket ID
   passkey on the HTTPS deployment; the local QA one-time-link exception ends at
   deployment.

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
