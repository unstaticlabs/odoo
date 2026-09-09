"""Extract the reference facts the user guide generates its pages from.

Odoo shell script: it reads the running registry and prints one JSON
document (``usl-docs-reference/v1``) on its last line. ``scripts/docs-reference``
renders that document into ``docs/users/reference/`` and proves the committed
pages match it.

Everything comes from what the product modules declare: the models and fields
they define (including fields they add to native models), the selection
values of their state fields, the groups they create, the menus they add and
the settings they expose. Labels and ``help`` texts are read in the database's
default language, so the pages say what the screen says.
"""

import json
import os

# Odoo shell script: terminal output is part of its contract.
# ruff: noqa: T201

PRODUCT_MODULES = {
    "rebuild_account_migration",
    "usl_access_control",
    "usl_accounting",
    "usl_b2c",
    "usl_b2c_ingest",
    "usl_documents",
    "usl_documents_accounting",
    "usl_documents_b2c",
    "usl_docs",
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
#: Field names that carry a record's lifecycle; their values make the states page.
STATE_FIELDS = {"state", "status", "readiness_state", "expense_progress", "batch_state"}
#: Technical fields every model carries; they never belong on a user page.
TECHNICAL_FIELDS = {
    "id", "create_uid", "create_date", "write_uid", "write_date", "display_name",
    "message_ids", "message_follower_ids", "message_partner_ids", "message_is_follower",
    "message_needaction", "message_needaction_counter", "message_has_error",
    "message_has_error_counter", "message_attachment_count", "message_has_sms_error",
    "website_message_ids", "activity_ids", "activity_state", "activity_user_id",
    "activity_type_id", "activity_type_icon", "activity_date_deadline", "my_activity_date_deadline",
    "activity_summary", "activity_exception_decoration", "activity_exception_icon",
    "activity_calendar_event_id", "rating_ids", "has_message",
}

env = env  # noqa: F821  (odoo shell global)
Data = env["ir.model.data"].sudo()
Model = env["ir.model"].sudo()
Fields = env["ir.model.fields"].sudo()


def _owned(model_name):
    """Return {res_id: module} for records of ``model_name`` a product module created."""
    owned = {}
    for data in Data.search([("model", "=", model_name), ("module", "in", sorted(PRODUCT_MODULES))]):
        owned.setdefault(data.res_id, data.module)
    return owned


def _selection(field):
    return [
        {"value": item.value, "label": item.name}
        for item in field.selection_ids.sorted(lambda item: item.sequence)
    ]


# --- Fields, per model ------------------------------------------------------

field_owner = _owned("ir.model.fields")
model_owner = _owned("ir.model")
models = {}
for record in Fields.browse(sorted(field_owner)).exists():
    if record.name in TECHNICAL_FIELDS or record.name.startswith("x_"):
        continue
    if record.model not in env.registry:
        continue
    model = env[record.model]
    entry = models.setdefault(record.model, {
        "model": record.model,
        "description": record.model_id.name or "",
        "explanation": (record.model_id.explanation or "").strip(),
        "module": model_owner.get(record.model_id.id, ""),
        "owned": record.model_id.id in model_owner,
        "transient": bool(getattr(model, "_transient", False)),
        "fields": [],
    })
    entry["fields"].append({
        "name": record.name,
        "label": record.field_description or record.name,
        "type": record.ttype,
        "required": bool(record.required),
        "readonly": bool(record.readonly),
        "stored": bool(record.store),
        "relation": record.relation or "",
        "selection": _selection(record) if record.ttype == "selection" else [],
        "help": (record.help or "").strip(),
        "module": field_owner.get(record.id, ""),
    })
for entry in models.values():
    entry["fields"].sort(key=lambda field: field["name"])

# --- States -------------------------------------------------------------------

states = []
for entry in models.values():
    for field in entry["fields"]:
        if field["name"] in STATE_FIELDS and field["selection"]:
            states.append({
                "model": entry["model"],
                "description": entry["description"],
                "field": field["name"],
                "label": field["label"],
                "help": field["help"],
                "values": field["selection"],
            })
states.sort(key=lambda item: (item["description"].lower(), item["field"]))

# --- Groups -------------------------------------------------------------------

groups = []
group_owner = _owned("res.groups")
for group in env["res.groups"].sudo().browse(sorted(group_owner)).exists():
    privilege = group.privilege_id
    groups.append({
        "name": group.name,
        "full_name": group.full_name,
        "category": " / ".join(part for part in (privilege.category_id.name, privilege.name) if part) if privilege else "",
        "module": group_owner.get(group.id, ""),
        "comment": (group.comment or "").strip(),
        "implies": sorted(group.implied_ids.mapped("full_name")),
    })
groups.sort(key=lambda item: item["full_name"].lower())

# --- Menus --------------------------------------------------------------------

menus = []
menu_owner = _owned("ir.ui.menu")
for menu in env["ir.ui.menu"].sudo().browse(sorted(menu_owner)).exists():
    action = menu.action
    menus.append({
        "path": menu.complete_name,
        "module": menu_owner.get(menu.id, ""),
        "action": action.name if action else "",
        "action_type": action._name if action else "",
        "groups": sorted(menu.group_ids.mapped("full_name")),
        "sequence": menu.sequence,
    })
menus.sort(key=lambda item: item["path"].lower())

# --- Settings -----------------------------------------------------------------

settings = [
    field
    for entry in models.values() if entry["model"] == "res.config.settings"
    for field in entry["fields"]
]
settings.sort(key=lambda field: (field["module"], field["label"].lower()))

# --- Coverage -----------------------------------------------------------------

coverage = {}
for entry in models.values():
    for field in entry["fields"]:
        if field["type"] in {"one2many", "many2many", "many2one_reference", "binary"} or not field["stored"]:
            # Relations and computed helpers rarely need a tooltip; count the
            # stored scalar fields a person reads or edits.
            continue
        documented, total = coverage.get(field["module"], (0, 0))
        coverage[field["module"]] = (documented + (1 if field["help"] else 0), total + 1)

payload = {
    "schema": "usl-docs-reference/v1",
    "lang": env.user.lang or "en_US",
    "models": [models[name] for name in sorted(models)],
    "states": states,
    "groups": groups,
    "menus": menus,
    "settings": settings,
    "coverage": {module: {"documented": documented, "total": total} for module, (documented, total) in sorted(coverage.items())},
}
if os.environ.get("USL_DOCS_REFERENCE_OUTPUT"):
    with open(os.environ["USL_DOCS_REFERENCE_OUTPUT"], "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
