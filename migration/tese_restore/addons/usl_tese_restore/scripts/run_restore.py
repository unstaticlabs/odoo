# ruff: noqa: F821, T201

import json
import os

from odoo import fields

source_database = os.environ.get(
    "TESE_SOURCE_DATABASE",
    "odoo_online_source_saas_19_3",
)
source_snapshot = os.environ.get(
    "TESE_SOURCE_SNAPSHOT",
    "odoo-online-saas-19.3-tese-payroll",
)
run = env["usl.tese.restore.run"].sudo().create({
    "source_database": source_database,
    "source_snapshot": source_snapshot,
    "target_database": env.cr.dbname,
})
try:
    statistics = run.action_restore()
except Exception:
    env.cr.rollback()
    failed_run = env["usl.tese.restore.run"].sudo().create({
        "source_database": source_database,
        "source_snapshot": source_snapshot,
        "target_database": env.cr.dbname,
        "status": "failed",
        "finished_at": fields.Datetime.now(),
    })
    env.cr.commit()
    print(json.dumps({"run_id": failed_run.id, "status": "failed"}))
    raise
env.cr.commit()
print(json.dumps({
    "run_id": run.id,
    "status": run.status,
    "statistics": statistics,
}, indent=2, sort_keys=True))
