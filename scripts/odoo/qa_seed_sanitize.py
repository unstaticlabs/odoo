"""Remove environment credentials before sealing a reusable QA seed."""

# Odoo shell script; ``env`` is supplied by Odoo.
# ruff: noqa: F821, T201


def table_exists(name):
    env.cr.execute("SELECT to_regclass(%s)", (f"public.{name}",))  # noqa: F821
    return bool(env.cr.fetchone()[0])  # noqa: F821


env.cr.execute("UPDATE res_users SET password = NULL")  # noqa: F821
if table_exists("auth_oauth_provider"):
    env.cr.execute(  # noqa: F821
        "UPDATE auth_oauth_provider SET client_secret = NULL",
    )
for table in ("auth_api_key", "auth_totp_device", "auth_passkey_key"):
    if table_exists(table):
        env.cr.execute(f'DELETE FROM "{table}"')  # noqa: S608, F821

# Provider configuration is rebuilt from the isolated environment after
# hydration. Paperless mappings reference OIDC identities, so remove the
# environment-specific dependent rows before identity subjects and audit
# events. None of them may enter a seed or release cohort.
for table in (
    "usl_paperless_user_mapping",
    "usl_oidc_audit_event",
    "usl_oidc_identity",
):
    if table_exists(table):
        env.cr.execute(f'DELETE FROM "{table}"')  # noqa: S608, F821

if table_exists("res_users"):
    for column in ("oauth_access_token", "oauth_uid"):
        env.cr.execute(  # noqa: F821
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='res_users' "
            "AND column_name=%s",
            (column,),
        )
        if env.cr.fetchone():  # noqa: F821
            env.cr.execute(f'UPDATE res_users SET "{column}" = NULL')  # noqa: S608, F821

env["ir.config_parameter"].sudo().search(  # noqa: F821
    [
        (
            "key",
            "in",
            (
                "usl_documents.paperless_public_url",
                "usl_documents.paperless_service_user_id",
                "usl_documents.paperless_token",
                "usl_documents.paperless_url",
            ),
        ),
    ],
).unlink()

env.cr.commit()  # noqa: F821
print("QA seed credential sanitation: PASS")
