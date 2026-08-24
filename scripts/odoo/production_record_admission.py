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
env["ir.config_parameter"].sudo().set_str(  # noqa: F821
    "usl.production.admitted_candidate_fingerprint",
    fingerprint,
)
env.cr.commit()  # noqa: F821
print("Admitted candidate recorded")
