# ruff: noqa: F821, T201 -- Odoo shell injects env; stdout is wrapper input.
"""Configure and verify the isolated Sign-to-Paperless connection."""

import json
import os

token = os.environ.get("PAPERLESS_SIGN_TOKEN", "").strip()
service_user_id = int(os.environ.get("PAPERLESS_SIGN_SERVICE_USER_ID", "0"))
if not token or service_user_id <= 0:
    msg = "The Paperless Sign integration identity is incomplete"
    raise RuntimeError(msg)

params = env["ir.config_parameter"].sudo()
params.set_str(
    "usl_documents.paperless_url",
    os.environ.get("PAPERLESS_SIGN_INTERNAL_URL", "http://paperless-webserver:8000"),
)
params.set_str(
    "usl_documents.paperless_public_url",
    os.environ.get("PAPERLESS_SIGN_PUBLIC_URL", "http://127.0.0.1:16810"),
)
params.set_str("usl_documents.paperless_token", token)
params.set_int("usl_documents.paperless_timeout", 20)
params.set_int("usl_documents.paperless_service_user_id", service_user_id)

# The isolated QA tenant must be able to send attributable invitations and the
# final dossier.  Keep these product defaults in the stack configuration rather
# than hiding them inside an acceptance fixture.
company = env.company
company.write(
    {
        "email": company.email or "sign-qa@preproduction.invalid",
        "sign_oca_send_sign_request_copy": True,
    },
)

client = env["usl.document"].sudo()._paperless()
compatibility = client.compatibility()
policy = client.ensure_fail_closed_ingestion_policy()
env.cr.commit()
print(
    json.dumps(
        {
            "api_version": compatibility["api_version"],
            "configured": True,
            "final_dossier_copy_enabled": company.sign_oca_send_sign_request_copy,
            "fail_closed_workflow_id": policy["workflow_id"],
            "server_version": compatibility["server_version"],
            "service_user_id": service_user_id,
        },
        indent=2,
        sort_keys=True,
    ),
)
