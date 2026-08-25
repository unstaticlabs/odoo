"""Verify that the running Odoo process and database share one Pocket ID config."""

# ruff: noqa: F821, T201

import json
import os

required_environment = {
    "USL_POCKET_ID_ENABLED": os.getenv("USL_POCKET_ID_ENABLED", "").strip(),
    "USL_POCKET_ID_ISSUER": os.getenv("USL_POCKET_ID_ISSUER", "").strip(),
    "USL_POCKET_ID_CLIENT_ID": os.getenv("USL_POCKET_ID_CLIENT_ID", "").strip(),
    "USL_POCKET_ID_CLIENT_SECRET": os.getenv(
        "USL_POCKET_ID_CLIENT_SECRET",
        "",
    ),
    "USL_POCKET_ID_ODOO_BASE_URL": os.getenv(
        "USL_POCKET_ID_ODOO_BASE_URL",
        "",
    ).strip(),
    "USL_POCKET_ID_REQUIRED_GROUP": os.getenv(
        "USL_POCKET_ID_REQUIRED_GROUP",
        "",
    ).strip(),
}
missing = [name for name, value in required_environment.items() if not value]
if required_environment["USL_POCKET_ID_ENABLED"].lower() not in {
    "1",
    "true",
    "yes",
    "on",
}:
    missing.append("USL_POCKET_ID_ENABLED=1")
if missing:
    message = "Odoo runtime is missing Pocket ID configuration: " + ", ".join(
        sorted(set(missing)),
    )
    raise RuntimeError(message)

provider = env.ref("usl_pocketid.provider_pocketid", raise_if_not_found=False).sudo()
if not provider:
    message = "Pocket ID provider is not installed in the target database."
    raise RuntimeError(message)

expected = {
    "enabled": True,
    "usl_pocketid": True,
    "client_id": required_environment["USL_POCKET_ID_CLIENT_ID"],
    "usl_oidc_issuer": required_environment["USL_POCKET_ID_ISSUER"].rstrip("/"),
    "usl_public_base_url": required_environment[
        "USL_POCKET_ID_ODOO_BASE_URL"
    ].rstrip("/"),
    "usl_required_group": required_environment["USL_POCKET_ID_REQUIRED_GROUP"],
}
mismatches = []
for field_name, expected_value in expected.items():
    actual_value = provider[field_name]
    if isinstance(actual_value, str):
        actual_value = actual_value.rstrip("/")
    if actual_value != expected_value:
        mismatches.append(field_name)
if mismatches:
    message = "Pocket ID provider differs from the Odoo runtime: " + ", ".join(
        sorted(mismatches),
    )
    raise RuntimeError(message)

summary = {
    "client_id": provider.client_id,
    "database": env.cr.dbname,
    "issuer": provider.usl_oidc_issuer,
    "odoo_base_url": provider.usl_public_base_url,
    "required_group": provider.usl_required_group,
    "secret_present": True,
    "status": "ready",
}
print("POCKET_ID_RUNTIME_CHECK=" + json.dumps(summary, sort_keys=True))
