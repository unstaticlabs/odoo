#!/usr/bin/env python3
"""Discover and validate the delivered Odoo action-risk inventory.

The tracked surface is intentionally separate from its reviewed policy.  A
surface refresh can never classify an action: the policy must name every exact
action key and bind its review to that action's normalized digest.
"""

# This operator CLI intentionally prints concise pass/fail output.
# ruff: noqa: EM101, T201, TC003

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SURFACE = (
    ROOT / "custom-addons" / "usl_access_control" / "policy" / "action_surface.json"
)
DEFAULT_POLICY = (
    ROOT / "custom-addons" / "usl_access_control" / "policy" / "action_policy.json"
)
DEFAULT_RUNTIME_POLICY = (
    ROOT
    / "custom-addons"
    / "usl_access_control"
    / "policy"
    / "protected_runtime_policy.json"
)
SURFACE_SCHEMA = "usl-action-risk-surface-v1"
POLICY_SCHEMA = "usl-action-risk-policy-v1"
RUNTIME_SCHEMA = "usl-action-risk-runtime-v1"
RUNTIME_POLICY_SCHEMA = "usl-action-risk-protected-runtime-v2"
MAX_RUNTIME_POLICY_BYTES = 512 * 1024
CLASSIFICATIONS = frozenset(
    {
        "operational",
        "protected",
        "read_only",
        "recoverable",
        "system_internal",
        "transport",
    },
)
ACTION_PREFIXES = (
    "rpc:",
    "route:",
    "ui:",
    "cron:",
    "server_action:",
    "client:",
    "sink:",
    "guard:",
)
PRODUCT_MODULES = frozenset(
    {
        "rebuild_account_migration",
        "usl_access_control",
        "usl_accounting",
        "usl_b2c",
        "usl_documents",
        "usl_documents_accounting",
        "usl_documents_b2c",
        "usl_expense_batch",
        "usl_home",
        "usl_locale",
        "usl_platform_billing",
        "usl_platform_billing_pocketid",
        "usl_pocketid",
        "usl_project",
        "usl_sign",
        "usl_tese_accounting",
        "usl_tese_payroll",
    },
)
ADDON_ROOTS = (
    ("custom", "custom-addons"),
    ("oca", "oca-addons"),
    ("native", "addons"),
    ("core", "odoo/addons"),
)
MUTATING_SINKS = frozenset(
    {
        "external_call",
        "filesystem_delete",
        "message_send",
        "orm_create",
        "orm_unlink",
        "orm_write",
        "raw_sql_mutation",
    },
)
MANDATORY_PROTECTED_FLAGS = frozenset(
    {
        "authorization_change",
        "external_deletion",
        "external_registration",
        "lock_change",
        "module_lifecycle",
        "permanent_deletion",
    },
)
SQL_MUTATION = re.compile(
    r"^\s*(?:WITH\b[\s\S]*?\b)?(ALTER|CREATE|DELETE|DROP|INSERT|MERGE|REPLACE|TRUNCATE|UPDATE)\b",
    re.IGNORECASE,
)
JS_RPC_CALL = re.compile(
    r"(?P<call>(?:this\.)?(?:orm\.)?call)\(\s*['\"](?P<model>[^'\"]+)['\"]\s*,\s*['\"](?P<method>[^'\"]+)['\"]",
)
JS_ROUTE_CALL = re.compile(
    r"(?P<call>(?:this\.)?(?:rpc|jsonrpc))\(\s*['\"](?P<route>/[^'\"]+)['\"]",
)
JS_METHOD = re.compile(
    r"^\s*(?:async\s+)?(?P<name>[A-Za-z_$][\w$]*)\s*\([^;]*\)\s*\{\s*$",
)
GUARD_KEY = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
NON_ORM_WRITE_RECEIVERS = frozenset(
    {
        "buffer",
        "csv_writer",
        "file",
        "output",
        "response",
        "stream",
        "workbook",
        "worksheet",
        "writer",
        "zip_file",
    },
)


class InventoryError(RuntimeError):
    """The action inventory cannot be discovered, loaded, or validated."""


class DuplicateKeyError(InventoryError):
    """A JSON object contains a duplicate key."""


@dataclass(frozen=True)
class ModuleInfo:
    """A locally available Odoo add-on."""

    name: str
    origin: str
    path: Path
    manifest: Mapping[str, Any]
    manifest_sha256: str


@dataclass(frozen=True)
class MethodContribution:
    """One Python implementation contributing to an Odoo model method."""

    module: str
    models: tuple[str, ...]
    parents: tuple[str, ...]
    method: str
    private: bool
    source: str
    line: int
    fragment: str
    delegates: tuple[str, ...]
    sinks: tuple[str, ...]
    guards: tuple[str, ...]
    risk_flags: tuple[str, ...]


def canonical_json(value: object) -> str:
    """Return the one canonical representation used by every policy consumer."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def stable_ast_dump(value: object) -> str:
    """Serialize semantic AST content without version-specific empty fields."""

    if isinstance(value, ast.AST):
        fields = []
        for name in value._fields:
            child = getattr(value, name, None)
            if child is None or child == []:
                continue
            fields.append(f"{name}={stable_ast_dump(child)}")
        return f"{type(value).__name__}({', '.join(fields)})"
    if isinstance(value, list):
        return f"[{', '.join(stable_ast_dump(item) for item in value)}]"
    return repr(value)


def qualified_policy_digest(
    surface: Mapping[str, object],
    policy: Mapping[str, object],
) -> str:
    """Hash the complete surface and policy with the policy's self-digest removed."""

    action_policy = copy.deepcopy(dict(policy))
    action_policy.pop("qualified_policy_digest", None)
    return sha256_json(
        {
            "action_policy": action_policy,
            "action_surface": dict(surface),
        },
    )


def runtime_policy_digest(runtime_policy: Mapping[str, object]) -> str:
    """Hash the compact enforcement artifact without its self-digest."""

    payload = copy.deepcopy(dict(runtime_policy))
    payload.pop("runtime_policy_sha256", None)
    return sha256_json(payload)


def build_runtime_policy(
    surface: Mapping[str, object],
    policy: Mapping[str, object],
) -> dict[str, object]:
    """Compile only protected runtime enforcement facts from the full review."""

    failures: list[str] = []
    reviewed = normalize_policy_actions(policy, failures)
    if failures:
        raise InventoryError("Cannot compile runtime policy: " + "; ".join(failures))
    actions = []
    server_actions = []
    for action_key, entry in sorted(reviewed.items()):
        if action_key.startswith("server_action:"):
            server_actions.append(
                {
                    "action_key": action_key,
                    "classification": entry.get("classification"),
                },
            )
        if entry.get("classification") != "protected":
            continue
        runtime_entry: dict[str, object] = {
            "action_key": action_key,
            "classification": "protected",
        }
        action_name = entry.get("action_name", entry.get("label"))
        if action_name is not None:
            runtime_entry["action_name"] = action_name
        enforcement = entry.get("enforcement")
        if enforcement is not None:
            runtime_entry["enforcement"] = enforcement
        actions.append(runtime_entry)
    result: dict[str, object] = {
        "actions": actions,
        "qualified_policy_digest": qualified_policy_digest(surface, policy),
        "schema": RUNTIME_POLICY_SCHEMA,
        "server_actions": server_actions,
    }
    result["runtime_policy_sha256"] = runtime_policy_digest(result)
    return result


def validate_runtime_policy(
    surface: Mapping[str, object],
    policy: Mapping[str, object],
    runtime_policy: Mapping[str, object],
) -> list[str]:
    """Prove the compact runtime artifact is the exact full-policy derivative."""

    errors: list[str] = []
    runtime_size = len(canonical_json(runtime_policy).encode())
    if runtime_size > MAX_RUNTIME_POLICY_BYTES:
        errors.append(
            "Protected runtime policy exceeds the 512 KiB worker-load budget: "
            f"{runtime_size} bytes.",
        )
    if runtime_policy.get("schema") != RUNTIME_POLICY_SCHEMA:
        errors.append(f"Runtime policy schema must be {RUNTIME_POLICY_SCHEMA}.")
    recorded_digest = runtime_policy.get("runtime_policy_sha256")
    computed_digest = runtime_policy_digest(runtime_policy)
    if recorded_digest != computed_digest:
        errors.append(
            "Runtime policy digest mismatch: "
            f"recorded {recorded_digest!r}, computed {computed_digest}.",
        )
    try:
        expected = build_runtime_policy(surface, policy)
    except InventoryError as error:
        errors.append(str(error))
        return errors
    if runtime_policy != expected:
        errors.append(
            "Protected runtime policy is stale or was not compiled from the exact "
            "reviewed surface and policy.",
        )
    return errors


