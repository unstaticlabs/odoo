# ruff: noqa: F821, T201
import json
import os

if os.getenv("USL_EINVOICE_LIVE_ENABLED", "0") != "0":
    raise RuntimeError("USL_EINVOICE_LIVE_ENABLED must remain 0 during restore.")
if os.getenv("USL_EREPORTING_LIVE_ENABLED", "0") != "0":
    raise RuntimeError("USL_EREPORTING_LIVE_ENABLED must remain 0 during restore.")

options = {
    "host": os.getenv("SIGN_SOURCE_DB_HOST", "accounting-source-db"),
    "port": int(os.getenv("SIGN_SOURCE_DB_PORT", "5432")),
    "user": os.getenv("SIGN_SOURCE_DB_USER", "odoo"),
    "password": os.getenv("SIGN_SOURCE_DB_PASSWORD", "odoo"),
    "database": os.getenv("SIGN_SOURCE_DATABASE", "odoo_online_source_saas_19_2"),
    "snapshot": os.getenv("SIGN_SOURCE_SNAPSHOT", "odoo-online-saas-19.2-sign"),
    "filestore": os.getenv("SIGN_SOURCE_FILESTORE", "/mnt/accounting-source/filestore"),
}
run, statistics = env["usl.sign.restore.run"].restore_from_source(env, options)
env.cr.commit()
print(json.dumps({"run_id": run.id, "status": run.status, "issues": run.issue_count, "statistics": statistics}, default=str, indent=2, sort_keys=True))
if run.status != "passed":
    raise RuntimeError(f"Sign restoration run {run.id} did not pass.")

