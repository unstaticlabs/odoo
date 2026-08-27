"""Reject any outbound work created while reconstructing a target database."""

# Odoo shell script; ``env`` is supplied by Odoo.
# ruff: noqa: F821, T201

import json
import os

queue_models = (
    "mail.mail",
    "mail.notification",
    "mail.scheduled.message",
    "mail.push",
    "sms.sms",
    "snailmail.letter",
)
counts = {
    model_name: env[model_name].sudo().search_count([])  # noqa: F821
    for model_name in queue_models
    if model_name in env.registry  # noqa: F821
}
nonempty = {name: count for name, count in counts.items() if count}
phase = os.getenv("USL_MIGRATION_OUTBOUND_PHASE", "unspecified")
if nonempty:
    raise RuntimeError(
        "Migration outbound queue boundary failed during "
        f"{phase}: {nonempty}. A reconstruction must never enqueue or send "
        "mail, inbox notifications, scheduled messages, web pushes, SMS, or "
        "physical mail.",
    )

print(
    json.dumps(
        {
            "counts": counts,
            "phase": phase,
            "status": "passed",
        },
        indent=2,
        sort_keys=True,
    ),
)
