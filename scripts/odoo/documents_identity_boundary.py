import json

# Odoo shell acceptance script with concise failures and intentional output.
# ruff: noqa: T201

ROLE_GROUPS = {
    "administrator": "usl_documents.group_documents_manager",
    "collaborator": "usl_documents.group_documents_user",
    "accounting_evidence_reader": "usl_documents.group_documents_accountant",
}

Users = env["res.users"].sudo()  # noqa: F821
Mappings = env["usl.paperless.user.mapping"].sudo()  # noqa: F821
Documents = env["usl.document"].sudo()  # noqa: F821

personas = Users.search(
    [
        ("active", "=", True),
        ("share", "=", False),
        ("usl_pocketid_access", "=", True),
    ],
).filtered(
    lambda user: any(user.has_group(xmlid) for xmlid in ROLE_GROUPS.values()),
)

results = []
failures = []
for user in personas.sorted("login"):
    roles = [
        role
        for role, xmlid in ROLE_GROUPS.items()
        if user.has_group(xmlid)
    ]
    mapping = Mappings.search(
        [("user_id", "=", user.id), ("active", "=", True)],
        limit=1,
    )
    errors = []
    visible_ids = user._documents_visible_for_permission_sync()[user.id]
    direct_documents = Documents.browse(list(visible_ids)).filtered(
        lambda document: (
            document.paperless_id
            and document.availability_state == "available"
        ),
    )
    visible_documents = len(direct_documents)
    unsynchronized_documents = len(
        direct_documents.filtered(
            lambda document: document.permission_sync_state != "synchronized",
        ),
    )
    if not mapping:
        errors.append("missing verified Paperless identity mapping")
    else:
        if mapping.sync_state != "synchronized":
            errors.append(f"mapping state is {mapping.sync_state}")
        identity_error = mapping._identity_error()
        if identity_error:
            errors.append(str(identity_error))
        if unsynchronized_documents:
            errors.append(
                f"{unsynchronized_documents} visible document permission sets "
                "are not synchronized",
            )

    status = "pass" if not errors else "fail"
    result = {
        "login": user.login,
        "name": user.name,
        "roles": roles,
        "status": status,
        "paperless_user_id": mapping.paperless_user_id if mapping else None,
        "visible_documents": visible_documents,
        "unsynchronized_documents": unsynchronized_documents,
        "errors": errors,
    }
    results.append(result)
    if errors:
        failures.append(user.login)

summary = {
    "database": env.cr.dbname,  # noqa: F821
    "persona_count": len(personas),
    "ready_count": len(personas) - len(failures),
    "personas": results,
}
print(json.dumps(summary, indent=2, sort_keys=True))

if not personas:
    raise RuntimeError(
        "No active Pocket ID Documents personas are configured for direct "
        "Paperless acceptance.",
    )
if failures:
    raise RuntimeError(
        "Direct Paperless access is not ready for: "
        f"{', '.join(failures)}. Each person must first sign in to Paperless "
        "with Pocket ID; a Documents administrator must then map the resulting "
        "individual Paperless user and run Verify identity.",
    )
