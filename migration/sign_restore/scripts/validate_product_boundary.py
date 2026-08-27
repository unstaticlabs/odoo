# ruff: noqa: F821, T201
"""Prove the finalized target contains no Sign migration machinery."""

import json

forbidden_models = {
    "usl.sign.restore.run",
    "usl.sign.restore.binding",
    "sign.request",
    "sign.request.item",
    "sign.log",
}
loaded = sorted(forbidden_models & set(env.registry.models))  # noqa: F821
if loaded:
    raise RuntimeError(f"Legacy or migration Sign models remain loaded: {loaded}")
bindings = env["ir.model.data"].sudo().search_count(  # noqa: F821
    [("module", "=", "usl_sign_restore")],
)
if bindings:
    raise RuntimeError(f"Temporary Sign source bindings remain: {bindings}")
forbidden_fields = sorted(
    (model_name, field_name)
    for model_name in ("sign.oca.request", "sign.oca.request.signer")
    for field_name in env[model_name]._fields  # noqa: F821
    if field_name.startswith(("legacy_", "source_sign_", "odoo_online_"))
)
if forbidden_fields:
    raise RuntimeError(f"Legacy Sign fields remain: {forbidden_fields}")
requests = env["sign.oca.request"].sudo().search(  # noqa: F821
    [("record_kind", "=", "external_archive")],
)
if requests.filtered(lambda row: row.state != "external_archived"):
    message = "An external Sign record escaped its terminal archive state"
    raise RuntimeError(message)
print(
    json.dumps(
        {
            "external_records": len(requests),
            "legacy_models": [],
            "migration_bindings": 0,
            "migration_fields": [],
            "status": "passed",
        },
        sort_keys=True,
    ),
)
