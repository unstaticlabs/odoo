# ruff: noqa: F821, T201
"""Clear the disposable Paperless mirror before a canonical archive rebuild."""

import json
import os

if os.environ.get("DOCUMENTS_CANONICAL_CACHE_RESET") != "1":
    message = "Canonical Documents cache reset was not explicitly enabled."
    raise RuntimeError(message)
if env.cr.dbname != "odoo_dev":
    raise RuntimeError(f"Refusing Documents cache reset on {env.cr.dbname!r}.")

models = (
    "usl.document.operation",
    "usl.document.link",
    "usl.document",
)
before = {
    model_name: env[model_name].sudo().search_count([])
    for model_name in models
}
for model_name in models:
    env[model_name].sudo().search([]).unlink()

after = {
    model_name: env[model_name].sudo().search_count([])
    for model_name in models
}
if any(after.values()):
    raise RuntimeError(f"Documents cache reset is incomplete: {after}")

env.cr.commit()
print(
    "DOCUMENTS_CANONICAL_CACHE_RESET="
    + json.dumps({"before": before, "after": after}, sort_keys=True),
)
