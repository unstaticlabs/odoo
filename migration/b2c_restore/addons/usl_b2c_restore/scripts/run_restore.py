# ruff: noqa: F821, T201

import json

from odoo import fields

from odoo.addons.usl_b2c_restore.models.restore import run_restore

try:
    run, statistics = run_restore(env)
    env.cr.commit()
except Exception:
    env.cr.rollback()
    latest = env["usl.b2c.restore.run"].sudo().search([], order="id desc", limit=1)
    if latest:
        latest.write({"status": "failed", "finished_at": fields.Datetime.now()})
        env.cr.commit()
    raise
print("B2C_RESTORE_RESULT=" + json.dumps(statistics, sort_keys=True))
