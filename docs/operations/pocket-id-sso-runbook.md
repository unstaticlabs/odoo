# Pocket ID SSO runbook

## Existing production Pocket ID

Final migration uses `compose.external-pocket-id.yaml`, which contains no Pocket
service, volume, provisioning or restore action. The mode-`0600` external
identity policy is applied only to Odoo and Paperless. Follow
[Portable production migration candidate](portable-production-migration.md)
and require matching read-only Pocket state hashes around rehearsal journeys.

This runbook configures Pocket ID as the sole human login without making it an
authorization source. Local integration QA uses canonical `odoo_dev`, the
disposable production-shaped target reconstructed from the Online dump. The
helper never touches the read-only source database.

For Odoo integration acceptance in the isolated local tenant, administrator
one-time Pocket ID login links stand in for the passkey ceremony. They exercise
the real Pocket ID session, OIDC authorization-code redirect, PKCE, token
exchange, claims and Odoo callback. Validating WebAuthn/passkey enrollment and
verification is Pocket ID's responsibility and is not an Odoo acceptance gate.

## 1. Safety and prerequisites

Before changing a database:

- take and verify a database and filestore backup;
- confirm `USL_EINVOICE_LIVE_ENABLED=0` and
  `USL_EREPORTING_LIVE_ENABLED=0`;
- inspect the selected candidate's current users and reusable partner records;
- obtain the exact Pocket ID issuer, client ID, client secret, required group,
  immutable subjects and Prosper identity details from the identity owner;
- choose one maintenance window because the helper stops and recreates the
  selected Odoo service;
- generate one emergency password of at least 20 characters, store it in the
  approved secret manager, and leave emergency access sealed.

Never paste tokens, client secrets, passkeys or raw Pocket ID subjects into a
commit, ticket, screenshot or validation artifact.

## 2. Local target and parallel worktrees

The repository provides a pinned Pocket ID v2.14.0 Compose overlay and an
idempotent helper. It:

- binds Pocket ID to `127.0.0.1:1411`;
- serves Odoo on the normal local port at `http://odoo.localhost:8069`;
- accepts local HTTP only for RFC-reserved `.localhost` names;
- targets only the selected project's `odoo_dev`;
- refuses either enabled regulatory live guard;
- writes generated secrets and immutable test subjects only to the ignored
  `.pocket-id.env` with mode 0600;
- provisions stable local subjects idempotently;
- applies target-only identity policy after source-data reconstruction.

Create and configure the tenant:

```bash
scripts/pocket-id-dev bootstrap
scripts/pocket-id-dev configure-odoo
scripts/pocket-id-dev status
```

The main checkout may use the default project and ports. Every linked worktree
must use its own Compose project and four non-conflicting ports. Keep those
values together for reconstruction, deploy, diagnostics and login:

```bash
COMPOSE_PROJECT=usl-odoo-my-feature-a1b2 \
ODOO_HTTP_PORT=18669 ODOO_GEVENT_PORT=18672 \
POCKET_ID_HTTP_PORT=11411 PAPERLESS_HTTP_PORT=18010 \
USL_ONLINE_DUMP_DIR=/absolute/path/to/usl-online-dump \
make target-reconstruct

make COMPOSE_PROJECT=usl-odoo-my-feature-a1b2 doctor
make COMPOSE_PROJECT=usl-odoo-my-feature-a1b2 deploy
make COMPOSE_PROJECT=usl-odoo-my-feature-a1b2 login-link USER=valentin
```

The first command creates an ignored `.pocket-id.env` bound to that project.
Do not copy it to another worktree and do not switch project names afterward.
Tests and QA bootstraps restore Odoo with this same overlay automatically.

`make doctor` reports `Pocket ID: ready` only when the running Odoo process has
all environment-only credentials and the database provider matches its issuer,
client, public URL and required group. If it reports `broken`,
`not-configured`, or `wrong-project`, run the exact repair command it prints.
The short form, when the worktree environment already exists, is:

