# ruff: noqa: F821, T201 -- Odoo shell injects env; stdout is wrapper input.
"""Issue the next sequential Strong signer session after the prior revision."""

import json
import os

request_id = int(os.environ.get("USL_SIGN_ACCEPTANCE_REQUEST_ID", "0"))
if request_id <= 0:
    msg = "A Strong acceptance request ID is required"
    raise RuntimeError(msg)
sign_request = env["sign.oca.request"].browse(request_id).exists()
if not sign_request or sign_request.requested_trust != "strong_personal":
    msg = "The Strong acceptance request is unavailable"
    raise RuntimeError(msg)
pending = sign_request.signer_ids.filtered(lambda signer: signer.state != "signed").sorted(
    lambda signer: (signer.sequence, signer.id),
)
prior = sign_request.signer_ids - pending
if (
    len(pending) != 1
    or pending.state not in {"notified", "viewed", "authorized"}
    or pending.access_revoked
    or any(signer.state != "signed" for signer in prior)
    or sign_request.state != "partial"
):
    msg = "The next Strong signer is not ready after the preceding revision"
    raise RuntimeError(msg)
signer = pending
access_token = signer._issue_access_token()
session_token = signer._exchange_access_token(access_token)
base_url = env["ir.config_parameter"].sudo().get_str("web.base.url").rstrip("/")
env.cr.commit()
payload = {
    "request_id": sign_request.id,
    "signer_id": signer.id,
    "username": "valentin",
    "signing_url": f"{base_url}/sign/session/{signer.id}/{session_token}?review=1",
}
print("USL_SIGN_STRONG_ACCEPTANCE=" + json.dumps(payload, sort_keys=True))
