"""Apply only environment-owned Pocket ID provider settings during recovery."""

# ruff: noqa: F821, T201

import json

enabled = env["auth.oauth.provider"]._usl_pocketid_apply_environment()
env.cr.commit()
provider = env.ref("usl_pocketid.provider_pocketid").sudo()
print(
    "POCKET_ID_RUNTIME_REPAIR="
    + json.dumps(
        {
            "client_id": provider.client_id,
            "database": env.cr.dbname,
            "enabled": bool(enabled and provider.enabled),
            "issuer": provider.usl_oidc_issuer,
            "odoo_base_url": provider.usl_public_base_url,
            "status": "applied",
        },
        sort_keys=True,
    ),
)
