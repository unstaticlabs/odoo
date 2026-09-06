"""Emit exact action-surface facts from the current Odoo registry.

Run with ``odoo shell --database=... < this_file``.  The final stdout line is
one deterministic JSON document using the ``usl-action-risk-runtime-v1``
schema; ordinary Odoo logs remain on stderr.
"""

# Odoo shell provides ``env`` as a global and the JSON line is the contract.
# ruff: noqa: F821, T201

import ast
import hashlib
import inspect
import json
import os
import re

from lxml import etree

SCHEMA = "usl-action-risk-runtime-v1"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def module_name(value):
    components = getattr(value, "__module__", "").split(".")
    if components[:2] == ["odoo", "addons"] and len(components) > 2:
        return components[2]
    return "base"


def source_path(value):
    try:
        path = inspect.getsourcefile(value) or inspect.getfile(value)
    except (OSError, TypeError):
        return "runtime"
    match = re.search(
        r"/(custom-addons|oca-addons|addons|odoo/addons)/([^/]+)/(.*)$",
        path,
    )
    if match:
        return "/".join(match.groups())
    if path.endswith("/odoo/orm/models.py"):
        return "odoo/orm/models.py"
    return path.rsplit("/", 3)[-1]


def callable_fragment(value):
    try:
        source = inspect.getsource(value)
    except (OSError, TypeError):
        code = getattr(value, "__code__", None)
        if code is None:
            return {"callable": repr(type(value))}

        def constant_fragment(item):
            if inspect.iscode(item):
                return {
                    "bytecode": item.co_code.hex(),
                    "constants": [constant_fragment(value) for value in item.co_consts],
                    "names": list(item.co_names),
                }
            if isinstance(item, (str, int, float, bool)) or item is None:
                return item
            return repr(type(item))

        return {
            "bytecode": code.co_code.hex(),
            "constants": [constant_fragment(item) for item in code.co_consts],
            "names": list(code.co_names),
        }
    try:
        return ast.dump(ast.parse(source), include_attributes=False)
    except SyntaxError:
        return " ".join(source.split())


