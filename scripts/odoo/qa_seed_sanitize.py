"""Remove environment credentials before exporting reusable Odoo data."""

# Odoo shell script; ``env`` is supplied by Odoo.
# ruff: noqa: F821, S608, T201

import json
import os
import re


IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
removed = {}

if os.environ.get("USL_EINVOICE_LIVE_ENABLED", "") != "0" or os.environ.get(
    "USL_EREPORTING_LIVE_ENABLED",
    "",
) != "0":
    raise ValueError("Regulatory live flags must remain disabled during sanitation.")


def table_exists(name):
    env.cr.execute("SELECT to_regclass(%s)", (f"public.{name}",))  # noqa: F821
    return bool(env.cr.fetchone()[0])  # noqa: F821


def column_exists(table, column):
    env.cr.execute(  # noqa: F821
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s AND column_name=%s",
        (table, column),
    )
    return bool(env.cr.fetchone())  # noqa: F821


def execute_count(label, statement, parameters=()):
    if parameters:
        env.cr.execute(statement, parameters)  # noqa: F821
    else:
        # Passing an empty tuple makes psycopg2 interpret literal SQL ``%``
        # characters (for example ILIKE patterns) as placeholders.
        env.cr.execute(statement)  # noqa: F821
    removed[label] = removed.get(label, 0) + max(env.cr.rowcount, 0)  # noqa: F821


def delete_table(table):
    if table_exists(table):
        execute_count(table, f'DELETE FROM "{table}"')


def null_column(table, column, *, where=None):
    if not IDENTIFIER.fullmatch(table) or not IDENTIFIER.fullmatch(column):
        raise ValueError(f"Unsafe sanitation identifier: {table}.{column}")
    if table_exists(table) and column_exists(table, column):
        condition = where or f'"{column}" IS NOT NULL'
        execute_count(
            f"{table}.{column}",
            f'UPDATE "{table}" SET "{column}" = NULL WHERE {condition}',
        )


def set_column(table, column, value, *, where=None):
    if not IDENTIFIER.fullmatch(table) or not IDENTIFIER.fullmatch(column):
        raise ValueError(f"Unsafe sanitation identifier: {table}.{column}")
    if table_exists(table) and column_exists(table, column):
        condition = where or f'"{column}" IS DISTINCT FROM %s'
        parameters = (value,) if where else (value, value)
        execute_count(
            f"{table}.{column}",
            f'UPDATE "{table}" SET "{column}" = %s WHERE {condition}',
            parameters,
        )


# Never hide an incomplete finalization by stripping migration columns from a
# portable database. Candidate/seed capture must occur after the boundary gate.
env.cr.execute(  # noqa: F821
    """
    SELECT count(*)
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND column_name LIKE 'rebuild_source_%'
    """,
)
if env.cr.fetchone()[0]:  # noqa: F821
    raise ValueError("Migration source-binding columns remain in the export database.")

# Paperless mappings reference immutable OIDC identities with RESTRICT. They
# are environment identity state and are rebuilt from the external mode-0600
# identity policy during configuration.
delete_table("usl_paperless_user_mapping")

# Mail-thread records for identities can themselves retain an issuer/subject.
for table in ("mail_followers", "mail_activity", "mail_message"):
    if table_exists(table) and column_exists(table, "res_model"):
        execute_count(
            f"{table}.pocket_identity",
            f'DELETE FROM "{table}" WHERE res_model = %s',
            ("usl.oidc.identity",),
        )
    elif table_exists(table) and column_exists(table, "model"):
        execute_count(
            f"{table}.pocket_identity",
            f'DELETE FROM "{table}" WHERE model = %s',
            ("usl.oidc.identity",),
        )

# Identity subjects and local security audit events are not portable business
# history. No Pocket ID mutation API is called by sanitation or import.
delete_table("usl_oidc_audit_event")
delete_table("usl_oidc_identity")

# Passwords, API keys, MFA/passkey material and active sessions must never be
# carried between hosts. Filesystem sessions are outside the filestore archive.
null_column("res_users", "password")
null_column("res_users", "totp_secret")
for table in (
    "auth_api_key",
    "auth_totp_device",
    "auth_passkey_key",
    "auth_totp_rate_limit_log",
    "auth_totp_wizard",
    "change_password_own",
    "change_password_user",
    "res_device_log",
    "ir_logging",
    "ir_profile",
    "bus_bus",
    "bus_presence",
):
    delete_table(table)
for column in ("oauth_access_token", "oauth_uid", "oauth_provider_id"):
    null_column("res_users", column)

# Provider registrations and cryptographic key material are environment
# credentials, not accounting or document history.  Delete the provider rows
# before their referenced keys, and remove attachment-backed key payloads so
# no private key can enter the portable filestore.
if table_exists("ir_attachment"):
    execute_count(
        "ir_attachment.environment_keys",
        "DELETE FROM ir_attachment "
        "WHERE res_model IN ('certificate.key', 'certificate.certificate')",
    )
for table in (
    "account_edi_proxy_client_user",
    "certificate_certificate",
    "certificate_key",
):
    delete_table(table)

