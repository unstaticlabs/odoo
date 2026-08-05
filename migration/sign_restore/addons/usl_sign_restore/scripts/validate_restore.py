# ruff: noqa: F821, T201
import base64
import hashlib
import json

run = env["usl.sign.restore.run"].sudo().search([], order="id desc", limit=1)
assert run and run.status == "passed", "Latest Sign restoration did not pass."
assert not run.issue_ids.filtered(lambda issue: issue.severity == "error" and not issue.resolved), "Blocking Sign restoration issues remain."

requests = env["sign.oca.request"].sudo().search([("historical", "=", True), ("provider_code", "=", "odoo_online")])
assert len(requests) >= run.statistics_json["target"]["request_links"], "Historical request count regressed."
for request in requests:
    assert request.state == "completed" and request.signed, f"Historical request {request.id} is not completed."
    assert request.migration_assurance_unproven, f"Historical request {request.id} overstates assurance."
    assert not request.achieved_assurance and not request.authentication_method, f"Historical request {request.id} claims unverified assurance."
    evidence = request.evidence_ids
    assert evidence.filtered(lambda item: item.kind == "original"), f"Historical request {request.id} lacks original evidence."
    assert evidence.filtered(lambda item: item.kind == "signed"), f"Historical request {request.id} lacks signed evidence."
    assert evidence.filtered(lambda item: item.kind == "completion_evidence"), f"Historical request {request.id} lacks completion evidence."
    assert evidence.filtered(lambda item: item.kind == "audit_trail"), f"Historical request {request.id} lacks source audit evidence."
    for item in evidence:
        data = base64.b64decode(item.with_context(bin_size=False).data)
        assert hashlib.sha256(data).hexdigest() == item.sha256, f"Evidence {item.id} hash mismatch."

print(json.dumps({"run_id": run.id, "historical_requests": len(requests), "evidence": sum(len(request.evidence_ids) for request in requests)}, indent=2, sort_keys=True))
