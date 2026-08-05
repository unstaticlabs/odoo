# ruff: noqa: F821, T201
import json

module = env["ir.module.module"].sudo().search([("name", "=", "usl_sign_restore")], limit=1)
if not module or module.state != "installed":
    raise RuntimeError("usl_sign_restore must be installed before finalization.")
run = env["usl.sign.restore.run"].sudo().search([], order="id desc", limit=1)
if not run or run.status != "passed" or run.issue_ids.filtered(lambda issue: issue.severity == "error" and not issue.resolved):
    raise RuntimeError("A clean, passing Sign restoration is required before finalization.")

def business_state():
    requests = env["sign.oca.request"].sudo().search([("historical", "=", True), ("provider_code", "=", "odoo_online")])
    return {
        "templates": env["sign.oca.template"].sudo().with_context(active_test=False).search_count([]),
        "requests": len(requests),
        "signers": sum(len(request.signer_ids) for request in requests),
        "evidence": sum(len(request.evidence_ids) for request in requests),
        "document_hashes": sorted((request.original_sha256, request.final_sha256) for request in requests),
    }

before = business_state()
module.button_immediate_uninstall()
env.cr.commit()
after = business_state()
if before != after:
    raise RuntimeError(f"Sign business evidence changed during finalization: {before} -> {after}.")
print(json.dumps({"migration_module": "uninstalled", "business_state": after}, indent=2, sort_keys=True))

