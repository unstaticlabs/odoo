# ruff: noqa: F821, T201

import json


run = env["rebuild.account.import.run"].sudo().search(
    [("source_snapshot_id", "!=", False)],
    order="id desc",
    limit=1,
)
if not run:
    raise RuntimeError("No Accounting restoration run is available for repair.")

statistics = run.repair_final_account_move_attachment_targets()
env.cr.commit()
print(
    "ACCOUNTING_ATTACHMENT_TARGET_RESULT="
    + json.dumps(statistics, sort_keys=True),
)
