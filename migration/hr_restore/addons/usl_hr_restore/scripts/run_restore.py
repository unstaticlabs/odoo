# ruff: noqa: F821, T201

import json
import os

from odoo.addons.usl_hr_restore.models.restore import HrSourceReader, source_options


snapshot = os.environ["HR_SOURCE_SNAPSHOT"]
run = env["usl.hr.restore.run"].sudo().create(
    {
        "source_database": source_options()["database"],
        "source_snapshot": snapshot,
    },
)
try:
    counts = run.restore(HrSourceReader(source_options()).read())
    env.cr.commit()
except Exception as exc:
    env.cr.rollback()
    failed = env["usl.hr.restore.run"].sudo().create(
        {
            "source_database": source_options()["database"],
            "source_snapshot": snapshot,
            "status": "failed",
            "finished_at": env.cr.now(),
            "statistics_json": {"error": str(exc)},
        },
    )
    env.cr.commit()
    raise
print(json.dumps({"run_id": run.id, "counts": counts}, indent=2, sort_keys=True))