```bash
make COMPOSE_PROJECT=usl-odoo-my-feature-a1b2 repair-pocket-id
```

`make login-link` performs the same verification before creating a one-time
link. It refuses a link that would redirect to an Odoo runtime with missing or
mismatched Pocket ID settings and prints the repair command instead.

Runtime repair reapplies only the environment-owned provider configuration and
recreates Odoo with the project overlay. It never changes user classification,
groups or permissions. Use `make configure-pocket-id` only for the stricter
full named-user policy operation; that command deliberately refuses
unclassified QA or human users.

Generate a one-hour, single-user test login link without exposing a password.
Pass the exact username of any existing user in the local Pocket ID tenant:

```bash
make login-link USER=valentin
make login-link USER=roger
make login-link USER=prosper
```

The target requires `USER=` explicitly so the host operating-system username
cannot be mistaken for the intended Pocket ID identity. The command resolves
the username exactly and refuses missing or ambiguous matches. It does not
create a user or change that user's Odoo permissions.

Use these lifecycle controls for repeatable acceptance:

```bash
scripts/pocket-id-dev set-disabled roger true
scripts/pocket-id-dev set-disabled roger false
scripts/pocket-id-dev set-group roger absent
scripts/pocket-id-dev set-group roger present
scripts/pocket-id-dev set-profile roger roger+changed@example.invalid "Roger Changed"
scripts/pocket-id-dev set-profile roger roger@unstaticlabs.com Roger
scripts/pocket-id-dev stop-idp
scripts/pocket-id-dev start-idp
```

`reset-idp --confirm` deletes only the local Pocket ID container and volume.
It does not delete `odoo_dev`. Reprovisioning is deterministic because the
ignored environment file retains the immutable local subjects and credentials:

```bash
scripts/pocket-id-dev reset-idp --confirm
scripts/pocket-id-dev configure-odoo
```

Normal development does not require cleanup. `make dev`, `make deploy` and
`make rebuild` keep the selected target and local identity provider running.

The local overlay deliberately enables insecure callback URLs only inside this
loopback target topology. Staging and production require HTTPS, require each
person's Pocket ID passkey, and must not copy the HTTP or one-time-link QA
exception. Pocket ID uses `prosper@preproduction.invalid` only as a
clearly synthetic provider-side placeholder. It is not written to Prosper's
existing Odoo user, whose canonical email is currently blank. Replace the
placeholder with an owner-confirmed address before any non-local activation.

## 3. Reconstruction and target finalization

The Online source has no SSO. This is expected and must not be “fixed” by
writing Pocket metadata into source extraction or parity mappings. The
canonical pipeline keeps two explicit layers:

1. reconstruct and validate native business data;
2. apply the desired target environment and governed identities.

Run the complete lifecycle with:

```bash
make target-reconstruct-product
```

That shorthand owns the default project only in the main checkout. In a linked
worktree, pass the same explicit `COMPOSE_PROJECT` and isolated Odoo, gevent,
Pocket ID and Paperless ports to reconstruction and finalization. The local
`.pocket-id.env` then belongs to that checkout/project pair; helpers reject a
container whose Compose working-directory label points at another checkout.

It executes Accounting reset/import/parity, Project import/parity and
migration finalization before applying Pocket ID. To reapply only target
configuration after a deployment or environment change:

```bash
make target-finalize
```

Both commands retain the migration tools as maintained, versioned repository
deliverables while the final Odoo database remains free of migration modules,
models, menus and provenance fields.

## 4. Configure an external Pocket ID

Create a confidential OIDC client in Pocket ID:

- flow: authorization code;
- redirect URI:
  `https://<public-odoo-host>/auth_oauth/signin`;
- scopes: `openid profile email groups`;
- signing algorithm: RS256;
- client authentication: `client_secret_basic` when advertised, otherwise
  `client_secret_post`;
- allowed user group: the dedicated preproduction or production Odoo group.

