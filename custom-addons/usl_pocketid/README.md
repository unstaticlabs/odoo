# USL Pocket ID SSO

`usl_pocketid` provides the Pocket ID authentication boundary for the USL
Odoo fork. It extends pinned OCA `auth_oidc`; Odoo remains authoritative for
companies, groups, Accounting permissions and record rules.

The module owns:

- authorization-code OIDC with per-request PKCE, state and nonce;
- RS256/JWKS, exact issuer and audience validation;
- immutable `(issuer, subject)` identity links;
- environment-managed provider configuration and audit events;
- fail-closed login and one independent local break-glass administrator.

It does not hard-reference roles owned by downstream modules. Product modules
extend `res.users._usl_pocketid_profile_definitions()` for those roles. The
transitional Accounting compatibility module currently registers the scoped
accountant-reviewer profile because it still owns that stable group XML ID.

Pocket ID is inert unless `USL_POCKET_ID_ENABLED=1` and the complete provider
environment is deliberately applied. Client secrets never enter the database.

Local validation uses a disposable clone of canonical `odoo_dev`:

```bash
scripts/pocket-id-dev bootstrap
scripts/pocket-id-dev configure-odoo
scripts/pocket-id-dev one-time-link valentin
scripts/pocket-id-dev cleanup-qa-clone --confirm
```

The cleanup command removes the QA clone and local Pocket ID data, then
restores `http://localhost:8069/web/login?db=odoo_dev`.

Run clean module tests with:

```bash
scripts/odoo-dev test usl_pocketid odoo_test_usl_pocketid
```

See [Pocket ID architecture](../../docs/product/pocket-id-sso.md) and the
[operations runbook](../../docs/operations/pocket-id-sso-runbook.md).