def surface_digest(surface: Mapping[str, object]) -> str:
    payload = copy.deepcopy(dict(surface))
    payload.pop("surface_sha256", None)
    return sha256_json(payload)


def _no_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_no_duplicate_object,
        )
    except (OSError, json.JSONDecodeError) as error:
        raise InventoryError(f"Cannot load {path}: {error}") from error
    if not isinstance(value, dict):
        raise InventoryError(f"{path} must contain one JSON object.")
    return value


def write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _manifest(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    try:
        value = ast.literal_eval(raw.decode())
    except (SyntaxError, ValueError, UnicodeDecodeError) as error:
        raise InventoryError(f"Cannot parse manifest {path}: {error}") from error
    if not isinstance(value, dict):
        raise InventoryError(f"Manifest {path} is not a dictionary.")
    return value, hashlib.sha256(raw).hexdigest()


def module_index(root: Path = ROOT) -> dict[str, ModuleInfo]:
    """Index add-ons in runtime precedence order."""

    result: dict[str, ModuleInfo] = {}
    for origin, relative_root in ADDON_ROOTS:
        addons = root / relative_root
        if not addons.is_dir():
            continue
        for path in sorted(addons.glob("*/__manifest__.py")):
            name = path.parent.name
            if name in result:
                continue
            manifest, digest = _manifest(path)
            result[name] = ModuleInfo(
                name=name,
                origin=origin,
                path=path.parent,
                manifest=manifest,
                manifest_sha256=digest,
            )
    return result


def dependency_closure(
    modules: Mapping[str, ModuleInfo],
    roots: Iterable[str],
) -> list[str]:
    pending = list(roots)
    result: set[str] = set()
    missing: set[str] = set()
    while pending:
        name = pending.pop()
        if name in result:
            continue
        result.add(name)
        info = modules.get(name)
        if info is None:
            missing.add(name)
            continue
        dependencies = info.manifest.get("depends", [])
        if not isinstance(dependencies, list) or not all(
            isinstance(item, str) for item in dependencies
        ):
            raise InventoryError(f"Module {name} has an invalid dependency list.")
        pending.extend(dependencies)
    if missing:
        raise InventoryError(
            "Missing installed module sources: " + ", ".join(sorted(missing)),
        )
    return sorted(result)


def installed_module_fixed_point(
    modules: Mapping[str, ModuleInfo],
    roots: Iterable[str],
    *,
    country_codes: Iterable[str] = ("fr",),
) -> list[str]:
    """Resolve dependencies and Odoo's boolean/list ``auto_install`` rules."""

    installed = set(dependency_closure(modules, roots))
    normalized_countries = {code.lower() for code in country_codes}
    while True:
        additions: set[str] = set()
        for name, info in modules.items():
            if name in installed or info.manifest.get("installable", True) is False:
                continue
            auto_install = info.manifest.get("auto_install", False)
            dependencies = info.manifest.get("depends", [])
            module_countries = {
                code.lower()
                for code in info.manifest.get("countries", [])
                if isinstance(code, str)
            }
            if module_countries and not module_countries & normalized_countries:
                continue
            if not isinstance(dependencies, list) or not all(
                isinstance(item, str) for item in dependencies
            ):
                raise InventoryError(f"Module {name} has an invalid dependency list.")
            if auto_install is True:
                triggers = set(dependencies)
            elif isinstance(auto_install, (list, tuple, set)):
                triggers = set(auto_install)
                if not triggers <= set(dependencies):
                    raise InventoryError(
                        f"Module {name} has auto_install triggers outside its dependencies.",
                    )
            else:
                continue
            if triggers <= installed:
                additions.update(dependency_closure(modules, [name]))
        additions -= installed
        if not additions:
            return sorted(installed)
        installed.update(additions)


def _logical_source(info: ModuleInfo, path: Path) -> str:
    return f"{dict(ADDON_ROOTS)[info.origin]}/{info.name}/{path.relative_to(info.path).as_posix()}"


def _source_files(info: ModuleInfo, suffix: str) -> list[Path]:
    ignored_parts = {"__pycache__", "i18n", "migrations", "tests"}
    return [
        path
        for path in sorted(info.path.rglob(f"*{suffix}"))
        if path.is_file()
        and not ignored_parts.intersection(path.relative_to(info.path).parts)
    ]


def _module_source_digest(info: ModuleInfo) -> str:
    digest = hashlib.sha256()
    included_suffixes = {".csv", ".js", ".json", ".py", ".scss", ".xml"}
    ignored_parts = {"__pycache__", "i18n", "migrations", "tests"}
    for path in sorted(info.path.rglob("*")):
        relative = path.relative_to(info.path)
        if (
            not path.is_file()
            or path.suffix not in included_suffixes
            or ignored_parts.intersection(relative.parts)
            or "static/lib" in relative.as_posix()
            or relative.as_posix()
            in {
                "policy/action_policy.json",
                "policy/action_surface.json",
                "policy/protected_runtime_policy.json",
            }
        ):
            continue
        digest.update(relative.as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _literal_strings(node: ast.AST | None) -> tuple[str, ...]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values: list[str] = []
        for item in node.elts:
            literals = _literal_strings(item)
            if not literals:
                return ()
            values.extend(literals)
        return tuple(values)
    return ()


def _assignment(class_node: ast.ClassDef, name: str) -> tuple[str, ...]:
    for statement in class_node.body:
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
            )
            if any(
                isinstance(target, ast.Name) and target.id == name for target in targets
            ):
                return _literal_strings(statement.value)
    return ()


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _dotted_name(node.func)
    if isinstance(node, ast.Subscript):
        return _dotted_name(node.value)
    return ""


def _is_model_class(node: ast.ClassDef) -> bool:
    return any(
        _dotted_name(base).endswith(("Model", "AbstractModel", "TransientModel"))
        for base in node.bases
    ) or bool(_assignment(node, "_name") or _assignment(node, "_inherit"))


def _is_controller_class(node: ast.ClassDef) -> bool:
    return any(_dotted_name(base).endswith("Controller") for base in node.bases)


def _decorated_private(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        _dotted_name(decorator).endswith(".private")
        for decorator in node.decorator_list
    )


def _constant_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class _MethodAnalysis(ast.NodeVisitor):
    def __init__(self, model: str | None, method: str) -> None:
        self.model = model
        self.method = method
        self.delegates: set[str] = set()
        self.sinks: list[str] = []
        self.guards: set[str] = set()
        self.private_calls: set[str] = set()
        self.risk_flags: set[str] = set()
        self.nonliteral_guards = 0

    def _add_sink(self, kind: str) -> None:
        self.sinks.append(kind)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        dotted = _dotted_name(node.func)
        name = dotted.rsplit(".", 1)[-1]

        if name == "_usl_require_irreversible_action":
            guard = _constant_string(node.args[0]) if node.args else None
            if guard and GUARD_KEY.fullmatch(guard):
                self.guards.add(f"guard:{guard}")
                if guard.startswith("einvoice.") and any(
                    marker in guard
                    for marker in ("deregister", "disconnect", "unregister")
                ):
                    self.risk_flags.add("external_deletion")
                if (
                    guard.startswith("einvoice.")
                    and any(
                        marker in guard
                        for marker in ("register", "reconnect", "reregister")
                    )
                    and not any(
                        marker in guard
                        for marker in ("deregister", "disconnect", "unregister")
                    )
                ):
                    self.risk_flags.add("external_registration")
                if guard.startswith("documents.permanent"):
                    self.risk_flags.add("permanent_deletion")
            elif not any(
                keyword.arg == "exact_policy_key"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in node.keywords
            ):
                self.nonliteral_guards += 1

        if isinstance(node.func, ast.Attribute):
            value = node.func.value
            if isinstance(value, ast.Name) and value.id == "self":
                if node.func.attr.startswith("_"):
                    self.private_calls.add(node.func.attr)
                elif self.model:
                    self.delegates.add(f"rpc:{self.model}.{node.func.attr}")
            if isinstance(value, ast.Subscript) and _dotted_name(value.value).endswith(
                "env",
            ):
                target_model = _constant_string(value.slice)
                if target_model and not node.func.attr.startswith("_"):
                    self.delegates.add(f"rpc:{target_model}.{node.func.attr}")
            if name in {
                "button_immediate_install",
                "button_immediate_upgrade",
                "button_immediate_uninstall",
            } and not (self.model == "ir.module.module" and self.method == name):
                self.delegates.add(f"rpc:ir.module.module.{name}")

        receiver = (
            _dotted_name(node.func.value)
            if isinstance(node.func, ast.Attribute)
            else ""
        )
        if name == "sudo":
            self._add_sink("sudo")
        elif name == "create":
            self._add_sink("orm_create")
        elif name == "write" and receiver.rsplit(".", 1)[-1] not in (
            NON_ORM_WRITE_RECEIVERS
        ):
            self._add_sink("orm_write")
        elif name == "unlink" and (
            receiver in {"self", "super"}
            or receiver.endswith(("record", "records", "recordset"))
            or ".env" in receiver
        ):
            self._add_sink("orm_unlink")
        elif name in {"message_post", "send", "send_mail", "_send"}:
            self._add_sink("message_send")
        elif dotted in {
            "os.remove",
            "os.removedirs",
            "os.unlink",
            "shutil.rmtree",
        } or (receiver in {"Path", "pathlib.Path"} and name == "unlink"):
            self._add_sink("filesystem_delete")

        if name in {"execute", "_execute_query"} and node.args:
            sql = _constant_string(node.args[0])
            if sql and SQL_MUTATION.search(sql):
                self._add_sink("raw_sql_mutation")

        if name.startswith("_") and any(
            marker in name.lower()
            for marker in (
                "_add",
                "_create",
                "_delete",
                "_deregister",
                "_post",
                "_register",
                "_remove",
                "_send",
                "_set",
                "_sync",
                "_unlink",
                "_unsubscribe",
                "_update",
                "_write",
            )
        ):
            self._add_sink("orm_write")

        is_external_call = (
            dotted.startswith(("requests.", "httpx.", "urllib.request."))
            or name in {"jsonrpc", "urlopen"}
            or (
                "peppol" in dotted.lower()
                and name in {"request", "send", "register", "deregister", "unregister"}
            )
        )
        if is_external_call:
            self._add_sink("external_call")

        lowered = f"{self.method} {dotted}".lower()
        if (self.model == "ir.actions.server" and self.method == "run") or (
            self.model == "ir.cron" and self.method == "method_direct_trigger"
        ):
            self.risk_flags.add("arbitrary_execution")
        if is_external_call:
            provider_context = f"{self.model or ''} {lowered}"
            if any(marker in provider_context for marker in ("pdp", "peppol")):
                if name == "delete" or any(
                    term in lowered
                    for term in ("deregister", "disconnect", "unregister")
                ):
                    self.risk_flags.add("external_deletion")
                if any(
                    term in lowered for term in ("register", "reregister", "reconnect")
                ) and not any(
                    term in lowered
                    for term in ("deregister", "disconnect", "unregister")
                ):
                    self.risk_flags.add("external_registration")

        self.generic_visit(node)


def _method_analysis(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    model: str | None,
) -> _MethodAnalysis:
    visitor = _MethodAnalysis(model, node.name)
    visitor.visit(node)
    if "permanently_delete" in node.name.lower():
        visitor.risk_flags.add("permanent_deletion")
    lowered_name = node.name.lower()
    moves_lock = "orm_write" in visitor.sinks and any(
        marker in lowered_name
        for marker in ("fiscal_lock", "hard_lock", "lock_date", "tax_lock")
    )
    if moves_lock or (
        model == "b2c.accounting.session" and lowered_name == "action_unlock"
    ):
        visitor.risk_flags.add("lock_change")
    if model == "ir.module.module" and node.name in {
        "button_install",
        "button_immediate_install",
        "button_immediate_install_app",
        "button_upgrade",
        "button_immediate_upgrade",
        "button_uninstall",
        "button_immediate_uninstall",
        "module_uninstall",
    }:
        visitor.risk_flags.add("module_lifecycle")
    return visitor


def _expanded_method_analysis(
    method_name: str,
    analyses: Mapping[str, _MethodAnalysis],
    seen: frozenset[str] = frozenset(),
) -> _MethodAnalysis:
    """Include same-class private helper effects in an exposed method."""

    direct = analyses[method_name]
    expanded = _MethodAnalysis(direct.model, direct.method)
    expanded.delegates.update(direct.delegates)
    expanded.sinks.extend(direct.sinks)
    expanded.guards.update(direct.guards)
    expanded.private_calls.update(direct.private_calls)
    expanded.risk_flags.update(direct.risk_flags)
    expanded.nonliteral_guards = direct.nonliteral_guards
    for private_name in sorted(direct.private_calls):
        if private_name in seen or private_name not in analyses:
            continue
        nested = _expanded_method_analysis(
            private_name,
            analyses,
            seen | {method_name},
        )
        expanded.delegates.update(nested.delegates)
        expanded.sinks.extend(nested.sinks)
        expanded.guards.update(nested.guards)
        expanded.risk_flags.update(nested.risk_flags)
        expanded.nonliteral_guards += nested.nonliteral_guards
    return expanded


def _route_values(
    decorator: ast.AST,
) -> tuple[tuple[str, ...], str, tuple[str, ...], str] | None:
    if not isinstance(decorator, ast.Call) or not _dotted_name(decorator.func).endswith(
        ".route",
    ):
        return None
    paths = _literal_strings(decorator.args[0]) if decorator.args else ()
    route_type = "http"
    methods: tuple[str, ...] = ()
    auth = "user"
    for keyword in decorator.keywords:
        if keyword.arg == "type":
            route_type = _constant_string(keyword.value) or "dynamic"
        elif keyword.arg == "methods":
            methods = _literal_strings(keyword.value)
        elif keyword.arg == "auth":
            auth = _constant_string(keyword.value) or "dynamic"
    return paths, route_type, tuple(sorted(methods)), auth


def _route_key(path: str, route_type: str, methods: Sequence[str]) -> str:
    method_text = ",".join(methods) if methods else "ANY"
    return f"route:{route_type}:{method_text}:{path}"


class _Accumulator:
    def __init__(self) -> None:
        self._actions: dict[str, dict[str, Any]] = {}

    def add(self, action: Mapping[str, Any], fragment: str) -> None:
        key = str(action["key"])
        current = self._actions.setdefault(
            key,
            {
                "key": key,
                "kind": action["kind"],
                "modules": [],
                "sources": [],
                "delegates": [],
                "sinks": [],
                "guards": [],
                "risk_flags": [],
                "parents": [],
                "_fragments": [],
            },
        )
        if current["kind"] != action["kind"]:
            raise InventoryError(f"Action {key} has conflicting kinds.")
        for scalar in ("model", "method", "route", "xmlid", "handler", "sink_kind"):
            if scalar in action:
                old = current.get(scalar)
                if old is not None and old != action[scalar]:
                    raise InventoryError(
                        f"Action {key} has conflicting {scalar} values.",
                    )
                current[scalar] = action[scalar]
        for plural in (
            "modules",
            "sources",
            "delegates",
            "sinks",
            "guards",
            "risk_flags",
            "parents",
        ):
            value = action.get(plural, [])
            current[plural].extend(value)
        current["_fragments"].append(fragment)

    def finish(self) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        for key in sorted(self._actions):
            action = self._actions[key]
            for plural in (
                "modules",
                "delegates",
                "sinks",
                "guards",
                "risk_flags",
                "parents",
            ):
                action[plural] = sorted(set(action[plural]))
            action["sources"] = sorted(
                {
                    canonical_json(source): source for source in action["sources"]
                }.values(),
                key=lambda value: (value["path"], value.get("line", 0)),
            )
            action["digest"] = sha256_json(sorted(action.pop("_fragments")))
            for plural in ("delegates", "sinks", "guards", "risk_flags", "parents"):
                if not action[plural]:
                    action.pop(plural)
            actions.append(action)
        return actions


def _python_contributions(
    info: ModuleInfo,
    accumulator: _Accumulator,
    diagnostics: list[str],
) -> tuple[list[MethodContribution], dict[str, set[str]]]:
    contributions: list[MethodContribution] = []
    parents_by_model: dict[str, set[str]] = defaultdict(set)
    for path in _source_files(info, ".py"):
        source = _logical_source(info, path)
        try:
            tree = ast.parse(path.read_bytes(), filename=source)
        except (SyntaxError, UnicodeDecodeError) as error:
            diagnostics.append(f"{source}: cannot parse Python: {error}")
            continue

        for class_node in (
            node for node in tree.body if isinstance(node, ast.ClassDef)
        ):
            if _is_controller_class(class_node):
                controller_methods = [
                    node
                    for node in class_node.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                controller_analyses = {
                    method_node.name: _method_analysis(method_node, None)
                    for method_node in controller_methods
                }
                for method_node in controller_methods:
                    analysis = _expanded_method_analysis(
                        method_node.name,
                        controller_analyses,
                    )
                    for decorator in method_node.decorator_list:
                        route = _route_values(decorator)
                        if route is None:
                            continue
                        paths, route_type, methods, auth = route
                        for route_path in paths:
                            key = _route_key(route_path, route_type, methods)
                            accumulator.add(
                                {
                                    "key": key,
                                    "kind": "route",
                                    "route": route_path,
                                    "modules": [info.name],
                                    "sources": [
                                        {"path": source, "line": method_node.lineno},
                                    ],
                                    "delegates": sorted(analysis.delegates),
                                    "sinks": sorted(set(analysis.sinks)),
                                    "guards": sorted(analysis.guards),
                                    "risk_flags": sorted(analysis.risk_flags),
                                },
                                canonical_json(
                                    {
                                        "auth": auth,
                                        "method": stable_ast_dump(method_node),
                                        "methods": methods,
                                        "route": route_path,
                                        "type": route_type,
                                    },
                                ),
                            )

            if not _is_model_class(class_node):
                continue
            names = _assignment(class_node, "_name")
            inherits = _assignment(class_node, "_inherit")
            models = names or inherits
            if not models:
                continue
            parents = tuple(parent for parent in inherits if parent not in models)
            for model in models:
                parents_by_model[model].update(parents)
            method_nodes = [
                node
                for node in class_node.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            for model in models:
                analyses = {
                    method_node.name: _method_analysis(method_node, model)
                    for method_node in method_nodes
                }
                for method_node in method_nodes:
                    direct_analysis = analyses[method_node.name]
                    analysis = (
                        direct_analysis
                        if method_node.name.startswith("_")
                        else _expanded_method_analysis(method_node.name, analyses)
                    )
                    if analysis.nonliteral_guards:
                        diagnostics.append(
                            f"{source}:{method_node.lineno}: {model}.{method_node.name} "
                            "uses a non-literal irreversible-action guard",
                        )
                    _add_method_internal_actions(
                        accumulator,
                        info.name,
                        source,
                        method_node,
                        model,
                        direct_analysis,
                    )
                    if not method_node.name.startswith("_"):
                        contributions.append(
                            MethodContribution(
                                module=info.name,
                                models=(model,),
                                parents=parents,
                                method=method_node.name,
                                private=_decorated_private(method_node),
                                source=source,
                                line=method_node.lineno,
                                fragment=stable_ast_dump(method_node),
                                delegates=tuple(sorted(analysis.delegates)),
                                sinks=tuple(sorted(set(analysis.sinks))),
                                guards=tuple(sorted(analysis.guards)),
                                risk_flags=tuple(sorted(analysis.risk_flags)),
                            ),
                        )

        _add_helper_sinks(info, source, tree, accumulator)
    return contributions, parents_by_model


def _add_method_internal_actions(
    accumulator: _Accumulator,
    module: str,
    source: str,
    method_node: ast.FunctionDef | ast.AsyncFunctionDef,
    model: str,
    analysis: _MethodAnalysis,
) -> None:
    parents = (
        [] if method_node.name.startswith("_") else [f"rpc:{model}.{method_node.name}"]
    )
    for guard in sorted(analysis.guards):
        accumulator.add(
            {
                "key": guard,
                "kind": "guard",
                "modules": [module],
                "sources": [{"path": source, "line": method_node.lineno}],
                "parents": parents,
                "risk_flags": sorted(analysis.risk_flags),
            },
            stable_ast_dump(method_node),
        )
    counts: dict[str, int] = defaultdict(int)
    for sink in analysis.sinks:
        counts[sink] += 1
        key = f"sink:{module}:{source}:{model}.{method_node.name}:{sink}:{counts[sink]}"
        accumulator.add(
            {
                "key": key,
                "kind": "sink",
                "sink_kind": sink,
                "modules": [module],
                "sources": [{"path": source, "line": method_node.lineno}],
                "parents": parents,
                "guards": sorted(analysis.guards),
                "risk_flags": sorted(analysis.risk_flags),
            },
            f"{stable_ast_dump(method_node)}:{sink}:{counts[sink]}",
        )


def _add_helper_sinks(
    info: ModuleInfo,
    source: str,
    tree: ast.Module,
    accumulator: _Accumulator,
) -> None:
    model_method_lines = {
        node.lineno
        for class_node in tree.body
        if isinstance(class_node, ast.ClassDef) and _is_model_class(class_node)
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    functions: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append((node.name, node))
        elif isinstance(node, ast.ClassDef) and not _is_model_class(node):
            functions.extend(
                (f"{node.name}.{method.name}", method)
                for method in node.body
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
    for qualname, node in functions:
        if node.lineno in model_method_lines:
            continue
        analysis = _method_analysis(node, None)
        counts: dict[str, int] = defaultdict(int)
        for sink in analysis.sinks:
            counts[sink] += 1
            key = f"sink:{info.name}:{source}:{qualname}:{sink}:{counts[sink]}"
            accumulator.add(
                {
                    "key": key,
                    "kind": "sink",
                    "sink_kind": sink,
                    "modules": [info.name],
                    "sources": [{"path": source, "line": node.lineno}],
                    "risk_flags": sorted(analysis.risk_flags),
                },
                f"{stable_ast_dump(node)}:{sink}:{counts[sink]}",
            )


def _framework_methods(
    root: Path, accumulator: _Accumulator,
) -> list[MethodContribution]:
    path = root / "odoo" / "orm" / "models.py"
    if not path.is_file():
        return []
    tree = ast.parse(path.read_bytes(), filename="odoo/orm/models.py")
    result: list[MethodContribution] = []
    for class_node in tree.body:
        if not isinstance(class_node, ast.ClassDef) or class_node.name != "BaseModel":
            continue
        for method_node in class_node.body:
            if not isinstance(method_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            analysis = _method_analysis(method_node, "base")
            _add_method_internal_actions(
                accumulator,
                "base",
                "odoo/orm/models.py",
                method_node,
                "base",
                analysis,
            )
            if method_node.name.startswith("_"):
                continue
            result.append(
                MethodContribution(
                    module="base",
                    models=("base",),
                    parents=(),
                    method=method_node.name,
                    private=_decorated_private(method_node),
                    source="odoo/orm/models.py",
                    line=method_node.lineno,
                    fragment=stable_ast_dump(method_node),
                    delegates=(),
                    sinks=tuple(sorted(set(analysis.sinks))),
                    guards=(),
                    risk_flags=tuple(sorted(analysis.risk_flags)),
                ),
            )
    return result


def _effective_rpc_actions(
    contributions: Sequence[MethodContribution],
    parents_by_model: Mapping[str, set[str]],
    accumulator: _Accumulator,
) -> None:
    by_model: dict[str, list[MethodContribution]] = defaultdict(list)
    for contribution in contributions:
        for model in contribution.models:
            by_model[model].append(contribution)
    models = sorted(set(by_model) | set(parents_by_model))

    def ancestry(model: str, seen: frozenset[str] = frozenset()) -> set[str]:
        if model in seen:
            return set()
        result = {model}
        for parent in parents_by_model.get(model, set()):
            result.update(ancestry(parent, seen | {model}))
        return result

    framework = [
        contribution
        for contribution in contributions
        if contribution.models == ("base",)
    ]
    for model in models:
        chain_models = ancestry(model)
        effective = [
            contribution
            for chain_model in sorted(chain_models)
            for contribution in by_model.get(chain_model, [])
        ]
        if model != "base":
            effective.extend(framework)
        by_method: dict[str, list[MethodContribution]] = defaultdict(list)
        for contribution in effective:
            by_method[contribution.method].append(contribution)
        for method, implementations in sorted(by_method.items()):
            if any(item.private for item in implementations):
                continue
            key = f"rpc:{model}.{method}"
            for implementation in implementations:
                delegates = [
                    value.replace("rpc:base.", f"rpc:{model}.", 1)
                    if value.startswith("rpc:base.")
                    else value
                    for value in implementation.delegates
                ]
                accumulator.add(
                    {
                        "key": key,
                        "kind": "rpc",
                        "model": model,
                        "method": method,
                        "modules": [implementation.module],
                        "sources": [
                            {
                                "path": implementation.source,
                                "line": implementation.line,
                            },
                        ],
                        "delegates": delegates,
                        "sinks": list(implementation.sinks),
                        "guards": list(implementation.guards),
                        "risk_flags": list(implementation.risk_flags),
                    },
                    implementation.fragment,
                )


def _xml_text(element: ET.Element) -> str:
    clone = copy.deepcopy(element)
    for item in clone.iter():
        if item.text:
            item.text = " ".join(item.text.split())
        if item.tail:
            item.tail = " ".join(item.tail.split())
        item.attrib.update(sorted(item.attrib.items()))
    return ET.tostring(clone, encoding="unicode")


def _field_text(record: ET.Element, name: str) -> str | None:
    for field in record.findall(f"./field[@name='{name}']"):
        return field.get("ref") or (field.text or "").strip() or None
    return None


def _xml_actions(
    info: ModuleInfo,
    accumulator: _Accumulator,
    diagnostics: list[str],
) -> None:
    for path in _source_files(info, ".xml"):
        source = _logical_source(info, path)
        try:
            if not path.read_bytes().strip():
                continue
            tree = ET.parse(path)
        except ET.ParseError as error:
            diagnostics.append(f"{source}: cannot parse XML: {error}")
            continue
        root = tree.getroot()
        for record in root.iter("record"):
            xmlid = record.get("id")
            record_model = record.get("model", "")
            if not xmlid:
                continue
            full_xmlid = xmlid if "." in xmlid else f"{info.name}.{xmlid}"
            if record_model == "ir.cron":
                key = f"cron:{full_xmlid}"
                code = _field_text(record, "code") or ""
                model_ref = _field_text(record, "model_id") or ""
                delegates = _python_code_targets(code, model_ref)
                accumulator.add(
                    {
                        "key": key,
                        "kind": "cron",
                        "xmlid": full_xmlid,
                        "modules": [info.name],
                        "sources": [{"path": source}],
                        "delegates": delegates,
                    },
                    _xml_text(record),
                )
            elif record_model in {"ir.actions.server", "base.automation"}:
                key = f"server_action:{full_xmlid}"
                code = _field_text(record, "code") or ""
                model_ref = _field_text(record, "model_id") or ""
                accumulator.add(
                    {
                        "key": key,
                        "kind": "server_action",
                        "xmlid": full_xmlid,
                        "modules": [info.name],
                        "sources": [{"path": source}],
                        "delegates": _python_code_targets(code, model_ref),
                        "guards": ["guard:automation.server_action.execute"],
                        "risk_flags": ["arbitrary_execution"] if code else [],
                    },
                    _xml_text(record),
                )
            elif record_model.startswith("ir.actions."):
                key = f"ui:{full_xmlid}"
                accumulator.add(
                    {
                        "key": key,
                        "kind": "ui",
                        "xmlid": full_xmlid,
                        "modules": [info.name],
                        "sources": [{"path": source}],
                    },
                    _xml_text(record),
                )

            if record_model != "ir.ui.view":
                continue
            view_model = _field_text(record, "model")
            button_counts: dict[tuple[str, str], int] = defaultdict(int)
            for button in record.iter("button"):
                button_type = button.get("type", "")
                name = button.get("name", "")
                if button_type not in {"action", "object"} or not name:
                    continue
                button_counts[button_type, name] += 1
                ordinal = button_counts[button_type, name]
                delegates: list[str] = []
                stable_name = name
                if button_type == "object" and view_model:
                    delegates.append(f"rpc:{view_model}.{name}")
                elif button_type == "action":
                    target = _xml_action_target(info.name, name)
                    if target:
                        stable_name = target.removeprefix("ui:")
                        delegates.append(target)
                key = (
                    f"ui:{full_xmlid}:button:{button_type}:{stable_name}:{ordinal}"
                )
                accumulator.add(
                    {
                        "key": key,
                        "kind": "ui",
                        "xmlid": full_xmlid,
                        "modules": [info.name],
                        "sources": [{"path": source}],
                        "delegates": delegates,
                    },
                    _xml_text(button),
                )


def _xml_action_target(module: str, value: str) -> str | None:
    match = re.fullmatch(r"%\(([^)]+)\)d", value)
    if match:
        xmlid = match.group(1)
        return f"ui:{xmlid if '.' in xmlid else f'{module}.{xmlid}'}"
    if re.fullmatch(r"[a-zA-Z0-9_.]+", value):
        return f"ui:{value if '.' in value else f'{module}.{value}'}"
    return None


def _python_code_targets(code: str, model_ref: str) -> list[str]:
    model = model_ref.removeprefix("model_").replace("_", ".") if model_ref else ""
    targets = []
    for match in re.finditer(r"(?:model|records?)\.([A-Za-z][A-Za-z0-9_]*)\s*\(", code):
        if model:
            targets.append(f"rpc:{model}.{match.group(1)}")
    return sorted(set(targets))


def _js_actions(info: ModuleInfo, accumulator: _Accumulator) -> None:
    for path in _source_files(info, ".js"):
        source = _logical_source(info, path)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        handler = "module"
        counts: dict[tuple[str, str], int] = defaultdict(int)
        for line_number, line in enumerate(lines, 1):
            if match := JS_METHOD.match(line):
                handler = match.group("name")
            for match in JS_RPC_CALL.finditer(line):
                target = f"rpc:{match.group('model')}.{match.group('method')}"
                counts[handler, target] += 1
                key = f"client:{info.name}:{source}:{handler}:rpc:{counts[handler, target]}"
                accumulator.add(
                    {
                        "key": key,
                        "kind": "client",
                        "handler": handler,
                        "modules": [info.name],
                        "sources": [{"path": source, "line": line_number}],
                        "delegates": [target],
                    },
                    line.strip(),
                )
            for match in JS_ROUTE_CALL.finditer(line):
                route = match.group("route")
                dataset = re.fullmatch(
                    r"/web/dataset/call_kw/(?P<model>[^/]+)/(?P<method>[^/]+)",
                    route,
                )
                target = (
                    f"rpc:{dataset.group('model')}.{dataset.group('method')}"
                    if dataset
                    else f"route:jsonrpc:ANY:{route}"
                )
                counts[handler, target] += 1
                key = f"client:{info.name}:{source}:{handler}:route:{counts[handler, target]}"
                accumulator.add(
                    {
                        "key": key,
                        "kind": "client",
                        "handler": handler,
                        "modules": [info.name],
                        "sources": [{"path": source, "line": line_number}],
                        "delegates": [target],
                    },
                    line.strip(),
                )


def _sink_parent_rewrite(actions: list[dict[str, Any]]) -> None:
    action_keys = {action["key"] for action in actions}
    routes: dict[str, list[str]] = defaultdict(list)
    xmlids: dict[str, list[str]] = defaultdict(list)
    for action in actions:
        if route := action.get("route"):
            routes[route].append(action["key"])
        if xmlid := action.get("xmlid"):
            xmlids[xmlid].append(action["key"])
    for action in actions:
        delegates = []
        for delegate in action.get("delegates", []):
            if delegate == action["key"]:
                continue
            candidates: list[str] = []
            if delegate.startswith("route:"):
                route = delegate.split(":", 3)[-1]
                candidates = routes.get(route, [])
            elif delegate.startswith("ui:"):
                candidates = xmlids.get(delegate.removeprefix("ui:"), [])
            delegates.append(candidates[0] if len(candidates) == 1 else delegate)
        delegates = sorted(set(delegates))
        if delegates:
            action["delegates"] = delegates
            unresolved = sorted(
                delegate for delegate in delegates if delegate not in action_keys
            )
            if unresolved:
                action["unresolved_delegates"] = unresolved
        else:
            action.pop("delegates", None)


def _merge_runtime(
    source_actions: list[dict[str, Any]],
    runtime: Mapping[str, object],
) -> list[dict[str, Any]]:
    if runtime.get("schema") != RUNTIME_SCHEMA:
        raise InventoryError(
            f"Runtime inventory schema must be {RUNTIME_SCHEMA!r}.",
        )
    raw_actions = runtime.get("actions")
    if not isinstance(raw_actions, list):
        raise InventoryError("Runtime inventory has no actions list.")
    runtime_actions: dict[str, dict[str, Any]] = {}
    for value in raw_actions:
        if not isinstance(value, dict) or not isinstance(value.get("key"), str):
            raise InventoryError("Runtime inventory contains an invalid action.")
        key = value["key"]
        if key in runtime_actions:
            raise InventoryError(f"Runtime inventory repeats action {key}.")
        runtime_actions[key] = copy.deepcopy(value)

    runtime_kinds = {"rpc", "route", "cron", "server_action", "ui"}
    merged = {
        action["key"]: action
        for action in source_actions
        if action["kind"] not in runtime_kinds
    }
    static_by_key = {action["key"]: action for action in source_actions}
    for key, runtime_action in runtime_actions.items():
        runtime_action["runtime_digest"] = runtime_action["digest"]
        if runtime_action.get("kind") == "server_action":
            runtime_action["guards"] = ["guard:automation.server_action.execute"]
        static = static_by_key.get(key)
        if static:
            for field in (
                "sinks",
                "guards",
                "risk_flags",
                "parents",
                "sources",
                "modules",
            ):
                runtime_action[field] = sorted(
                    {
                        canonical_json(item): item
                        for item in [
                            *static.get(field, []),
                            *runtime_action.get(field, []),
                        ]
                    }.values(),
                    key=canonical_json,
                )
            if runtime_action.get("kind") != "ui":
                runtime_action["delegates"] = sorted(
                    {
                        *static.get("delegates", []),
                        *runtime_action.get("delegates", []),
                    },
                )
            runtime_action["digest"] = sha256_json(
                [static["digest"], runtime_action["digest"]],
            )
        merged[key] = runtime_action
    return [merged[key] for key in sorted(merged)]


def discover_surface(
    *,
    root: Path = ROOT,
    root_modules: Iterable[str] = PRODUCT_MODULES,
    installed_modules: Iterable[str] | None = None,
    runtime: Mapping[str, object] | None = None,
    country_codes: Iterable[str] = ("fr",),
) -> dict[str, object]:
    """Discover a deterministic action surface from source and optional runtime facts."""

    modules = module_index(root)
    requested_roots = sorted(set(root_modules))
    if runtime is not None:
        runtime_modules = runtime.get("modules")
        if not isinstance(runtime_modules, list):
            raise InventoryError("Runtime inventory has no modules list.")
        requested = sorted(
            {
                str(module["name"])
                for module in runtime_modules
                if isinstance(module, dict) and isinstance(module.get("name"), str)
            },
        )
        runtime_countries = runtime.get("country_codes", [])
        if isinstance(runtime_countries, list) and all(
            isinstance(code, str) for code in runtime_countries
        ):
            country_codes = runtime_countries
    elif installed_modules is not None:
        requested = sorted(set(installed_modules))
    else:
        requested = installed_module_fixed_point(
            modules,
            requested_roots,
            country_codes=country_codes,
        )
    missing = sorted(set(requested) - set(modules))
    if missing:
        raise InventoryError("Missing module source: " + ", ".join(missing))

    module_entries = []
    accumulator = _Accumulator()
    diagnostics: list[str] = []
    contributions: list[MethodContribution] = _framework_methods(root, accumulator)
    parents_by_model: dict[str, set[str]] = defaultdict(set)
    for name in requested:
        info = modules[name]
        version = info.manifest.get("version")
        module_entries.append(
            {
                "name": name,
                "origin": info.origin,
                "version": version if isinstance(version, str) else "",
                "manifest_sha256": info.manifest_sha256,
                "source_sha256": _module_source_digest(info),
            },
        )
        python_items, module_parents = _python_contributions(
            info,
            accumulator,
            diagnostics,
        )
        contributions.extend(python_items)
        for model, parents in module_parents.items():
            parents_by_model[model].update(parents)
        _xml_actions(info, accumulator, diagnostics)
        _js_actions(info, accumulator)

    _effective_rpc_actions(contributions, parents_by_model, accumulator)
    actions = accumulator.finish()
    if runtime is not None:
        actions = _merge_runtime(actions, runtime)
    _sink_parent_rewrite(actions)
    module_set_sha256 = sha256_json(
        [
            {
                "manifest_sha256": module["manifest_sha256"],
                "name": module["name"],
                "source_sha256": module["source_sha256"],
                "version": module["version"],
            }
            for module in module_entries
        ],
    )
    surface: dict[str, object] = {
        "schema": SURFACE_SCHEMA,
        "root_modules": requested_roots,
        "module_set_sha256": module_set_sha256,
        "modules": module_entries,
        "actions": actions,
        "diagnostics": sorted(set(diagnostics)),
        "discovery": "runtime+source" if runtime is not None else "source",
        "country_codes": sorted({code.lower() for code in country_codes}),
    }
    surface["surface_sha256"] = surface_digest(surface)
    return surface


def _action_map(
    surface: Mapping[str, object], errors: list[str],
) -> dict[str, Mapping[str, object]]:
    raw_actions = surface.get("actions")
    if not isinstance(raw_actions, list):
        errors.append("Surface actions must be a list.")
        return {}
    result: dict[str, Mapping[str, object]] = {}
    for index, action in enumerate(raw_actions):
        if not isinstance(action, dict):
            errors.append(f"Surface action #{index} is not an object.")
            continue
        key = action.get("key")
        if not isinstance(key, str) or not key.startswith(ACTION_PREFIXES):
            errors.append(f"Surface action #{index} has invalid key {key!r}.")
            continue
        if key in result:
            errors.append(f"Surface repeats action key {key}.")
            continue
        if action.get("kind") != key.split(":", 1)[0]:
            errors.append(
                f"Surface action {key} has mismatched kind {action.get('kind')!r}.",
            )
        digest = action.get("digest")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            errors.append(f"Surface action {key} has no normalized digest.")
        result[key] = action
    if list(result) != sorted(result):
        errors.append("Surface actions are not sorted by key.")
    return result


def normalize_policy_actions(
    policy: Mapping[str, object],
    errors: list[str] | None = None,
) -> dict[str, Mapping[str, object]]:
    """Expand compact explicit-key groups into one reviewed entry per action."""

    failures = errors if errors is not None else []
    records = policy.get("actions")
    if isinstance(records, dict):
        return {
            key: value
            for key, value in records.items()
            if isinstance(key, str) and isinstance(value, dict)
        }
    if not isinstance(records, list):
        failures.append(
            "Policy actions must be an exact-key object or an array of explicit-key groups.",
        )
        return {}
    result: dict[str, Mapping[str, object]] = {}
    reserved = {"id", "action_keys", "reviewed_digests", "overrides"}
    for index, group in enumerate(records):
        if not isinstance(group, dict):
            failures.append(f"Policy action group #{index} must be an object.")
            continue
        group_id = group.get("id", f"#{index}")
        keys = group.get("action_keys")
        digests = group.get("reviewed_digests")
        overrides = group.get("overrides", {})
        if (
            not isinstance(keys, list)
            or not keys
            or not all(isinstance(key, str) and key for key in keys)
        ):
            failures.append(f"Policy group {group_id!r} requires explicit action_keys.")
            continue
        if len(keys) != len(set(keys)):
            failures.append(f"Policy group {group_id!r} repeats an action key.")
        if not isinstance(digests, dict) or set(digests) != set(keys):
            failures.append(
                f"Policy group {group_id!r} reviewed_digests must exactly match action_keys.",
            )
            digests = {}
        if not isinstance(overrides, dict) or not set(overrides) <= set(keys):
            failures.append(
                f"Policy group {group_id!r} overrides may only name its action_keys.",
            )
            overrides = {}
        common = {key: value for key, value in group.items() if key not in reserved}
        for key in keys:
            if key in result:
                failures.append(
                    f"Action {key} has more than one policy classification.",
                )
                continue
            override = overrides.get(key, {})
            if not isinstance(override, dict):
                failures.append(f"Policy override for {key} must be an object.")
                override = {}
            result[key] = {
                **common,
                **override,
                "reviewed_digest": digests.get(key),
            }
    return result


def _required_text(
    entry: Mapping[str, object], field: str, key: str, errors: list[str],
) -> None:
    if not isinstance(entry.get(field), str) or not str(entry[field]).strip():
        errors.append(f"Policy action {key} requires non-empty {field}.")


def validate_inventory(
    surface: Mapping[str, object],
    policy: Mapping[str, object],
) -> list[str]:
    """Return every schema, completeness, review, and contract failure."""

    errors: list[str] = []
    if surface.get("schema") != SURFACE_SCHEMA:
        errors.append(f"Surface schema must be {SURFACE_SCHEMA}.")
    if policy.get("schema") != POLICY_SCHEMA:
        errors.append(f"Policy schema must be {POLICY_SCHEMA}.")
    expected_surface_digest = surface_digest(surface)
    if surface.get("surface_sha256") != expected_surface_digest:
        errors.append(
            "Surface digest mismatch: "
            f"recorded {surface.get('surface_sha256')!r}, computed {expected_surface_digest}.",
        )
    diagnostics = surface.get("diagnostics", [])
    if not isinstance(diagnostics, list):
        errors.append("Surface diagnostics must be a list.")
    elif diagnostics:
        errors.extend(
            f"Discovery diagnostic: {diagnostic}" for diagnostic in diagnostics
        )

    actions = _action_map(surface, errors)
    policy_actions = normalize_policy_actions(policy, errors)
    for key in policy_actions:
        if not isinstance(key, str) or not key.startswith(ACTION_PREFIXES):
            errors.append(f"Policy contains invalid action key {key!r}.")
        if any(character in key for character in "*?[]"):
            errors.append(f"Policy action {key} uses a forbidden pattern character.")

    missing = sorted(set(actions) - set(policy_actions))
    stale = sorted(set(policy_actions) - set(actions))
    errors.extend(f"Unclassified action: {key}" for key in missing)
    errors.extend(f"Stale policy action: {key}" for key in stale)

    evidence = policy.get("evidence_families")
    if not isinstance(evidence, dict):
        errors.append("Policy evidence_families must be an object.")
        evidence = {}
    for evidence_id, family in evidence.items():
        if not isinstance(evidence_id, str) or not evidence_id:
            errors.append("Policy has an invalid evidence family identifier.")
            continue
        if not isinstance(family, dict):
            errors.append(f"Evidence family {evidence_id} must be an object.")
            continue
        _required_text(family, "contract", f"evidence:{evidence_id}", errors)
        tests = family.get("tests")
        if (
            not isinstance(tests, list)
            or not tests
            or not all(isinstance(test, str) and test.strip() for test in tests)
        ):
            errors.append(f"Evidence family {evidence_id} requires automated tests.")
            continue
        for test in tests:
            test_path = ROOT / test.split("::", 1)[0]
            if not test_path.is_file():
                errors.append(
                    f"Evidence family {evidence_id} references missing test {test}.",
                )

    enforcement_mappings: dict[tuple[str, str], str] = {}
    for key in sorted(set(actions) & set(policy_actions)):
        action = actions[key]
        entry = policy_actions[key]
        if not isinstance(entry, dict):
            errors.append(f"Policy action {key} must be an object.")
            continue
        classification = entry.get("classification")
        if classification not in CLASSIFICATIONS:
            errors.append(
                f"Policy action {key} has invalid classification {classification!r}.",
            )
            continue
        for field in ("domain", "consequence", "rationale", "evidence_id"):
            _required_text(entry, field, key, errors)
        if entry.get("reviewed_digest") != action.get("digest"):
            errors.append(
                f"Changed action requires review: {key} "
                f"({entry.get('reviewed_digest')!r} != {action.get('digest')!r}).",
            )
        evidence_id = entry.get("evidence_id")
        if isinstance(evidence_id, str) and evidence_id not in evidence:
            errors.append(
                f"Policy action {key} references missing evidence {evidence_id}.",
            )

        sinks = set(action.get("sinks", []))
        risk_flags = set(action.get("risk_flags", []))
        if classification == "read_only" and sinks & MUTATING_SINKS:
            errors.append(
                f"Read-only action {key} reaches mutation sinks: "
                + ", ".join(sorted(sinks & MUTATING_SINKS)),
            )
        if classification == "recoverable":
            reversal = entry.get("reversal_action")
            if not isinstance(reversal, str) or reversal not in actions:
                errors.append(
                    f"Recoverable action {key} requires an exact reversal_action.",
                )
        if classification == "protected":
            guard_key = entry.get("guard_key")
            enforcement = entry.get("enforcement")
            if action.get("kind") == "guard":
                if guard_key not in {None, key}:
                    errors.append(
                        f"Guard action {key} may only self-reference its guard_key.",
                    )
            elif guard_key is None and enforcement is None:
                errors.append(
                    f"Protected action {key} requires guard_key or enforcement.",
                )
            if guard_key is not None:
                if not isinstance(guard_key, str) or guard_key not in actions:
                    errors.append(
                        f"Protected action {key} references missing guard {guard_key!r}.",
                    )
                elif actions[guard_key].get("kind") != "guard":
                    errors.append(
                        f"Protected action {key} guard_key is not a guard action.",
                    )
                elif action.get("kind") != "guard" and guard_key not in action.get(
                    "guards",
                    [],
                ):
                    errors.append(
                        f"Protected action {key} does not invoke declared guard {guard_key}.",
                    )
            if enforcement is not None:
                _validate_enforcement(
                    key,
                    action,
                    classification,
                    enforcement,
                    enforcement_mappings,
                    errors,
                )
        elif "enforcement" in entry:
            errors.append(f"Only protected action {key} may declare enforcement.")
        if classification == "transport":
            targets = entry.get("targets")
            if not isinstance(targets, list) or not targets:
                errors.append(f"Transport action {key} requires exact targets.")
            else:
                if len(targets) != len(set(targets)):
                    errors.append(f"Transport action {key} repeats a target.")
                for target in targets:
                    if not isinstance(target, str) or target not in actions:
                        errors.append(
                            f"Transport action {key} has missing target {target!r}.",
                        )
                detected = set(action.get("delegates", [])) - set(
                    action.get("unresolved_delegates", []),
                )
                if detected - set(targets):
                    errors.append(
                        f"Transport action {key} omits detected targets: "
                        + ", ".join(sorted(detected - set(targets))),
                    )
            if sinks & MUTATING_SINKS:
                errors.append(
                    f"Transport action {key} contains a direct mutation sink.",
                )
        if classification == "system_internal":
            _required_text(entry, "reachability_proof", key, errors)
            if action.get("kind") in {"rpc", "route", "ui", "server_action", "client"}:
                errors.append(
                    f"Externally reachable action {key} cannot be system_internal.",
                )
        internal_sink = action.get("kind") == "sink" and classification == "system_internal"
        if (
            risk_flags & MANDATORY_PROTECTED_FLAGS
            and classification != "protected"
            and not internal_sink
        ):
            errors.append(
                f"Mandatory risk action {key} must be protected "
                f"({', '.join(sorted(risk_flags & MANDATORY_PROTECTED_FLAGS))}).",
            )
        if action.get("kind") == "guard" and classification != "protected":
            errors.append(f"Guard action {key} must be protected.")

    expected_qualified = qualified_policy_digest(surface, policy)
    recorded_qualified = policy.get("qualified_policy_digest")
    if recorded_qualified is not None and recorded_qualified != expected_qualified:
        errors.append(
            "Qualified policy digest mismatch: "
            f"recorded {recorded_qualified!r}, computed {expected_qualified}.",
        )
    return errors


def _validate_enforcement(
    key: str,
    action: Mapping[str, object],
    classification: object,
    enforcement: object,
    mappings: dict[tuple[str, str], str],
    errors: list[str],
) -> None:
    if classification != "protected" or not isinstance(enforcement, dict):
        errors.append(f"Protected action {key} has invalid enforcement.")
        return
    if set(enforcement) != {"kind", "model", "operation"}:
        errors.append(
            f"Model-operation enforcement for {key} must contain exactly kind, model, operation.",
        )
        return
    model = enforcement.get("model")
    operation = enforcement.get("operation")
    if enforcement.get("kind") != "model_operation":
        errors.append(f"Protected action {key} has unsupported enforcement kind.")
        return
    if (
        not isinstance(model, str)
        or not model
        or operation not in {"create", "write", "unlink"}
    ):
        errors.append(
            f"Protected action {key} has invalid model-operation enforcement.",
        )
        return
    expected_key = f"rpc:{model}.{operation}"
    if key != expected_key or action.get("kind") != "rpc":
        errors.append(
            f"Model-operation enforcement {model}.{operation} must be declared on {expected_key}.",
        )
    mapping = (model, str(operation))
    if mapping in mappings:
        errors.append(
            f"Model-operation enforcement {model}.{operation} is duplicated by "
            f"{mappings[mapping]} and {key}.",
        )
    else:
        mappings[mapping] = key


def compare_surfaces(
    expected: Mapping[str, object],
    candidate: Mapping[str, object],
    *,
    action_kinds: set[str] | None = None,
    ignored_action_fields: set[str] | None = None,
) -> list[str]:
    """Return actionable module/action drift between two valid-shaped surfaces."""

    def comparable_action(action: Mapping[str, object]) -> dict[str, object]:
        """Discard registry-local locator noise without hiding behavior drift.

        Source line numbers are useful evidence in the sealed surface, but a
        comment or an unrelated method inserted earlier in the file must not
        force thousands of action reviews.  Database record IDs are likewise
        allocation details: delivered records are identified by their XML ID.
        Keep source paths semantic so moving an action between files still
        requires review.
        """

        comparable = dict(action)
        sources = []
        xmlid = action.get("xmlid")
        for source in action.get("sources", []):
            if not isinstance(source, dict):
                sources.append(source)
                continue
            path = source.get("path")
            match = (
                re.fullmatch(r"database:([^:]+):\d+", path)
                if isinstance(path, str)
                else None
            )
            if match and isinstance(xmlid, str) and xmlid:
                path = f"database:{match.group(1)}:{xmlid}"
            normalized = {
                field: value
                for field, value in source.items()
                if field != "line"
            }
            if path is not None:
                normalized["path"] = path
            sources.append(normalized)
        comparable["sources"] = sorted(sources, key=canonical_json)
        return comparable

    errors: list[str] = []
    if expected.get("module_set_sha256") != candidate.get("module_set_sha256"):
        errors.append(
            "Installed module set changed: "
            f"{expected.get('module_set_sha256')} -> {candidate.get('module_set_sha256')}.",
        )
    expected_modules = {
        module.get("name"): module
        for module in expected.get("modules", [])
        if isinstance(module, dict)
    }
    candidate_modules = {
        module.get("name"): module
        for module in candidate.get("modules", [])
        if isinstance(module, dict)
    }
    for name in sorted(set(candidate_modules) - set(expected_modules)):
        errors.append(f"Added installed module: {name}")
    for name in sorted(set(expected_modules) - set(candidate_modules)):
        errors.append(f"Removed installed module: {name}")
    for name in sorted(set(expected_modules) & set(candidate_modules)):
        if expected_modules[name] != candidate_modules[name]:
            errors.append(f"Changed installed module identity: {name}")

    expected_actions = {
        action.get("key"): action
        for action in expected.get("actions", [])
        if isinstance(action, dict)
    }
    candidate_actions = {
        action.get("key"): action
        for action in candidate.get("actions", [])
        if isinstance(action, dict)
    }
    if action_kinds is not None:
        expected_actions = {
            key: action
            for key, action in expected_actions.items()
            if action.get("kind") in action_kinds
        }
        candidate_actions = {
            key: action
            for key, action in candidate_actions.items()
            if action.get("kind") in action_kinds
        }
    for key in sorted(set(candidate_actions) - set(expected_actions)):
        errors.append(f"Added action requires classification: {key}")
    for key in sorted(set(expected_actions) - set(candidate_actions)):
        errors.append(f"Removed action leaves stale review: {key}")
    for key in sorted(set(expected_actions) & set(candidate_actions)):
        expected_action = comparable_action(expected_actions[key])
        candidate_action = comparable_action(candidate_actions[key])
        if ignored_action_fields:
            expected_action = {
                field: value
                for field, value in expected_action.items()
                if field not in ignored_action_fields
            }
            candidate_action = {
                field: value
                for field, value in candidate_action.items()
                if field not in ignored_action_fields
            }
        if expected_action == candidate_action:
            continue
        fields = sorted(
            field
            for field in set(expected_action) | set(candidate_action)
            if expected_action.get(field) != candidate_action.get(field)
        )
        errors.append(f"Changed action requires review: {key} ({', '.join(fields)})")
    return errors


def _print_errors(title: str, errors: Sequence[str]) -> int:
    if not errors:
        print(f"{title}: PASS")
        return 0
    print(f"{title}: FAIL", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    return 1


def _discover_from_args(args: argparse.Namespace) -> dict[str, object]:
    runtime = load_json(args.runtime) if getattr(args, "runtime", None) else None
    modules = args.modules.split(",") if getattr(args, "modules", None) else None
    roots = (
        args.root_modules.split(",")
        if getattr(args, "root_modules", None)
        else PRODUCT_MODULES
    )
    return discover_surface(
        root=args.root,
        root_modules=roots,
        installed_modules=modules,
        runtime=runtime,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover", aliases=["candidate"])
    discover.add_argument("--root", type=Path, default=ROOT)
    discover.add_argument("--root-modules")
    discover.add_argument("--modules")
    discover.add_argument("--runtime", type=Path)
    discover.add_argument("--output", type=Path)

    refresh = subparsers.add_parser("refresh")
    refresh.add_argument("--candidate", type=Path, required=True)
    refresh.add_argument("--surface", type=Path, default=DEFAULT_SURFACE)
    refresh.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    refresh.add_argument(
        "--runtime-policy",
        type=Path,
        default=DEFAULT_RUNTIME_POLICY,
    )

    compile_runtime = subparsers.add_parser("compile-runtime-policy")
    compile_runtime.add_argument("--surface", type=Path, default=DEFAULT_SURFACE)
    compile_runtime.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    compile_runtime.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_RUNTIME_POLICY,
    )

    check = subparsers.add_parser("check", aliases=["check-source"])
    check.add_argument("--root", type=Path, default=ROOT)
    check.add_argument("--surface", type=Path, default=DEFAULT_SURFACE)
    check.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    check.add_argument(
        "--runtime-policy",
        type=Path,
        default=DEFAULT_RUNTIME_POLICY,
    )
    check.add_argument("--candidate", type=Path)
    check.add_argument("--runtime", type=Path)
    check.add_argument("--skip-source-drift", action="store_true")

    digest = subparsers.add_parser("digest")
    digest.add_argument("--surface", type=Path, default=DEFAULT_SURFACE)
    digest.add_argument("--policy", type=Path, default=DEFAULT_POLICY)

    compare = subparsers.add_parser("compare-runtime")
    compare.add_argument("--root", type=Path, default=ROOT)
    compare.add_argument("--surface", type=Path, default=DEFAULT_SURFACE)
    compare.add_argument("--runtime", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command in {"discover", "candidate"}:
            surface = _discover_from_args(args)
            if args.output:
                write_json(args.output, surface)
            else:
                print(json.dumps(surface, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.command == "refresh":
            candidate = load_json(args.candidate)
            policy = load_json(args.policy)
            policy["qualified_policy_digest"] = qualified_policy_digest(
                candidate,
                policy,
            )
            errors = validate_inventory(candidate, policy)
            if errors:
                return _print_errors("Action-risk refresh", errors)
            write_json(args.surface, candidate)
            write_json(args.policy, policy)
            write_json(
                args.runtime_policy,
                build_runtime_policy(candidate, policy),
            )
            print(
                f"Action-risk surface refreshed: {len(candidate.get('actions', []))} actions, "
                f"digest {qualified_policy_digest(candidate, policy)}",
            )
            return 0
        if args.command == "compile-runtime-policy":
            surface = load_json(args.surface)
            policy = load_json(args.policy)
            errors = validate_inventory(surface, policy)
            if errors:
                return _print_errors("Action-risk runtime policy", errors)
            runtime_policy = build_runtime_policy(surface, policy)
            write_json(args.output, runtime_policy)
            print(
                "Protected runtime policy compiled: "
                f"{len(runtime_policy['actions'])} actions, "
                f"{runtime_policy['runtime_policy_sha256']}",
            )
            return 0
        if args.command == "digest":
            surface = load_json(args.surface)
            policy = load_json(args.policy)
            errors = validate_inventory(surface, policy)
            if errors:
                return _print_errors("Action-risk digest", errors)
            print(qualified_policy_digest(surface, policy))
            return 0
        if args.command == "compare-runtime":
            expected = load_json(args.surface)
            runtime = load_json(args.runtime)
            candidate = discover_surface(root=args.root, runtime=runtime)
            return _print_errors(
                "Action-risk runtime comparison",
                compare_surfaces(expected, candidate),
            )
        if args.command in {"check", "check-source"}:
            surface = load_json(args.surface)
            policy = load_json(args.policy)
            errors = validate_inventory(surface, policy)
            runtime_policy = load_json(args.runtime_policy)
            errors.extend(validate_runtime_policy(surface, policy, runtime_policy))
            if args.candidate:
                errors.extend(compare_surfaces(surface, load_json(args.candidate)))
            elif args.runtime:
                errors.extend(
                    compare_surfaces(
                        surface,
                        discover_surface(
                            root=args.root, runtime=load_json(args.runtime),
                        ),
                    ),
                )
            elif not args.skip_source_drift:
                module_names = [
                    module["name"]
                    for module in surface.get("modules", [])
                    if isinstance(module, dict) and isinstance(module.get("name"), str)
                ]
                candidate = discover_surface(
                    root=args.root,
                    root_modules=surface.get("root_modules", PRODUCT_MODULES),
                    installed_modules=module_names,
                    country_codes=surface.get("country_codes", ("fr",)),
                )
                action_kinds = None
                if surface.get("discovery") == "runtime+source":
                    action_kinds = {"client", "guard", "sink"}
                errors.extend(
                    compare_surfaces(
                        surface,
                        candidate,
                        action_kinds=action_kinds,
                        ignored_action_fields={"delegates", "unresolved_delegates"},
                    ),
                )
            return _print_errors("Action-risk inventory", errors)
    except (InventoryError, OSError, ValueError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
