# USL Pocket ID SSO

`usl_pocketid` provides the Pocket ID authentication boundary for the USL
Odoo fork. It extends pinned OCA `auth_oidc`; Odoo remains authoritative for
companies, groups, Accounting permissions and record rules.

The module owns:

- authorization-code OIDC with per-request PKCE, state and nonce;
- RS256/JWKS, exact issuer and audience validation;
- immutable `(issuer, subject)` identity links;
- environment-managed provider configuration and audit events;
- SSO-only login for internal and portal users, including sensitive-action
  reauthentication;
- one sealed, time-limited emergency administrator and API-key-safe RPC.

It does not hard-reference roles owned by downstream modules. Product modules
extend `res.users._usl_pocketid_profile_definitions()` for those roles. The
transitional Accounting compatibility module currently registers the scoped
accountant-reviewer profile because it still owns that stable group XML ID.

Pocket ID is inert unless `USL_POCKET_ID_ENABLED=1` and the complete provider
environment is deliberately applied. `USL_POCKET_ID_LOGIN_POLICY=sso_only`
removes every normal local credential path only after provider, user and
identity validation succeeds. Client secrets never enter the database.

Local validation uses canonical `odoo_dev`, the disposable production-shaped
target:

```bash
scripts/pocket-id-dev bootstrap
scripts/pocket-id-dev configure-odoo
scripts/pocket-id-dev one-time-link valentin
```

Normal `make dev`, `make deploy` and `make rebuild` preserve this target
configuration. `make target-reconstruct` rebuilds source business data first,
finalizes migration infrastructure out of the database, and applies Pocket ID
last.

Run clean module tests with:

```bash
scripts/odoo-dev test usl_pocketid odoo_test_usl_pocketid
```

See [Pocket ID architecture](../../docs/product/pocket-id-sso.md) and the
[operations runbook](../../docs/operations/pocket-id-sso-runbook.md).
