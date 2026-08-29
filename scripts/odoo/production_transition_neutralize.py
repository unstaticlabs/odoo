"""Neutralize a quiesced transition database and retain approved Paperless access."""

# Odoo shell script; ``env`` is supplied by Odoo.
# ruff: noqa: EM101, F821, T201

import json
import os

from odoo.modules.neutralize import neutralize_database


if os.environ.get("USL_TRANSITION_WRITERS_QUIESCED") != "1":
    raise RuntimeError("Transition neutralization requires quiesced writers.")
if os.environ.get("USL_EINVOICE_LIVE_ENABLED") != "0" or os.environ.get(
    "USL_EREPORTING_LIVE_ENABLED",
) != "0":
    raise RuntimeError("Regulatory live flags must remain disabled.")

env.cr.execute(
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE datname = current_database() "
    "AND pid <> pg_backend_pid() "
    "AND backend_type = 'client backend'",
)
other_sessions = int(env.cr.fetchone()[0])
if other_sessions:
    raise RuntimeError(
        f"Transition database still has {other_sessions} other client session(s).",
    )

params = env["ir.config_parameter"].sudo()
paperless = {
    "internal_url": params.get_str("usl_documents.paperless_url"),
    "public_url": params.get_str("usl_documents.paperless_public_url"),
    "token": params.get_str("usl_documents.paperless_token"),
    "service_user_id": params.get_int(
        "usl_documents.paperless_service_user_id",
    ),
}
if not all(
    (
        paperless["internal_url"],
        paperless["public_url"],
        paperless["token"],
        paperless["service_user_id"] > 0,
    ),
):
    raise RuntimeError("Approved Paperless runtime configuration is incomplete.")

neutralize_database(env.cr)

# Neutralization intentionally deletes the Paperless parameters with SQL.  The
# typed parameter API caches both values and record IDs, so discard that cache
# before recreating the approved transition-local values.  Otherwise ``set_*``
# can write to IDs that no longer exist and silently leave the database without
# its runtime configuration.
env.registry.clear_cache("stable")

params.set_str("usl_documents.paperless_url", paperless["internal_url"])
params.set_str("usl_documents.paperless_public_url", paperless["public_url"])
params.set_str("usl_documents.paperless_token", paperless["token"])
params.set_int(
    "usl_documents.paperless_service_user_id",
    paperless["service_user_id"],
)
env["usl.document"]._paperless().ensure_fail_closed_ingestion_policy()

# Standard neutralization disables every OAuth provider.  The transition
# runtime admits interactive access only through its environment-owned Pocket
# ID provider, so restore exactly that governed provider from the running
# release configuration and leave every unrelated provider disabled.
pocket_provider_enabled = env[
    "auth.oauth.provider"
]._usl_pocketid_apply_environment()
pocket_provider = env.ref("usl_pocketid.provider_pocketid").sudo()
if not pocket_provider_enabled or not pocket_provider.enabled:
    raise RuntimeError("The governed Pocket ID provider was not restored.")

env.registry.clear_cache("stable")
restored_paperless = {
    "internal_url": params.get_str("usl_documents.paperless_url"),
    "public_url": params.get_str("usl_documents.paperless_public_url"),
    "token": params.get_str("usl_documents.paperless_token"),
    "service_user_id": params.get_int(
        "usl_documents.paperless_service_user_id",
    ),
}
if restored_paperless != paperless:
    raise RuntimeError("Approved Paperless runtime configuration was not restored.")

if not params.get_bool("database.is_neutralized"):
    raise RuntimeError("Odoo standard neutralization marker was not applied.")
active_crons = env["ir.cron"].sudo().search_count([("active", "=", True)])
if active_crons:
    raise RuntimeError(
        f"Neutralization left {active_crons} scheduled job(s) active.",
    )
neutralized_mail_servers = env["ir.mail_server"].sudo().search_count(
    [("active", "=", True), ("smtp_host", "=", "invalid")],
)
if not neutralized_mail_servers:
    raise RuntimeError("Neutralization did not install an invalid SMTP sink.")

env.cr.commit()
print(
    json.dumps(
        {
            "database": env.cr.dbname,
            "neutralized_mail_servers": neutralized_mail_servers,
            "paperless_runtime_restored": True,
            "pocket_id_runtime_restored": True,
            "scheduled_jobs_active": active_crons,
            "standard_neutralized": True,
            "status": "passed",
        },
        indent=2,
        sort_keys=True,
    ),
)
