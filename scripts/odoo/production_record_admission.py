"""Record the independently approved candidate fingerprint in Odoo."""

# Odoo shell script; ``env`` is supplied by Odoo.
# ruff: noqa: EM101, F821, T201

import os
import re

fingerprint = os.environ.get("USL_ADMITTED_CANDIDATE_FINGERPRINT", "")
if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
    raise RuntimeError("The admitted candidate fingerprint is invalid.")
if os.environ.get("USL_EINVOICE_LIVE_ENABLED") != "0" or os.environ.get(
    "USL_EREPORTING_LIVE_ENABLED",
) != "0":
    raise RuntimeError("Regulatory live flags must remain disabled at admission.")
parameters = env["ir.config_parameter"].sudo()  # noqa: F821
if parameters.get_bool("database.is_neutralized"):
    raise RuntimeError("The database remains neutralized at admission.")
if parameters.get_str("usl.production.activation_candidate_fingerprint") != fingerprint:
    raise RuntimeError("The production activation identity differs from admission.")
existing = parameters.get_str("usl.production.admitted_candidate_fingerprint")
if existing and existing != fingerprint:
    raise RuntimeError("Another production candidate is already admitted.")
parameters.set_str(
    "usl.production.admitted_candidate_fingerprint",
    fingerprint,
)
env.cr.commit()  # noqa: F821
print("Admitted candidate recorded")
