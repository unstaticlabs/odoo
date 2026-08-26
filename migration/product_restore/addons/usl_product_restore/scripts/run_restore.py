# ruff: noqa: F821, T201

import json
import os

from odoo import fields

from odoo.addons.usl_product_restore.models.restore import (
    ProductSourceReader,
    source_options,
)

source = ProductSourceReader(source_options()).read()
run = env["usl.product.restore.run"].sudo().create(
    {
        "source_database": source_options()["database"],
        "source_snapshot": os.environ["PRODUCT_SOURCE_SNAPSHOT"],
    },
)
try:
    counts = run.restore(source)
    env.cr.commit()
except Exception:
    env.cr.rollback()
    run = env["usl.product.restore.run"].sudo().browse(run.id)
    if run.exists():
        run.write({"status": "failed", "finished_at": fields.Datetime.now()})
        env.cr.commit()
    raise
print("PRODUCT_RESTORE_RESULT=" + json.dumps(counts, sort_keys=True))
