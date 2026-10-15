# Mail seed identifiers during upgrades

A normal packaged Odoo upgrade repairs two missing Mail external identifiers
before Base or Mail data loads. This lets an evolved database keep its existing
administrator channel memberships instead of attempting duplicate creation.
Existing memberships, roles, read markers, channels and messages stay unchanged.

The native `pre_upgrade_scripts` setting selects `/opt/usl/pre_upgrade.py`.
The distribution image includes that file; its contents participate in the
component build identity. The native hook runs on an upgrade even when no add-on
version changes. A later custom add-on migration would run too late for this
failure.

## Supported states

The repair is limited to these upstream identifiers:

- `mail.channel_member_general_channel_for_admin`
- `mail.channel_member_channel_admin_partner_admin`

It resolves the live native anchors `base.partner_admin`,
`mail.channel_all_employees` and `mail.channel_admin`.

A valid existing binding is a no-op. A missing binding can identify only the
single existing membership for the expected partner and channel. A binding
whose correct-model target no longer exists can be rebound only to that same
unique expected membership. Adopted bindings are `noupdate` so native seed
loading does not replay defaults over current member state.

When neither the binding nor its membership exists, native creation proceeds
normally. Before Mail is installed, the hook leaves installation unchanged.
It does not recreate deleted memberships or infer arbitrary historical data.

Both seed states are checked before the first write. A transaction-scoped lock
serializes cooperating upgrades; a savepoint also rolls back earlier adoption
if a late metadata conflict occurs. The caller owns the upgrade transaction.
The hook never commits independently. Odoo uses Repeatable Read isolation, so a
racing transaction can safely receive a PostgreSQL serialization refusal after
waiting for the lock. Retry the whole upgrade transaction; a statement-only retry
retains the old snapshot. Simultaneous upgrades are not promised to both succeed.

## Refusal and recovery

A wrong-model binding, a binding to another live member, malformed native
anchors, ambiguous memberships or conflicting concurrent metadata causes a
fixed diagnostic beginning `Mail seed metadata repair refused`. Diagnostics
contain no record values.

Retain the failed upgrade receipt and inspect the exact identifier/anchor
metadata privately. Resolve an ambiguous or conflicting state through a reviewed
recovery procedure before retrying. Do not delete memberships, reset roles or
read markers, drop uniqueness constraints, or broadly adopt records to force an
upgrade through. This hook handles the two supported identities only.

Use the ordinary qualified release workflow. The configured script must exist
inside the final image: native Odoo merely warns and skips a missing path.
An image with only a configured filename is not sufficient evidence.

## Qualification

Fast packaging and content-identity contracts run with:

```sh
python3 -m unittest scripts.tests.test_component_build
```

Real PostgreSQL state/concurrency tests use the runtime's existing `psycopg2`
dependency and a disposable database whose name starts with
`odoo_test_mail_seed`. Each test owns a separate temporary schema:

```sh
USL_MAIL_SEED_TEST_DSN='<disposable local test connection>' \
  python3 -m unittest scripts.tests.test_mail_seed_pre_upgrade -v
```

Without the explicit test connection these integration cases report a skip;
that result is not PostgreSQL qualification. Native QA must also prove ordinary
Mail data loading fails with the repair disabled, succeeds through the real
packaged/configured hook, preserves complete existing member rows on repeat,
and fails again when the repair is disabled on an untouched fixture clone.
Use synthetic fixtures for public evidence. Any approved production-derived
restore and its values, counts, filestore and logs remain private, neutralized
and isolated from outbound traffic.