def stable_value(value):
    if callable(value):
        return callable_fragment(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): stable_value(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((stable_value(item) for item in value), key=canonical)
    if isinstance(value, (list, tuple)):
        return [stable_value(item) for item in value]
    return repr(type(value))


def public_method_chain(model_class, method_name):
    chain = []
    for cls in model_class.mro():
        value = cls.__dict__.get(method_name)
        if value is None:
            continue
        if isinstance(value, (classmethod, staticmethod)):
            return []
        if getattr(value, "_api_private", False):
            return []
        if not callable(value):
            continue
        chain.append(
            {
                "module": module_name(value),
                "source": source_path(value),
                "fragment": callable_fragment(value),
            },
        )
    return chain


def rpc_actions():
    result = []
    for model_name, model_class in sorted(env.registry.models.items()):
        for method_name in sorted(dir(model_class)):
            if method_name.startswith("_"):
                continue
            try:
                method = getattr(model_class, method_name)
            except (AttributeError, TypeError):
                continue
            if not callable(method):
                continue
            chain = public_method_chain(model_class, method_name)
            if not chain:
                continue
            result.append(
                {
                    "key": f"rpc:{model_name}.{method_name}",
                    "kind": "rpc",
                    "model": model_name,
                    "method": method_name,
                    "abstract": bool(getattr(model_class, "_abstract", False)),
                    "archiveable": bool(
                        "active" in getattr(model_class, "_fields", {})
                        and getattr(model_class._fields["active"], "type", None)
                        == "boolean",
                    ),
                    "transient": bool(getattr(model_class, "_transient", False)),
                    "modules": sorted({item["module"] for item in chain}),
                    "sources": sorted(
                        ({"path": item["source"]} for item in chain),
                        key=lambda item: item["path"],
                    ),
                    "digest": digest(chain),
                },
            )
    return result


def route_actions():
    result = {}
    routing_map = env["ir.http"].routing_map()
    for rule in routing_map.iter_rules():
        endpoint = rule.endpoint
        routing = getattr(endpoint, "routing", {})
        route_type = routing.get("type", "http")
        methods = tuple(sorted(routing.get("methods") or ()))
        method_text = ",".join(methods) if methods else "ANY"
        key = f"route:{route_type}:{method_text}:{rule.rule}"
        wrapped = inspect.unwrap(endpoint)
        action = result.setdefault(
            key,
            {
                "key": key,
                "kind": "route",
                "route": rule.rule,
                "modules": [],
                "sources": [],
                "_fragments": [],
            },
        )
        action["modules"].append(module_name(wrapped))
        action["sources"].append({"path": source_path(wrapped)})
        action["_fragments"].append(
            {
                "auth": routing.get("auth"),
                "csrf": routing.get("csrf"),
                "method": callable_fragment(wrapped),
                "methods": methods,
                "readonly": stable_value(routing.get("readonly")),
                "route": rule.rule,
                "type": route_type,
            },
        )
    for action in result.values():
        action["modules"] = sorted(set(action["modules"]))
        action["sources"] = sorted(
            {canonical(item): item for item in action["sources"]}.values(),
            key=lambda item: item["path"],
        )
        action["digest"] = digest(sorted(action.pop("_fragments"), key=canonical))
    return list(result.values())


def external_id(record):
    value = record.get_external_id().get(record.id)
    return value or f"database:{record.id}"


def record_value(record, field):
    if field not in record._fields:
        return None
    value = record[field]
    field_type = record._fields[field].type
    if field_type == "many2one":
        if not value:
            return None
        return value.get_external_id().get(value.id) or f"database:{value._name}:{value.id}"
    if field_type in {"many2many", "one2many"}:
        external_ids = value.get_external_id()
        return sorted(
            external_ids.get(item.id) or f"database:{item._name}:{item.id}"
            for item in value
        )
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return stable_value(value)


def database_action_records(model, kind, prefix, fields):
    result = []
    if model not in env.registry:
        return result
    for record in env[model].sudo().with_context(active_test=False).search([]):
        xmlid = external_id(record)
        values = {field: record_value(record, field) for field in fields}
        result.append(
            {
                "key": f"{prefix}:{xmlid}",
                "kind": kind,
                "xmlid": xmlid,
                "modules": [xmlid.split(".", 1)[0]] if "." in xmlid else ["database"],
                "sources": [{"path": f"database:{model}:{xmlid}"}],
                "digest": digest(values),
            },
        )
    return result


def _ui_action_xmlid(value):
    """Resolve a view button's action name to one stable external ID."""

    match = re.fullmatch(r"%\(([^)]+)\)d", value or "")
    if match:
        return match.group(1)
    if value and value.isdigit():
        action = env["ir.actions.actions"].sudo().browse(int(value)).exists()
        if action:
            concrete_model = action.type if action.type in env.registry else action._name
            return external_id(env[concrete_model].sudo().browse(action.id))
    return None


def ui_action_records():
    """Inventory reports, client/window actions, and other UI action records."""

    fields = (
        "binding_model_id",
        "binding_type",
        "binding_view_types",
        "context",
        "domain",
        "name",
        "params",
        "path",
        "report_name",
        "report_type",
        "res_model",
        "tag",
        "target",
        "type",
    )
    result = []
    for action in env["ir.actions.actions"].sudo().search([], order="id"):
        concrete_model = action.type if action.type in env.registry else action._name
        concrete = env[concrete_model].sudo().browse(action.id)
        xmlid = external_id(concrete)
        values = {
            field: record_value(concrete, field)
            for field in fields
            if field in concrete._fields
        }
        delegates = (
            [f"server_action:{xmlid}"]
            if concrete_model == "ir.actions.server"
            else []
        )
        result.append(
            {
                "key": f"ui:{xmlid}",
                "kind": "ui",
                "xmlid": xmlid,
                "ui_type": concrete_model,
                "modules": [xmlid.split(".", 1)[0]] if "." in xmlid else ["database"],
                "sources": [{"path": f"database:{concrete_model}:{xmlid}"}],
                "delegates": delegates,
                "digest": digest(values),
            },
        )
    return result


def _button_model(view_model, button):
    """Resolve relational subviews/group-by buttons to their record model."""

    model_name = view_model
    ancestors = list(button.iterancestors())
    ancestors.reverse()
    for ancestor in ancestors:
        field_names = []
        if ancestor.tag == "xpath":
            expression = ancestor.get("expr", "")
            matches = list(re.finditer(
                r"field\s*\[\s*@name\s*=\s*['\"]([^'\"]+)",
                expression,
            ))
            # The last field is normally the insertion anchor, so buttons
            # operate on that field's record, not its comodel.  Traverse it
            # only when the XPath explicitly descends into a relational
            # subview after the field.
            field_names = [match.group(1) for match in matches[:-1]]
            if matches and re.search(
                r"/(?:/)?(?:button|form|kanban|list|tree)\b",
                expression[matches[-1].end():],
            ):
                field_names.append(matches[-1].group(1))
        elif ancestor.tag in {"field", "groupby"}:
            # A field used as an inheritance anchor (position=...) does not
            # make inserted buttons operate on that field's comodel.
            if ancestor.tag == "field" and ancestor.get("position"):
                continue
            field_names = [ancestor.get("name")]
        for field_name in field_names:
            if not field_name or model_name not in env.registry:
                continue
            field = env[model_name]._fields.get(field_name)
            comodel_name = getattr(field, "comodel_name", None)
            if comodel_name:
                model_name = comodel_name
    return model_name


def ui_view_buttons():
    """Inventory every stored view button after XML-ID/action resolution."""

    result = []
    views = env["ir.ui.view"].sudo().with_context(active_test=False).search([], order="id")
    for view in views:
        xmlid = external_id(view)
        arch = view.arch_db
        if not isinstance(arch, str) or not arch.strip():
            continue
        try:
            root = etree.fromstring(arch.encode())
        except etree.XMLSyntaxError as error:
            raise RuntimeError(f"Stored view {xmlid} has invalid XML: {error}") from error
        counts = {}
        for button in root.xpath(".//button[@name][@type='action' or @type='object']"):
            button_type = button.get("type")
            raw_name = button.get("name")
            delegates = []
            stable_name = raw_name
            if button_type == "object":
                model_name = _button_model(view.model, button)
                if model_name:
                    delegates.append(f"rpc:{model_name}.{raw_name}")
            else:
                target_xmlid = _ui_action_xmlid(raw_name)
                if target_xmlid:
                    stable_name = target_xmlid
                    delegates.append(f"ui:{target_xmlid}")
            normalized_button = etree.fromstring(
                etree.tostring(button, with_tail=False),
            )
            if button_type == "action" and stable_name:
                # Stored view arches contain database-local numeric action IDs.
                # The resolved XML ID is the delivered behavior; normalizing the
                # attribute keeps independently installed registries comparable.
                normalized_button.set("name", stable_name)
            count_key = (button_type, stable_name)
            counts[count_key] = counts.get(count_key, 0) + 1
            result.append(
                {
                    "key": (
                        f"ui:{xmlid}:button:{button_type}:{stable_name}:"
                        f"{counts[count_key]}"
                    ),
                    "kind": "ui",
                    "xmlid": xmlid,
                    "modules": [xmlid.split(".", 1)[0]] if "." in xmlid else ["database"],
                    "sources": [{"path": f"database:ir.ui.view:{xmlid}"}],
                    "delegates": delegates,
                    "digest": digest(
                        {
                            "button": etree.tostring(
                                normalized_button,
                                encoding="unicode",
                            ),
                            "model": view.model,
                            "resolved_model": (
                                _button_model(view.model, button)
                                if button_type == "object"
                                else None
                            ),
                            "target": delegates,
                            "view": xmlid,
                        },
                    ),
                },
            )
    return result


def modules():
    return [
        {"name": record.name, "version": record.installed_version or ""}
        for record in env["ir.module.module"]
        .sudo()
        .search(
            [("state", "=", "installed")],
            order="name",
        )
    ]


def normalized_module_version(version):
    """Compare manifest and database versions without Odoo's series prefix."""

    prefix = "saas~19.3."
    if not isinstance(version, str) or not version:
        return "1.0"
    return version.removeprefix(prefix)


actions = rpc_actions()
actions.extend(route_actions())
actions.extend(ui_action_records())
actions.extend(ui_view_buttons())
actions.extend(
    database_action_records(
        "ir.cron",
        "cron",
        "cron",
        # ``target-finalize`` pauses scheduled jobs while it validates the
        # reconstructed database and restores their governed state only after
        # every release gate passes.  ``active`` is therefore operational
        # state, not part of the reviewed action definition.  Including it
        # made the exact same job fail qualification merely because the gate
        # was doing its safety pause.  Code, schedule and target model remain
        # fingerprinted; changing activation is independently protected by
        # the irreversible-action guard on ``ir.cron`` writes.
        ("code", "interval_number", "interval_type", "model_id"),
    ),
)
actions.extend(
    database_action_records(
        "ir.actions.server",
        "server_action",
        "server_action",
        ("binding_model_id", "child_ids", "code", "model_id", "state"),
    ),
)
payload = {
    "schema": SCHEMA,
    "database": env.cr.dbname,
    "country_codes": sorted(
        {
            code.lower()
            for code in env["res.company"].sudo().search([]).country_id.mapped("code")
            if code
        },
    ),
    "modules": modules(),
    "actions": sorted(actions, key=lambda action: action["key"]),
}


def policy_digest(surface, policy):
    policy_payload = {
        key: value for key, value in policy.items() if key != "qualified_policy_digest"
    }
    return digest({"action_policy": policy_payload, "action_surface": surface})


def runtime_check(runtime_payload):
    from odoo.addons.usl_access_control.models import action_policy  # noqa: PLC0415

    surface = action_policy._read_json(action_policy._SURFACE_FILE)
    policy = action_policy._read_json(action_policy._POLICY_FILE)
    errors = []
    expected_modules = {
        module["name"]: normalized_module_version(module.get("version", ""))
        for module in surface.get("modules", [])
        if isinstance(module, dict) and isinstance(module.get("name"), str)
    }
    actual_modules = {
        module["name"]: normalized_module_version(module.get("version", ""))
        for module in runtime_payload["modules"]
    }
    for name in sorted(actual_modules.keys() - expected_modules.keys()):
        errors.append(f"Added installed module: {name}")
    for name in sorted(expected_modules.keys() - actual_modules.keys()):
        errors.append(f"Removed installed module: {name}")
    for name in sorted(expected_modules.keys() & actual_modules.keys()):
        if expected_modules[name] != actual_modules[name]:
            errors.append(
                f"Changed installed module identity: {name} "
                f"({expected_modules[name]!r} != {actual_modules[name]!r})",
            )

    runtime_kinds = {"cron", "route", "rpc", "server_action", "ui"}
    expected_actions = {
        action["key"]: action
        for action in surface.get("actions", [])
        if isinstance(action, dict) and action.get("kind") in runtime_kinds
    }
    actual_actions = {
        action["key"]: action
        for action in runtime_payload["actions"]
        if action.get("kind") in runtime_kinds
    }
    for key in sorted(actual_actions.keys() - expected_actions.keys()):
        errors.append(f"Added runtime action requires classification: {key}")
    for key in sorted(expected_actions.keys() - actual_actions.keys()):
        errors.append(f"Removed runtime action leaves stale review: {key}")
    for key in sorted(expected_actions.keys() & actual_actions.keys()):
        expected_digest = expected_actions[key].get(
            "runtime_digest",
            expected_actions[key].get("digest"),
        )
        if expected_digest != actual_actions[key].get("digest"):
            errors.append(f"Changed runtime action requires review: {key}")
    expected_digest = policy_digest(surface, policy)
    if policy.get("qualified_policy_digest") not in {None, expected_digest}:
        errors.append(
            "The qualified policy digest does not match its runtime artifacts.",
        )
    if errors:
        raise RuntimeError(
            "Action-risk runtime inventory failed:\n- " + "\n- ".join(errors),
        )
    print(
        "Action-risk runtime inventory: PASS "
        f"({len(actual_modules)} modules, {len(actual_actions)} runtime actions, "
        f"policy {expected_digest})",
    )


if os.environ.get("ACTION_RISK_MODE", "discover") == "check":
    runtime_check(payload)
else:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