For Paperless, create a second confidential client with callback
`https://<paperless-host>/accounts/oidc/pocket-id/login/callback/`. Never reuse
the Odoo client ID or secret. Configure Paperless through its supported
OpenID Connect provider, disable interactive password login in every target,
and keep `PAPERLESS_SOCIAL_ACCOUNT_SYNC_GROUPS=false`; Odoo continues to calculate
the actual document-object permissions. Set
`PAPERLESS_ACCOUNT_DEFAULT_HTTP_PROTOCOL=https` in production-like
environments. Paperless/django-allauth uses this value when it constructs the
OIDC callback, so it must match both `PAPERLESS_PUBLIC_URL` and the redirect
registered in Pocket. Local HTTP QA is the only supported `http` exception.
Set `PAPERLESS_SSO_BASE_GROUP` to the deployment's dedicated, non-business
capability group. The qualified Documents stack creates it idempotently,
assigns it through `PAPERLESS_SOCIAL_ACCOUNT_DEFAULT_GROUPS`, and reconciles
existing Pocket accounts. Do not add document-object grants to this group.

Pocket ID users enroll and use passkeys in Pocket ID. Do not create a second
passkey in Odoo for the SSO journey.

Set the provider variables in the environment secret store:

```dotenv
USL_POCKET_ID_ENABLED=1
USL_POCKET_ID_ISSUER=https://id.example.com
USL_POCKET_ID_CLIENT_ID=<client-id>
USL_POCKET_ID_CLIENT_SECRET=<secret>
USL_POCKET_ID_ODOO_BASE_URL=https://odoo.example.com
USL_POCKET_ID_REQUIRED_GROUP=<exact-pocket-id-group>
USL_POCKET_ID_SCOPES=openid profile email groups
USL_POCKET_ID_TOKEN_AUTH_METHOD=
USL_POCKET_ID_ALLOW_UNIQUE_EMAIL_LINK=0
USL_POCKET_ID_LOGIN_POLICY=sso_only
USL_POCKET_ID_BREAK_GLASS_ENABLED=0
USL_POCKET_ID_BREAK_GLASS_EXPIRES_AT=
ODOO_LIST_DB=False
```

Issuer and public base URLs must not have a trailing application path. The
configured public base URL, proxy host/scheme and Pocket ID redirect must be
identical. Leave `USL_POCKET_ID_TOKEN_AUTH_METHOD` empty to select the
advertised safe default.

## 5. Prepare named-user policy

`USL_POCKET_ID_USERS_JSON` must contain every non-framework Odoo user. The
framework OdooBot, Public user and Portal User Template are protected
automatically. Every other active internal or portal user must be classified;
no wildcard users are accepted.

The expected candidate policy has this shape; replace every angle-bracket
value with owner-confirmed data:

```json
[
  {
    "login": "admin",
    "profile": "break_glass",
    "companies": "all"
  },
  {
    "login": "valentin",
    "name": "Valentin",
    "email": "valentin@unstaticlabs.com",
    "profile": "administrator",
    "companies": "all",
    "subject": "<valentin-pocket-id-sub>",
    "create_if_missing": true
  },
  {
    "login": "roger@unstaticlabs.com",
    "name": "Roger",
    "email": "roger@unstaticlabs.com",
    "profile": "collaborator",
    "companies": ["Unstatic Labs"],
    "subject": "<roger-pocket-id-sub>",
    "create_if_missing": true
  },
  {
    "login": "roger@xaic.cat",
    "profile": "historical"
  },
  {
    "login": "prosper",
    "name": "Prosper",
    "email": "<owner-confirmed-prosper-email>",
    "profile": "accountant_reviewer",
    "companies": ["Unstatic Labs", "USL MEDIA"],
    "subject": "<prosper-pocket-id-sub>",
    "create_if_missing": true
  }
]
```