# Sanitize credentials exposed by installed modules without assuming a fixed
# Enterprise/Community source module set.  Commercial references such as
# Stripe/Etsy order and payment IDs are deliberately not selected.  Retain the
# full column inventory so the final assertion also rejects a non-nullable
# credential field instead of silently skipping it.
env.cr.execute(  # noqa: F821
    """
    SELECT table_name, column_name, is_nullable
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND data_type IN ('character varying', 'text')
       AND (
            column_name IN ('password', 'passkey', 'api_key', 'access_token',
                            'refresh_token', 'client_secret', 'private_key_password')
         OR column_name LIKE '%\\_password' ESCAPE '\\'
         OR column_name LIKE '%\\_api_key' ESCAPE '\\'
         OR column_name LIKE '%\\_access_token' ESCAPE '\\'
         OR column_name LIKE '%\\_refresh_token' ESCAPE '\\'
         OR column_name LIKE '%\\_client_secret' ESCAPE '\\'
         OR column_name LIKE '%\\_webhook_secret' ESCAPE '\\'
       )
     ORDER BY table_name, column_name
    """,
)
credential_columns = list(env.cr.fetchall())  # noqa: F821
for table, column, nullable in credential_columns:
    if nullable == "YES":
        null_column(table, column)

# Pocket ID is external and environment-managed. Keep the governed provider
# row/XML identity but disable it and clear all host/client endpoint values.
if table_exists("auth_oauth_provider"):
    pocket_condition = (
        "usl_pocketid IS TRUE"
        if column_exists("auth_oauth_provider", "usl_pocketid")
        else None
    )
    for column in (
        "client_id",
        "client_secret",
        "data_endpoint",
        "jwks_uri",
        "token_map",
        "token_endpoint",
        "usl_token_auth_method",
    ):
        null_column("auth_oauth_provider", column)
    for column in (
        "usl_end_session_endpoint",
        "usl_oidc_issuer",
        "usl_public_base_url",
        "usl_required_group",
    ):
        null_column(
            "auth_oauth_provider",
            column,
            where=pocket_condition,
        )
    for column in ("auth_endpoint", "validation_endpoint"):
        set_column(
            "auth_oauth_provider",
            column,
            "https://candidate.invalid/disabled",
        )
    if column_exists("auth_oauth_provider", "enabled"):
        execute_count(
            "auth_oauth_provider.enabled",
            "UPDATE auth_oauth_provider SET enabled = FALSE WHERE enabled IS TRUE",
        )

# Standard neutralization disables live payment providers but deliberately
# leaves test providers in test mode. A portable production candidate starts
# with every payment integration disabled; activation belongs to a separate
# reviewed production runbook.
if table_exists("payment_provider") and column_exists("payment_provider", "state"):
    set_column(
        "payment_provider",
        "state",
        "disabled",
        where='"state" IS DISTINCT FROM \'disabled\'',
    )

# Standard neutralization disables mail/webhooks but intentionally retains the
# old credentials on inactive rows.  Portable assets remove those secrets and
# host endpoints as well; production configuration comes from guarded files.
for column in (
    "smtp_user",
    "smtp_pass",
    "smtp_ssl_certificate",
    "smtp_ssl_private_key",
):
    null_column("ir_mail_server", column)
set_column("ir_mail_server", "smtp_host", "invalid")
for column in ("user", "password"):
    null_column("fetchmail_server", column)
set_column("fetchmail_server", "server", "invalid")
if table_exists("fetchmail_server") and column_exists("fetchmail_server", "active"):
    execute_count(
        "fetchmail_server.active",
        "UPDATE fetchmail_server SET active = FALSE WHERE active IS TRUE",
    )
null_column("iap_account", "account_token")
delete_table("mail_ice_server")

# Generic configuration values need key-aware sanitation because every value
# shares one column. Preserve business settings while removing host URLs and
# known credentials supplied by guarded production environment files.
if table_exists("ir_config_parameter"):
    execute_count(
        "ir_config_parameter.environment",
        """
        DELETE FROM ir_config_parameter
         WHERE key IN (
            'database.secret',
            'report.url',
            'web.base.url',
            'web.base.url.freeze',
            'usl_documents.paperless_public_url',
            'usl_documents.paperless_service_user_id',
            'usl_documents.paperless_token',
            'usl_documents.paperless_url'
         )
            OR key ILIKE '%client_secret%'
            OR key ILIKE '%api_key%'
            OR key ILIKE '%access_token%'
            OR key ILIKE '%refresh_token%'
        """,
    )

# Prove no selected credential column retained a value. Non-nullable cases are
# surfaced rather than silently weakening the sanitation policy.
remaining = []
for table, column, nullable in credential_columns:
    env.cr.execute(  # noqa: F821
        f'SELECT count(*) FROM "{table}" WHERE "{column}" IS NOT NULL',
    )
    count = env.cr.fetchone()[0]  # noqa: F821
    if count:
        remaining.append({
            "column": f"{table}.{column}",
            "count": count,
            "nullable": nullable,
        })
if remaining:
    raise ValueError(
        "Credential-like values remain after sanitation: "
        + json.dumps(remaining, sort_keys=True),
    )

# A portable production candidate first runs Odoo's maintained neutralization
# SQL for every installed module. This custom pass then removes identity state
# that standard neutralization intentionally does not know about. QA capture
# predates that extra command, so it remains compatible while candidate builds
# prove the stronger standard-neutralization marker.
standard_neutralized = False
if table_exists("ir_config_parameter"):
    env.cr.execute(  # noqa: F821
        "SELECT value FROM ir_config_parameter "
        "WHERE key = 'database.is_neutralized'",
    )
    row = env.cr.fetchone()  # noqa: F821
    standard_neutralized = bool(row and str(row[0]).lower() in {"1", "true"})

env.cr.commit()  # noqa: F821
print(json.dumps({
    "removed": dict(sorted(removed.items())),
    "standard_neutralized": standard_neutralized,
    "status": "passed",
}, sort_keys=True))
print("QA/candidate Odoo credential sanitation: PASS")
