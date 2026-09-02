import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

_POLICY_DIRECTORY = Path(__file__).resolve().parent.parent / "policy"
_SURFACE_FILE = _POLICY_DIRECTORY / "action_surface.json"
_POLICY_FILE = _POLICY_DIRECTORY / "action_policy.json"
_RUNTIME_POLICY_FILE = _POLICY_DIRECTORY / "protected_runtime_policy.json"
_AGENT_READONLY_RUNTIME_POLICY_FILE = (
    _POLICY_DIRECTORY / "agent_readonly_runtime_policy.json"
)
_RUNTIME_POLICY_SCHEMA = "usl-action-risk-protected-runtime-v2"
_AGENT_READONLY_RUNTIME_POLICY_SCHEMA = "usl-agent-access-runtime-v2"
_RUNTIME_POLICY_MAX_BYTES = 512 * 1024
_AGENT_READONLY_RUNTIME_POLICY_MAX_BYTES = 4 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ActionPolicyConfigurationError(ValueError):
    """The qualified action policy cannot safely drive runtime enforcement."""


@dataclass(frozen=True)
class ActionPolicyEntry:
    action_key: str
    classification: str
    action_name: str | None
    enforcement: Mapping[str, str] | None


@dataclass(frozen=True)
class ActionPolicy:
    entries: dict[str, ActionPolicyEntry]
    model_operation_guards: dict[tuple[str, str], ActionPolicyEntry]
    server_actions: dict[str, str]
    qualified_policy_digest: str

    def protected_action(self, action_key):
        entry = self.entries.get(action_key)
        if not entry:
            raise ActionPolicyConfigurationError(
                f"Protected action guard {action_key!r} is absent from the qualified policy.",
            )
        if entry.classification != "protected":
            raise ActionPolicyConfigurationError(
                f"Protected action guard {action_key!r} is classified "
                f"{entry.classification!r}, not 'protected'.",
            )
        return entry

    def protected_guard(self, guard_key):
        action_key = guard_key if guard_key.startswith("guard:") else f"guard:{guard_key}"
        return self.protected_action(action_key)

    def model_operation_guard(self, model_name, operation):
        return self.model_operation_guards.get((model_name, operation))

    def server_action_classification(self, action_key):
        return self.server_actions.get(action_key)


@dataclass(frozen=True)
class AgentReadonlyPolicy:
    read_only_actions: frozenset[str]
    collaboration_actions: frozenset[str]
    write_actions: frozenset[str]
    qualified_policy_digest: str

    def access_for(self, model_name, method_name):
        action_key = f"rpc:{model_name}.{method_name}"
        if action_key in self.read_only_actions:
            return "read_only"
        if action_key in self.collaboration_actions:
            return "collaboration"
        if action_key in self.write_actions:
            return "write"
        return None


def _read_json(path, *, max_bytes=None):
    try:
        if max_bytes is not None and path.stat().st_size > max_bytes:
            raise ActionPolicyConfigurationError(
                f"Qualified action policy file {path} exceeds its runtime size budget.",
            )
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise ActionPolicyConfigurationError(
            f"Cannot load the qualified action policy file {path}: {error}",
        ) from error
    if not isinstance(value, dict):
        raise ActionPolicyConfigurationError(
            f"Qualified action policy file {path} must contain a JSON object.",
        )
    return value


