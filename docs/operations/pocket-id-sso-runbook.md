# Pocket ID SSO runbook

This runbook configures Pocket ID passkey login without making Pocket ID an
authorization source. Local integration QA uses an automatically managed,
disposable clone of canonical `odoo_dev`. The helper never configures
`odoo_dev` itself and never touches the read-only source database.

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
- generate one break-glass password of at least 20 characters and store it in
  the approved password manager.

Never paste tokens, client secrets, passkeys or raw Pocket ID subjects into a
commit, ticket, screenshot or validation artifact.

## 2. Isolated local preproduction

The repository provides a pinned Pocket ID v2.12.0 Compose overlay and an
idempotent helper. It:

- binds Pocket ID to `127.0.0.1:1411`;
- serves Odoo on the normal local port at `http://odoo.localhost:8069`;
- accepts local HTTP only for RFC-reserved `.localhost` names;
- targets only a disposable `odoo_dev_*_qa` clone;
- refuses either enabled regulatory live guard;
- writes generated secrets and immutable test subjects only to the ignored
  `.pocket-id.env` with mode 0600;
- clones canonical `odoo_dev` and its filestore on demand;
- reuses that clone during one QA session, then removes it explicitly.

Create and configure the tenant:

```bash
scripts/pocket-id-dev bootstrap
scripts/pocket-id-dev configure-odoo
scripts/pocket-id-dev status
```

Generate a one-hour, single-user test login link without exposing a password:

```bash
scripts/pocket-id-dev one-time-link valentin
scripts/pocket-id-dev one-time-link roger
scripts/pocket-id-dev one-time-link prosper
```

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

`reset-idp --confirm` deletes only the disposable Pocket ID container and
volume. To finish QA, remove the disposable database/filestore clone and
restore the canonical Odoo service:

```bash
scripts/pocket-id-dev reset-idp --confirm
scripts/pocket-id-dev cleanup-qa-clone --confirm
```

`cleanup-qa-clone` is the normal final command. It also removes Pocket ID
service data, so running `reset-idp` first is optional.

The local overlay deliberately enables insecure callback URLs only inside this
loopback preproduction topology. Staging and production require HTTPS and must
not copy that setting. Pocket ID uses `prosper@preproduction.invalid` only as a
clearly synthetic provider-side placeholder. It is not written to Prosper's
existing Odoo user, whose canonical email is currently blank. Replace the
placeholder with an owner-confirmed address before any non-local activation.

## 3. Configure an external Pocket ID

Create a confidential OIDC client in Pocket ID:

- flow: authorization code;
- redirect URI:
  `https://<public-odoo-host>/auth_oauth/signin`;
- scopes: `openid profile email groups`;
- signing algorithm: RS256;
- client authentication: `client_secret_basic` when advertised, otherwise
  `client_secret_post`;
- allowed user group: the dedicated preproduction or production Odoo group.

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
```

Issuer and public base URLs must not have a trailing application path. The
configured public base URL, proxy host/scheme and Pocket ID redirect must be
identical. Leave `USL_POCKET_ID_TOKEN_AUTH_METHOD` empty to select the
advertised safe default.

## 4. Prepare named-user policy

`USL_POCKET_ID_USERS_JSON` must contain every non-framework Odoo user. The
framework OdooBot, Public user and Portal User Template are protected
automatically. No wildcard users are accepted.

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
    "login": "prosper",
    "name": "Prosper",
    "email": "<owner-confirmed-prosper-email>",
    "profile": "accountant_reviewer",
    "companies": ["Unstatic Labs"],
    "subject": "<prosper-pocket-id-sub>",
    "create_if_missing": true
  }
]
```

If a target contains the inactive source-style `roger@xaic.cat` user, add:

```json
{
  "login": "roger@xaic.cat",
  "email": "roger@xaic.cat",
  "profile": "historical"
}
```

Do not create a historical identity only to make the list resemble the source.
Local QA starts from the current canonical reconstruction, including its
existing users and imported contacts. Controlled creation therefore reuses an
exact existing login or email/partner instead of duplicating it; missing users
still require complete owner-approved identity details. The configuration
refuses ambiguity and never silently merges users.

