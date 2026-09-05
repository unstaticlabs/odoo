import hashlib
import json
import os

# Odoo shell acceptance script with concise failures and intentional output.
# ruff: noqa: EM101, T201

PRODUCT_MODULES = {
    "rebuild_account_migration",
    "usl_access_control",
    "usl_accounting",
    "usl_b2c",
    "usl_documents",
    "usl_documents_accounting",
    "usl_documents_b2c",
    "usl_expense_batch",
    "usl_feedback",
    "usl_home",
    "usl_locale",
    "usl_platform_billing",
    "usl_platform_billing_pocketid",
    "usl_pocketid",
    "usl_project",
    "usl_sign",
    "usl_tese_accounting",
    "usl_tese_payroll",
}


def canonical_sha256(payload):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


raw_identity = os.environ.get("USL_RELEASE_IDENTITY_JSON", "")
if not raw_identity:
    raise RuntimeError("USL_RELEASE_IDENTITY_JSON is required.")
identity = json.loads(raw_identity)
if identity.get("schema") != "usl-release-identity-v1":
    raise RuntimeError("The release identity schema is unsupported.")
expected_identity_sha = identity.get("identity_sha256")
unsigned_identity = {
    key: value for key, value in identity.items() if key != "identity_sha256"
}
if canonical_sha256(unsigned_identity) != expected_identity_sha:
    raise RuntimeError("The supplied release identity digest is invalid.")

image = identity.get("image") or {}
if os.environ.get("USL_RELEASE_COMMIT") != identity.get("release_commit"):
    raise RuntimeError("The running image commit does not match the release.")
if os.environ.get("USL_OCA_BUNDLE_SHA256") != (
    identity.get("oca") or {}
).get("bundle_sha256"):
    raise RuntimeError("The running OCA bundle does not match the release.")
if os.environ.get("USL_ACTION_RISK_POLICY_SHA256") != identity.get(
    "action_risk_policy_sha256",
):
    raise RuntimeError("The running action-risk policy does not match the release.")

Modules = env["ir.module.module"].sudo()  # noqa: F821
installed_modules = Modules.search([("state", "=", "installed")])
installed_versions = {
    module.name: module.installed_version or ""
    for module in installed_modules.sorted("name")
}
expected_versions = identity.get("product_module_versions") or {}
if set(expected_versions) != PRODUCT_MODULES:
    raise RuntimeError("The release product-module perimeter is incomplete.")
for module_name, expected_version in sorted(expected_versions.items()):
    if installed_versions.get(module_name) != expected_version:
        raise RuntimeError(
            f"Installed {module_name} version "
            f"{installed_versions.get(module_name)!r} does not match "
            f"{expected_version!r}.",
        )

params = env["ir.config_parameter"].sudo()  # noqa: F821
database_identity = {
    **identity,
    "database": {
        "name": env.cr.dbname,  # noqa: F821
        "uuid": params.get_str("database.uuid"),
        "installed_module_versions": installed_versions,
    },
}
database_identity["database_identity_sha256"] = canonical_sha256(
    database_identity,
)
serialized = json.dumps(database_identity, sort_keys=True, separators=(",", ":"))

if os.environ.get("USL_RELEASE_IDENTITY_APPLY") == "1":
    params.set_str("usl.release.identity", serialized)
    params.set_str(
        "usl.release.identity_sha256",
        database_identity["database_identity_sha256"],
    )
    params.set_str("usl.release.commit", identity["release_commit"])
    params.set_str(
        "usl.release.action_risk_policy_sha256",
        identity["action_risk_policy_sha256"],
    )
    params.set_str(
        "usl.release.source_dump_sha256",
        identity["source"]["dump_sha256"],
    )
    params.set_str("usl.release.image", image.get("reference", ""))
    env.cr.commit()  # noqa: F821
else:
    stored = params.get_str("usl.release.identity")
    if stored != serialized:
        raise RuntimeError(
            "The database release identity does not match its runtime artifact.",
        )

print(json.dumps(database_identity, indent=2, sort_keys=True))
