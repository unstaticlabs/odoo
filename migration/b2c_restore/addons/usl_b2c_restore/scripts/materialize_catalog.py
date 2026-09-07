# ruff: noqa: F821, T201

import json
import os

from odoo.addons.usl_b2c_restore.models.catalog import run_catalog

apply = os.environ.get("USL_B2C_CATALOG_MODE", "apply") == "apply"
try:
    report = run_catalog(env, apply=apply)
    env.cr.commit()
except Exception:
    env.cr.rollback()
    raise
print("B2C_CATALOG_RESULT=" + json.dumps(report, sort_keys=True))
