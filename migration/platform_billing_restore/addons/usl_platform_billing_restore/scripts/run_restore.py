# ruff: noqa: F821, T201

import json
import os
from pathlib import Path

from odoo.addons.usl_platform_billing_restore.models.restore import (
    default_source_options,
)

run, statistics = env[
    "usl.platform.billing.restore.run"
].restore_from_source(env, default_source_options())
env.cr.commit()
evidence = {
    "run_id": run.id,
    "status": run.status,
    "issues": run.issue_count,
    "statistics": statistics,
}
evidence_path = os.getenv("PLATFORM_BILLING_EVIDENCE_PATH")
if evidence_path:
    path = Path(evidence_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(evidence, default=str, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
print(json.dumps(evidence, default=str, indent=2, sort_keys=True))
if run.status != "passed":
    raise RuntimeError(
        f"Platform billing restoration run {run.id} has blocking discrepancies.",
    )