def _runtime_policy_digest(runtime_policy):
    payload = {
        key: value
        for key, value in runtime_policy.items()
        if key != "runtime_policy_sha256"
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _load_entries(runtime_policy):
    records = runtime_policy.get("actions")
    if not isinstance(records, list):
        message = "Protected runtime policy actions must be an array."
        raise ActionPolicyConfigurationError(message)
    result = {}
    previous_key = None
    allowed_fields = {"action_key", "action_name", "classification", "enforcement"}
    for values in records:
        if not isinstance(values, dict):
            message = "Every protected runtime policy entry must be a JSON object."
            raise ActionPolicyConfigurationError(message)
        if not set(values) <= allowed_fields:
            message = "Protected runtime policy entries contain unsupported fields."
            raise ActionPolicyConfigurationError(message)
        action_key = values.get("action_key")
        if not isinstance(action_key, str) or not action_key:
            message = "Every protected runtime policy entry requires an action_key."
            raise ActionPolicyConfigurationError(message)
        if action_key in result:
            raise ActionPolicyConfigurationError(
                f"Action {action_key!r} has more than one policy classification.",
            )
        if previous_key is not None and action_key < previous_key:
            message = "Protected runtime policy actions must be sorted by action_key."
            raise ActionPolicyConfigurationError(message)
        previous_key = action_key
        classification = values.get("classification")
        if classification != "protected":
            raise ActionPolicyConfigurationError(
                f"Runtime action {action_key!r} must be classified 'protected'.",
            )
        action_name = values.get("action_name")
        if action_name is not None and not isinstance(action_name, str):
            raise ActionPolicyConfigurationError(
                f"Action {action_key!r} has a non-string action name.",
            )
        enforcement = values.get("enforcement")
        if enforcement is not None and not isinstance(enforcement, dict):
            raise ActionPolicyConfigurationError(
                f"Action {action_key!r} has invalid enforcement metadata.",
            )
        result[action_key] = ActionPolicyEntry(
            action_key=action_key,
            classification=classification,
            action_name=action_name,
            enforcement=(
                MappingProxyType(dict(enforcement))
                if enforcement is not None
                else None
            ),
        )
    return result


def _model_operation_guards(entries):
    result = {}
    for entry in entries.values():
        enforcement = entry.enforcement or {}
        if enforcement.get("kind") != "model_operation":
            continue
        model_name = enforcement.get("model")
        operation = enforcement.get("operation")
        if not isinstance(model_name, str) or not model_name:
            raise ActionPolicyConfigurationError(
                f"Model-operation guard {entry.action_key!r} has no valid model.",
            )
        if operation not in {"create", "write", "unlink"}:
            raise ActionPolicyConfigurationError(
                f"Model-operation guard {entry.action_key!r} has invalid operation "
                f"{operation!r}.",
            )
        if entry.classification != "protected":
            raise ActionPolicyConfigurationError(
                f"Model-operation guard {entry.action_key!r} is classified "
                f"{entry.classification!r}, not 'protected'.",
            )
        lookup_key = (model_name, operation)
        previous = result.get(lookup_key)
        if previous:
            raise ActionPolicyConfigurationError(
                f"Multiple protected guards apply to {model_name}.{operation}: "
                f"{previous.action_key}, {entry.action_key}.",
            )
        result[lookup_key] = entry
    return result


def _load_server_actions(runtime_policy):
    records = runtime_policy.get("server_actions")
    if not isinstance(records, list):
        message = "Protected runtime policy server_actions must be an array."
        raise ActionPolicyConfigurationError(message)
    classifications = {
        "operational",
        "protected",
        "read_only",
        "recoverable",
        "system_internal",
        "transport",
    }
    result = {}
    previous_key = None
    for values in records:
        if not isinstance(values, dict) or set(values) != {
            "action_key",
            "classification",
        }:
            message = (
                "Every reviewed server action requires only an action key and "
                "classification."
            )
            raise ActionPolicyConfigurationError(message)
        action_key = values.get("action_key")
        classification = values.get("classification")
        if not isinstance(action_key, str) or not action_key.startswith(
            "server_action:",
        ):
            message = "Reviewed server actions require stable server_action keys."
            raise ActionPolicyConfigurationError(message)
        if previous_key is not None and action_key <= previous_key:
            message = (
                "Reviewed server actions must be unique and sorted by action_key."
            )
            raise ActionPolicyConfigurationError(message)
        if classification not in classifications:
            raise ActionPolicyConfigurationError(
                f"Server action {action_key!r} has invalid classification {classification!r}.",
            )
        previous_key = action_key
        result[action_key] = classification
    return result


def _load_sorted_action_keys(runtime_policy, field_name):
    values = runtime_policy.get(field_name)
    if not isinstance(values, list):
        raise ActionPolicyConfigurationError(
            f"Agent read-only runtime policy {field_name} must be an array.",
        )
    if any(
        not isinstance(action_key, str) or not action_key.startswith("rpc:")
        for action_key in values
    ):
        raise ActionPolicyConfigurationError(
            f"Agent read-only runtime policy {field_name} contains an invalid action key.",
        )
    if values != sorted(set(values)):
        raise ActionPolicyConfigurationError(
            f"Agent read-only runtime policy {field_name} must be sorted and unique.",
        )
    return frozenset(values)


@lru_cache(maxsize=1)
def load_action_policy():
    runtime_policy = _read_json(
        _RUNTIME_POLICY_FILE,
        max_bytes=_RUNTIME_POLICY_MAX_BYTES,
    )
    if runtime_policy.get("schema") != _RUNTIME_POLICY_SCHEMA:
        raise ActionPolicyConfigurationError(
            f"Protected runtime policy must use schema {_RUNTIME_POLICY_SCHEMA!r}.",
        )
    digest = _runtime_policy_digest(runtime_policy)
    if runtime_policy.get("runtime_policy_sha256") != digest:
        message = (
            "Protected runtime policy digest does not match its canonical content."
        )
        raise ActionPolicyConfigurationError(message)
    qualified_digest = runtime_policy.get("qualified_policy_digest")
    if not isinstance(qualified_digest, str) or not _SHA256.fullmatch(
        qualified_digest,
    ):
        message = "Protected runtime policy has no valid qualified policy digest."
        raise ActionPolicyConfigurationError(message)
    image_digest = os.environ.get("USL_ACTION_RISK_POLICY_SHA256")
    if (
        image_digest not in {None, "", "unverified"}
        and image_digest != qualified_digest
    ):
        message = (
            "Protected runtime policy does not match the qualified image policy digest."
        )
        raise ActionPolicyConfigurationError(message)
    entries = _load_entries(runtime_policy)
    return ActionPolicy(
        entries=MappingProxyType(entries),
        model_operation_guards=MappingProxyType(_model_operation_guards(entries)),
        server_actions=MappingProxyType(_load_server_actions(runtime_policy)),
        qualified_policy_digest=qualified_digest,
    )


@lru_cache(maxsize=1)
def load_agent_readonly_policy():
    runtime_policy = _read_json(
        _AGENT_READONLY_RUNTIME_POLICY_FILE,
        max_bytes=_AGENT_READONLY_RUNTIME_POLICY_MAX_BYTES,
    )
    if runtime_policy.get("schema") != _AGENT_READONLY_RUNTIME_POLICY_SCHEMA:
        raise ActionPolicyConfigurationError(
            "Agent read-only runtime policy has an unsupported schema.",
        )
    if runtime_policy.get("runtime_policy_sha256") != _runtime_policy_digest(
        runtime_policy,
    ):
        raise ActionPolicyConfigurationError(
            "Agent read-only runtime policy digest does not match its canonical content.",
        )
    qualified_digest = runtime_policy.get("qualified_policy_digest")
    if not isinstance(qualified_digest, str) or not _SHA256.fullmatch(
        qualified_digest,
    ):
        raise ActionPolicyConfigurationError(
            "Agent read-only runtime policy has no valid qualified policy digest.",
        )
    image_digest = os.environ.get("USL_ACTION_RISK_POLICY_SHA256")
    if (
        image_digest not in {None, "", "unverified"}
        and image_digest != qualified_digest
    ):
        raise ActionPolicyConfigurationError(
            "Agent read-only runtime policy does not match the qualified image policy digest.",
        )
    read_only_actions = _load_sorted_action_keys(runtime_policy, "read_only_actions")
    collaboration_actions = _load_sorted_action_keys(
        runtime_policy,
        "collaboration_actions",
    )
    write_actions = _load_sorted_action_keys(runtime_policy, "write_actions")
    if (
        read_only_actions & collaboration_actions
        or read_only_actions & write_actions
        or collaboration_actions & write_actions
    ):
        raise ActionPolicyConfigurationError(
            "Agent read-only, collaboration, and write action allowlists overlap.",
        )
    return AgentReadonlyPolicy(
        read_only_actions=read_only_actions,
        collaboration_actions=collaboration_actions,
        write_actions=write_actions,
        qualified_policy_digest=qualified_digest,
    )
