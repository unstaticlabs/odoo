"""Quarantine a production candidate before any runtime worker can start."""

# Odoo shell script; ``env`` is supplied by Odoo.
# ruff: noqa: F821, T201

import json
import os
import re


fingerprint = os.environ.get("USL_PRODUCTION_ACTIVATION_CONFIRM", "")
if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
    raise RuntimeError("Production quarantine requires the exact candidate fingerprint.")
if os.environ.get("USL_EINVOICE_LIVE_ENABLED") != "0" or os.environ.get(
    "USL_EREPORTING_LIVE_ENABLED",
) != "0":
    raise RuntimeError("Regulatory access must remain disabled during quarantine.")

params = env["ir.config_parameter"].sudo()  # noqa: F821
existing = params.get_str("usl.production.quarantined_candidate_fingerprint")
if existing and existing != fingerprint and existing != params.get_str(
    "usl.production.activation_candidate_fingerprint",
):
    raise RuntimeError("The database is quarantined for another candidate.")
params.set_bool("database.is_neutralized", True)
params.set_str("usl.production.quarantined_candidate_fingerprint", fingerprint)

Cron = env["ir.cron"].sudo().with_context(active_test=False)  # noqa: F821
active_crons = Cron.search([("active", "=", True)])
if active_crons:
    active_crons.write({"active": False})
if "fetchmail.server" in env.registry:  # noqa: F821
    active_fetchmail = env["fetchmail.server"].sudo().search([("active", "=", True)])  # noqa: F821
    if existing != fingerprint:
        params.set_str(
            "usl.production.quarantined_fetchmail_ids",
            json.dumps(sorted(active_fetchmail.ids)),
        )
    if active_fetchmail:
        active_fetchmail.write({"active": False})
env.cr.commit()  # noqa: F821

print("USL_PRODUCTION_QUARANTINE=" + json.dumps({
    "candidate_fingerprint": fingerprint,
    "cron_count": 0,
    "database_neutralized": True,
    "fetchmail_count": 0,
    "status": "passed",
}, sort_keys=True))
