# ruff: noqa: F821, T201

import json
import os

from odoo import fields

from odoo.addons.usl_b2c_restore.models.native_history import run_native_history

mode = os.environ.get("USL_B2C_NATIVE_HISTORY_MODE", "dry_run")
try:
    run, report = run_native_history(env, mode=mode)
    env.cr.commit()
except Exception as error:
    env.cr.rollback()
    env["usl.b2c.native.history.run"].sudo().create(
        {
            "mode": mode,
            "state": "failed",
            "finished_at": fields.Datetime.now(),
            "report_json": {"error": str(error)},
        },
    )
    env.cr.commit()
    raise
print("B2C_NATIVE_HISTORY_RESULT=" + json.dumps(report, sort_keys=True))
