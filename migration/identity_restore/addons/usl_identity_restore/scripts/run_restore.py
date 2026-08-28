# ruff: noqa: F821, T201

import json
import os

from odoo import fields

from odoo.addons.usl_identity_restore.models.restore import (
    IdentitySourceReader,
    source_options,
)

source = IdentitySourceReader(source_options()).read()
run = env["usl.identity.restore.run"].sudo().create(
    {
        "source_database": source_options()["database"],
        "source_snapshot": os.environ["IDENTITY_SOURCE_SNAPSHOT"],
    },
)
try:
    counts = run.restore(source)
    env.cr.commit()
except Exception:
    env.cr.rollback()
    run = env["usl.identity.restore.run"].sudo().browse(run.id)
    if run.exists():
        run.write({"status": "failed", "finished_at": fields.Datetime.now()})
        env.cr.commit()
    raise
print("IDENTITY_RESTORE_RESULT=" + json.dumps(counts, sort_keys=True))
