# Pocket ID SSO operations

Pocket ID is the human authentication authority for Odoo, Paperless, and Sign.
Odoo remains the authorization authority: OIDC claims identify a person but do
not assign companies, roles, or record access.

## Safety rules

- Keep client secrets, encryption keys, API keys, immutable subjects, passkeys,
  and recovery credentials outside Git and screenshots.
- Keep a protected break-glass Odoo administrator, but do not use it for
  ordinary work.
- Apply company and role policy only after the target database passes access,
  company and release validation.
- Never import Online passwords, sessions, or authentication tokens.
- Keep e-invoice reception and e-reporting disabled while configuring identity.
- Back up the database and filestore before changing a persistent environment.

## Ordinary local development

The local helper owns the development tenant and `.pocket-id.env`:

```bash
scripts/pocket-id-dev bootstrap
scripts/pocket-id-dev configure-odoo
scripts/pocket-id-dev status
```

The ignored environment file must remain mode `0600`. Keep its project, ports,
URLs, issuer, and client identity together. Do not copy it between runtimes.

Routine development commands preserve that identity:

```bash
make doctor
make dev
make deploy
make login-link USER=valentin
```

The helper refuses a login link when the running Odoo environment and database
provider disagree. Repair the same runtime with:

```bash
make repair-pocket-id
```

This reapplies environment-owned provider configuration; it does not grant
roles or companies.

## Mobile app access

Odoo's iOS app uses the normal Pocket ID sign-in flow. Do not block a mobile
client merely because its user agent identifies it as an Odoo app.

The Android app currently does not return from Pocket ID to Odoo reliably. Its
login page keeps Pocket ID available in case the app gains support, but shows a
non-blocking warning. Until then, open the public Odoo URL in Chrome, complete
Pocket ID sign-in, then choose **Install app** from Chrome's menu. The installed
PWA supports SSO and push notifications.

## Historical reconstruction runtimes

Migration runtimes store resolved identity separately from secrets. Adopt or
create them only through `migration/manage`; secret files may not contain
project names, ports, URLs, database names, or image identities.

Inspect the runtime and issue a link of at most eight hours:

```bash
migration/manage qa status --runtime <runtime-id>
migration/manage qa login-link \
  --runtime <runtime-id> --user valentin --ttl 8h
```

The link exercises the real Pocket ID session and OIDC code flow. It is
single-use; print it for the intended person and do not open it yourself.

## Named-user policy

Each admitted person requires:

- one immutable Pocket ID subject;
- one existing Odoo user and partner identity;
- explicit Odoo groups, allowed companies, and default company;
- matching governed Paperless and Sign identities where needed;
- a reviewed decision for administrator, accountant, employee, or portal
  capabilities.

Profile changes may refresh display name and email but never replace the
subject binding. A changed subject, ambiguous login, disabled person, missing
required group, or unexpected company access fails closed.

## Production configuration

Production uses an existing external Pocket ID deployment. The production
Compose topology contains no identity-provider volume, provisioning, reset, or
test-link service. Bind the approved HTTPS issuer, redirect URLs, client
identity, immutable subjects, and secrets through the deployment configuration
and secret store.

Before admission, verify:

- exact HTTPS issuer and JWKS retrieval;
- authorization-code flow, PKCE, state, nonce, and redirect validation;
- each named person's passkey journey;
- disabled and removed-group denial;
- Odoo, Paperless, and Sign identity agreement;
- company isolation and break-glass recovery;
- no local HTTP or one-time-link exception in production configuration.

Production identity and secrets are not part of a transferred data cohort.
Configure them after restore, then run the current access, security,
multi-company and application gates before admission.

## Incident response

For a login or access incident:

1. preserve logs and identify the runtime, issuer, subject, Odoo user, and
   active company context;
2. distinguish authentication failure from Odoo authorization denial;
3. verify exact runtime identity without printing secrets;
4. reproduce policy changes on an isolated clone;
5. repair the governing identity or Odoo access rule;
6. confirm denial for unrelated companies and users;
7. record the decision and recovery path.

Do not bypass the problem with `sudo`, a shared administrator account, a new
local password, or a duplicate user.
