# ruff: noqa: F821, T201
import json

module = env["ir.module.module"].sudo().search([("name", "=", "usl_sign_restore")], limit=1)
assert not module or module.state == "uninstalled", "Temporary Sign restoration module is still installed."
for model in ("usl.sign.restore.run", "usl.sign.restore.issue"):
    assert not env["ir.model"].sudo().search_count([("model", "=", model)]), f"Migration model {model} remains in the product registry."
for model_name in ("sign.oca.template", "sign.oca.request", "sign.oca.request.signer", "usl.sign.evidence"):
    forbidden = env["ir.model.fields"].sudo().search_count([("model", "=", model_name), ("name", "like", "rebuild_source_%")])
    assert not forbidden, f"Migration provenance leaked onto {model_name}."
requests = env["sign.oca.request"].sudo().search([("historical", "=", True), ("provider_code", "=", "odoo_online")])
assert all(request.state == "completed" and request.evidence_status == "available" for request in requests), "Historical evidence is incomplete."
print(json.dumps({"product_boundary": "passed", "historical_requests": len(requests)}, indent=2, sort_keys=True))

