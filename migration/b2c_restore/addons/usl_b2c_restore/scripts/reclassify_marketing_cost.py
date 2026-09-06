# ruff: noqa: F821, T201

import json
import os

from odoo.addons.usl_b2c_restore.models.marketing_cost import run_reclassification

apply = os.environ.get("USL_B2C_MARKETING_COST_MODE", "dry_run") == "apply"
try:
    report = run_reclassification(env, apply=apply)
    env.cr.commit()
except Exception:
    env.cr.rollback()
    raise
print("B2C_MARKETING_COST_RESULT=" + json.dumps(report, sort_keys=True))
