# Pocket ID SSO runbook

This runbook configures Pocket ID passkey login without making Pocket ID an
authorization source. Use the candidate database first. Do not run the
procedure against `odoo_dev` or the read-only source database.

## 1. Safety and prerequisites

Before changing a database:

- take and verify a database and filestore backup;
- confirm `USL_EINVOICE_LIVE_ENABLED=0` and
  `USL_EREPORTING_LIVE_ENABLED=0`;
- complete the accounting reconstruction step that creates the intended
  Valentin and Prosper users;
- obtain the exact Pocket ID issuer, client ID, client secret, required group,
  immutable subjects and Prosper identity details from the identity owner;
- choose one maintenance window because the helper stops and recreates the
  selected Odoo service;
- generate one break-glass password of at least 20 characters and store it in
  the approved password manager.

Never paste tokens, client secrets, passkeys or raw Pocket ID subjects into a
commit, ticket, screenshot or validation artifact.

## 2. Configure Pocket ID

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

## 3. Prepare named-user policy

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
    "email": "valentin@unstaticlabs.com",
    "profile": "administrator",
    "companies": "all",
    "subject": "<valentin-pocket-id-sub>"
  },
  {
    "login": "roger",
    "name": "Roger",
    "email": "roger@unstaticlabs.com",
    "profile": "collaborator",
    "companies": ["Unstatic Labs"],
    "subject": "<roger-pocket-id-sub>",
    "create_if_missing": true
  },
  {
    "login": "prosper",
    "profile": "accountant_reviewer",
    "companies": ["Unstatic Labs"],
    "subject": "<prosper-pocket-id-sub>"
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
The configuration matches login first and exact email second, refuses
ambiguity, and never silently merges users.

Verified-email first link is an exception for a known existing user. Replace
`subject` with `"email_link": true`, set both provider and per-user approval,
and disable both approvals after the first successful link. Do not use this
for an ambiguous email or as an account-discovery mechanism.

## 4. Install and dry-run

Select the candidate explicitly:

```bash
export ODOO_DEV_DB=odoo_saas_19_2_candidate_01
export USL_POCKET_ID_USERS_JSON='<complete-json-array>'
export USL_POCKET_ID_BREAK_GLASS_PASSWORD='<password-manager-secret>'
export USL_POCKET_ID_APPLY=0
scripts/odoo-dev deploy rebuild_account_migration
scripts/odoo-dev configure-pocket-id
```

The dry run performs discovery and all user, company, group, subject and
break-glass checks, then rolls the database transaction back. It prints only
logins, profiles and counts; it does not print subjects or secrets.

Review the dry-run result with the identity owner and accounting owner.
Resolve every missing user, duplicate email, unclassified login, company or
subject conflict. Never work around a refusal by deleting a user or identity.

## 5. Apply

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

## 6. Required acceptance journeys

Record date, environment, user, expected result and actual result. Do not
record passkey screens, secrets, tokens or raw subjects.

1. Valentin authenticates to Pocket ID with a passkey, selects **Log in with
   Pocket ID**, reaches the existing Odoo user, opens Settings, both allowed
   companies, Accounting administration, Expenses administration and Project
   administration.
2. Roger authenticates with a passkey, reaches the existing/migration-created
   collaborator, can use assigned projects, and cannot open Settings,
   Accounting, expense administration, HR private records, sales management,
   Documents administration or Signing administration.
3. Prosper authenticates with a passkey, sees only Unstatic Labs and the
   existing USL accountant-review screens, reports and exports. Attempted
   accounting create, edit, post, reconcile, configuration or approval actions
   are denied.
4. A Pocket ID user with the required group but no identity link is denied;
   the Odoo user count remains unchanged.
5. An incorrect issuer, audience, nonce, expired token, unsigned/wrong
   algorithm token, missing group and replayed state are denied and audited.
6. Archive a disposable SSO user or disable its identity. Its active session
   stops working, a new SSO login is denied, and its historical records remain.
7. Remove a disposable user from the Pocket ID allowed group. New login is
   denied. Disable the Odoo identity immediately and confirm the existing
   session is invalidated.
8. Stop or firewall the Pocket ID preproduction service. New SSO login shows
   the safe provider-unavailable error. The break-glass user can still sign in
   locally and inspect the configuration. Restore Pocket ID and confirm SSO
   recovery.
9. Change a disposable Pocket ID email/display name. The same issuer/subject
   returns to the same Odoo user without changing Odoo authorization or profile
   fields.

Pocket ID passkey completion and the first three named-user journeys are
external acceptance gates; automated signed-token tests do not replace them.

## 7. Offboarding and conflicts

For a planned departure:

1. remove the user from the Pocket ID allowed group;
2. in Odoo, disable the identity and Pocket ID access or apply `historical`;
3. archive the Odoo user when access must end;
4. verify the old browser session is invalid and new SSO is denied;
5. retain the user, partner, identity link, audit events and business records.

For an incorrect link, do not delete either user. Disable the identity, verify
the two exact users and immutable subjects with the identity owner, then
perform an explicit administrator relink. The relink is audited.

## 8. Outage, rollback and recovery

During a Pocket ID outage, do not enable local passwords for SSO-managed
users. Use only the tested break-glass account for necessary administration.

To stop new SSO while preserving evidence:

```dotenv
USL_POCKET_ID_ENABLED=0
```

Update `usl_pocketid` so the environment function disables the provider.
Disabling the provider does not by itself revoke already authenticated Odoo
sessions. Disable the affected identities or archive users to rotate their
session-security tokens.

After recovery, restore the exact issuer/client configuration, dry-run the
complete named-user policy, apply it, and repeat the relevant journeys. Never
replace the preserved identity table or recreate users to recover SSO.