The canonical policy classifies the inactive source-style `roger@xaic.cat`
identity as historical. It does not create or reactivate that identity. Local
QA starts from the current canonical reconstruction, including its
existing users and imported contacts. Controlled creation therefore reuses an
exact existing login or email/partner instead of duplicating it; missing users
still require complete owner-approved identity details. The configuration
refuses ambiguity and never silently merges users.

Verified-email first link is an exception for a known existing user. Replace
`subject` with `"email_link": true`, set both provider and per-user approval,
and disable both approvals after the first successful link. Do not use this
for an ambiguous email or as an account-discovery mechanism.

## 6. Install and dry-run

For a non-local preproduction database, select the approved target explicitly:

```bash
export ODOO_DEV_DB=<approved-preproduction-database>
export USL_POCKET_ID_USERS_JSON='<complete-json-array>'
export USL_POCKET_ID_BREAK_GLASS_PASSWORD='<password-manager-secret>'
export USL_POCKET_ID_APPLY=0
scripts/odoo-dev deploy rebuild_account_migration
scripts/odoo-dev configure-pocket-id
```

The dry run performs discovery and all user, company, group, subject and
break-glass checks, then rolls the database transaction back. It prints only
logins, profiles and counts; it does not print subjects or secrets.

For the canonical local target, `scripts/pocket-id-dev configure-odoo` updates
the product dependency graph, performs an apply bracketed by two successful
dry runs, and prints the Odoo and Pocket ID URLs.

Review the dry-run result with the identity owner and accounting owner.
Resolve every missing user, duplicate email, unclassified login, company or
subject conflict. Never work around a refusal by deleting a user or identity.

## 7. Apply

After the dry run is accepted:

```bash
export USL_POCKET_ID_APPLY=1
scripts/odoo-dev configure-pocket-id
unset USL_POCKET_ID_USERS_JSON
unset USL_POCKET_ID_BREAK_GLASS_PASSWORD
unset USL_POCKET_ID_APPLY
```

Keep the provider and emergency secrets in the runtime secret store. Remove
the one-shot user JSON and emergency plaintext from the shell after
application. Activation randomizes governed human password hashes once,
preserves API keys, disables signup/reset and closes the database manager.

In Odoo, verify under **Settings → Users & Companies**:

- Pocket ID provider is enabled and has no database-stored client secret;
- there is exactly one sealed emergency user;
- each active SSO user has the expected profile, company list and exact groups;
- every explicit link has the expected subject fingerprint;
- Pocket ID audit events exist for links and policy application.

## 8. Required acceptance journeys

Record date, environment, user, expected result and actual result. Do not
record passkey screens, secrets, tokens or raw subjects.

1. Valentin authenticates to Pocket ID with an approved test one-time link
   (or a passkey outside integration testing), selects **Log in with Pocket
   ID**, reaches the existing Odoo user, opens Settings, both allowed companies,
   Accounting administration, Expenses administration and Project
   administration.
2. Roger authenticates the same way, reaches the existing/migration-created
   collaborator, can use assigned projects, and cannot open Settings,
   Accounting, expense administration, HR private records, sales management,
   Documents administration or Signing administration.
3. Prosper authenticates the same way, can switch between Unstatic Labs and
   USL MEDIA, and reaches the accountant-review screens, reports and exports in
   both company contexts. Protected configuration, lock and permanent-delete
   actions remain denied.
4. A Pocket ID user with the required group but no identity link is denied;
   the Odoo user count remains unchanged.
5. Confirm the login page shows only **Continue with Pocket ID**. Password,
   signup, reset, Odoo-local passkey and alternate OAuth attempts are rejected.
6. An incorrect issuer, audience, nonce, expired token, unsigned/wrong
   algorithm token, missing group and replayed state are denied and audited.
7. Archive a disposable SSO user or disable its identity. Its active session
   stops working, a new SSO login is denied, and its historical records remain.
8. Remove a disposable user from the Pocket ID allowed group. New login is
   denied. Disable the Odoo identity immediately and confirm the existing
   session is invalidated.