Verified-email first link is an exception for a known existing user. Replace
`subject` with `"email_link": true`, set both provider and per-user approval,
and disable both approvals after the first successful link. Do not use this
for an ambiguous email or as an account-discovery mechanism.

## 5. Install and dry-run

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

For local QA, do not run these commands manually. `scripts/pocket-id-dev
configure-odoo` creates the disposable clone, updates the complete product
dependency graph, performs the same dry-run/apply sequence and prints the
isolated Odoo and Pocket ID URLs.

Review the dry-run result with the identity owner and accounting owner.
Resolve every missing user, duplicate email, unclassified login, company or
subject conflict. Never work around a refusal by deleting a user or identity.

## 6. Apply

After the dry run is accepted:

```bash
export USL_POCKET_ID_APPLY=1
scripts/odoo-dev configure-pocket-id
unset USL_POCKET_ID_USERS_JSON
unset USL_POCKET_ID_BREAK_GLASS_PASSWORD
unset USL_POCKET_ID_APPLY
```

Keep the provider secret in the runtime secret store. Remove the one-shot user
JSON and break-glass plaintext from the shell and deployment environment after
application.

In Odoo, verify under **Settings → Users & Companies**:

- Pocket ID provider is enabled and has no database-stored client secret;
- there is exactly one active local break-glass user;
- each active SSO user has the expected profile, company list and exact groups;
- every explicit link has the expected subject fingerprint;
- Pocket ID audit events exist for links and policy application.

## 7. Required acceptance journeys

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
3. Prosper authenticates the same way, sees only Unstatic Labs and the
   existing USL accountant-review screens, reports and exports. Attempted
   accounting create, edit, post, reconcile, configuration or approval actions
   are denied.
4. A Pocket ID user with the required group but no identity link is denied;
   the Odoo user count remains unchanged.
5. Confirm the default Odoo.com login button is absent after activation. The
   native Odoo passkey option remains available to eligible non-SSO identities,
   but a Pocket ID-managed user cannot use or register an Odoo-local passkey.
6. An incorrect issuer, audience, nonce, expired token, unsigned/wrong
   algorithm token, missing group and replayed state are denied and audited.
7. Archive a disposable SSO user or disable its identity. Its active session
   stops working, a new SSO login is denied, and its historical records remain.
8. Remove a disposable user from the Pocket ID allowed group. New login is
   denied. Disable the Odoo identity immediately and confirm the existing
   session is invalidated.
9. Stop or firewall the Pocket ID preproduction service. New SSO login shows
   the safe provider-unavailable error. The break-glass user can still sign in
   locally and inspect the configuration. Restore Pocket ID and confirm SSO
   recovery.
10. Change a disposable Pocket ID email/display name. The same issuer/subject
   returns to the same Odoo user without changing Odoo authorization or profile
   fields.

The first three named-user Odoo journeys remain browser acceptance gates.
One-time links are the approved local mechanism because the passkey ceremony
itself is outside the Odoo integration test scope. Automated signed-token
tests do not replace the real Pocket ID/Odoo redirect and callback.

## 8. Validated local acceptance

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
- with Pocket ID stopped, new SSO failed closed while the sole local
  break-glass administrator entered Odoo and reached Settings; after restart,
  Valentin SSO recovered.

Protocol error, replay, conflict, archive, authorization and read-only
accountant boundaries are covered by the module's focused automated tests.
Acceptance artifacts record only safe fingerprints and outcomes, never raw
subjects, tokens or generated credentials.

## 9. Offboarding and conflicts

For a planned departure:

1. remove the user from the Pocket ID allowed group;
2. in Odoo, disable the identity and Pocket ID access or apply `historical`;
3. archive the Odoo user when access must end;
4. verify the old browser session is invalid and new SSO is denied;
5. retain the user, partner, identity link, audit events and business records.

For an incorrect link, do not delete either user. Disable the identity, verify
the two exact users and immutable subjects with the identity owner, then
perform an explicit administrator relink. The relink is audited.

## 10. Outage, rollback and recovery

During a Pocket ID outage, do not enable local passwords for SSO-managed
users. Use only the tested break-glass account for necessary administration.

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
