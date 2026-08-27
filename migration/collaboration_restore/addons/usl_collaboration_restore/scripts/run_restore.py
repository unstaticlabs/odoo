# ruff: noqa: F821, T201
import json
import os

snapshot = os.environ["COLLABORATION_SOURCE_SNAPSHOT"]
source_database = os.environ.get(
    "COLLABORATION_SOURCE_DATABASE",
    "odoo_online_source_saas_19_3",
)
run = env["usl.collaboration.restore.run"].sudo().create({
    "source_snapshot": snapshot,
    "source_database": source_database,
    "target_database": env.cr.dbname,
})
try:
    statistics = run.action_restore()
    env.cr.commit()
except Exception:
    env.cr.rollback()
    failed = env["usl.collaboration.restore.run"].sudo().browse(run.id).exists()
    if failed:
        failed.write({"status": "failed"})
        env.cr.commit()
    raise
print("COLLABORATION_RESTORE_RESULT=" + json.dumps(statistics, sort_keys=True))