9. Stop or firewall the Pocket ID preproduction service. New SSO login shows
   the safe provider-unavailable error. Confirm ordinary local passwords still
   fail. Exercise the sealed emergency procedure only in a dedicated incident
   test, then restore Pocket ID and confirm SSO recovery.
10. Invite a portal user. The message links to Pocket ID, the immutable identity
    resolves the existing portal user, and no reset token or opportunistic Odoo
    user is created.
11. Change a disposable Pocket ID email/display name. The same issuer/subject
   returns to the same Odoo user without changing Odoo authorization or profile
   fields.

The first three named-user Odoo journeys remain browser acceptance gates.
One-time links are the approved local mechanism because the passkey ceremony
itself is outside the Odoo integration test scope. Automated signed-token
tests do not replace the real Pocket ID/Odoo redirect and callback.

## 9. Validated local acceptance

The isolated preproduction candidate was validated in the running application on
2026-07-30:

- Valentin, Roger and Prosper completed real Pocket ID authorization-code
  redirects and entered their explicitly linked Odoo users;
- Valentin saw both intended companies and administrative, Accounting,
  Expenses and Project applications;
- Roger saw Project but neither Settings nor Accounting, and only Unstatic
  Labs;
- Prosper opened the imported accountant-review and accounting-report surface,
  saw only Unstatic Labs, and had no Settings or Project administration;
- a group-authorized but unlinked Pocket ID user was refused without increasing
  Odoo's seven-user count;
- Roger was refused after Pocket ID disablement and separately after removal
  from the allowed group, then recovered after each control was restored;
- changing Roger's Pocket ID email left the immutable subject link, Odoo user,
  Odoo email, authorization and seven-user count unchanged;
- with Pocket ID stopped, new SSO failed closed; the sealed emergency policy
  is covered by focused tests and must be exercised only in an incident window.

Protocol error, replay, conflict, archive, authorization and read-only
accountant boundaries are covered by the module's focused automated tests.
Acceptance artifacts record only safe fingerprints and outcomes, never raw
subjects, tokens or generated credentials.

## 10. Offboarding and conflicts

For a planned departure:

1. remove the user from the Pocket ID allowed group;
2. in Odoo, disable the identity and Pocket ID access or apply `historical`;
3. archive the Odoo user when access must end;
4. verify the old browser session is invalid and new SSO is denied;
5. retain the user, partner, identity link, audit events and business records.

For an incorrect link, do not delete either user. Disable the identity, verify
the two exact users and immutable subjects with the identity owner, then
perform an explicit administrator relink. The relink is audited.

## 11. Outage, rollback and recovery

During a Pocket ID outage, do not enable local passwords for governed users.
Prefer recovery through the identity provider. If emergency Odoo access is
strictly necessary, set both deployment values, restart Odoo, and use only
`/usl/emergency-login`:

```dotenv
USL_POCKET_ID_BREAK_GLASS_ENABLED=1
USL_POCKET_ID_BREAK_GLASS_EXPIRES_AT=2026-08-06T15:30:00Z
```

The expiry must be in the future and no more than one hour from Odoo process
start. The normal login route still refuses the emergency password. Every
attempt is audited. As soon as the task is complete, set the enabled flag to
`0`, restart, verify the route returns 404 and rotate the emergency password
through the configuration helper.

To stop new SSO while preserving evidence:

```dotenv
USL_POCKET_ID_ENABLED=0
```

Update `usl_pocketid` so the environment function disables the Pocket ID
provider and keeps the bundled Odoo.com OAuth provider disabled.
Disabling the provider does not by itself revoke already authenticated Odoo
sessions. Disable the affected identities or archive users to rotate their
session-security tokens.

After recovery, restore the exact issuer/client configuration, dry-run the
complete named-user policy, apply it, and repeat the relevant journeys. Never
replace the preserved identity table or recreate users to recover SSO.
