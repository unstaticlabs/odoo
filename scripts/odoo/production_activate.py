"""Activate a fully gated production database without restoring migrated secrets."""

# Odoo shell script; ``env`` is supplied by Odoo.
# ruff: noqa: EM101, F821, T201

import json
import os
import re


fingerprint = os.environ.get("USL_PRODUCTION_ACTIVATION_CONFIRM", "")
if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
    raise RuntimeError("Production activation requires the exact candidate fingerprint.")
if os.environ.get("USL_EINVOICE_LIVE_ENABLED") != "0" or os.environ.get(
    "USL_EREPORTING_LIVE_ENABLED",
) != "0":
    raise RuntimeError("Regulatory live flags must remain disabled at initial activation.")

params = env["ir.config_parameter"].sudo()  # noqa: F821
admitted_fingerprint = params.get_str("usl.production.admitted_candidate_fingerprint")
activation_fingerprint = params.get_str("usl.production.activation_candidate_fingerprint")
if admitted_fingerprint and admitted_fingerprint != fingerprint:
    raise RuntimeError("The database already has a production admission fingerprint.")
already_activated = not params.get_bool("database.is_neutralized")
if already_activated and activation_fingerprint != fingerprint:
    raise RuntimeError("The database is active under another or missing activation identity.")

pending_mail = env["mail.mail"].sudo().search_count([  # noqa: F821
    ("state", "in", ["outgoing", "exception"]),
])
if pending_mail:
    raise RuntimeError(f"Pre-cutoff Odoo mail remains pending: {pending_mail}")
mail_servers = env["ir.mail_server"].sudo().search([])  # noqa: F821
if mail_servers:
    raise RuntimeError(f"Database mail servers remain before activation: {mail_servers.ids}")
if "fetchmail.server" in env.registry:  # noqa: F821
    active_fetchmail = env["fetchmail.server"].sudo().search([("active", "=", True)])  # noqa: F821
    if active_fetchmail:
        raise RuntimeError(f"Incoming mail servers remain active: {active_fetchmail.ids}")

# Neutralization intentionally removed or disabled external credentials. Those
# values stay removed. Production uses governed environment configuration and
# explicit provider gates. Only the runtime marker and visible test ribbons are
# reversed after all admission controls pass.
neutralize_views = env["ir.ui.view"].sudo().search([  # noqa: F821
    ("key", "in", ["web.neutralize_banner", "website.neutralize_ribbon"]),
    ("active", "=", True),
])
if not already_activated:
    params.set_bool("database.is_neutralized", False)
    params.set_str("usl.production.activation_candidate_fingerprint", fingerprint)
    if neutralize_views:
        neutralize_views.write({"active": False})
env.registry.clear_cache("stable")  # noqa: F821
if params.get_bool("database.is_neutralized"):
    raise RuntimeError("The production neutralization marker did not clear.")

env.cr.commit()  # noqa: F821
print("USL_PRODUCTION_ACTIVATION=" + json.dumps({
    "candidate_fingerprint": fingerprint,
    "idempotent_reentry": already_activated,
    "database_neutralized": False,
    "database_mail_server_count": 0,
    "incoming_mail_server_count": 0,
    "neutralization_ribbons_disabled": len(neutralize_views),
    "status": "passed",
}, sort_keys=True))
