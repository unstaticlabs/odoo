# ruff: noqa: F821, T201

import json

from odoo.addons.usl_b2c_restore.native_plan import accepted_finalization_run

restore_run = env["usl.b2c.restore.run"].sudo().search([], order="id desc", limit=1)
native_run = env["usl.b2c.native.history.run"].sudo().search(
    [("mode", "=", "apply")],
    order="id desc",
    limit=1,
)
accepted_run = accepted_finalization_run(
    restore_status=restore_run.status if restore_run else None,
    native_mode=native_run.mode if native_run else None,
    native_state=native_run.state if native_run else None,
)
models = (
    "b2c.channel",
    "b2c.order",
    "b2c.order.line",
    "b2c.order.source",
    "b2c.payment.event",
    "b2c.fulfilment.event",
    "b2c.product.alias",
    "b2c.accounting.session",
    "b2c.accounting.link",
    "b2c.provider.evidence",
)
before = {model: env[model].sudo().search_count([]) for model in models}
module = env["ir.module.module"].sudo().search(
    [("name", "=", "usl_b2c_restore")],
    limit=1,
)
module.button_immediate_uninstall()
env.cr.commit()
after = {model: env[model].sudo().search_count([]) for model in models}
assert before == after
print(
    json.dumps(
        {
            "accepted_run": accepted_run,
            "migration_module": "uninstalled",
            "before": before,
            "after": after,
        },
        indent=2,
        sort_keys=True,
    ),
)
