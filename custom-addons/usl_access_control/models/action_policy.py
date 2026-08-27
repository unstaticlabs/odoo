import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

_POLICY_DIRECTORY = Path(__file__).resolve().parent.parent / "policy"
_SURFACE_FILE = _POLICY_DIRECTORY / "action_surface.json"
_POLICY_FILE = _POLICY_DIRECTORY / "action_policy.json"
_SURFACE_SCHEMA = "usl-action-risk-surface-v1"
_POLICY_SCHEMA = "usl-action-risk-policy-v1"
_CLASSIFICATIONS = frozenset(
    {
        "protected",
        "read_only",
        "recoverable",
        "system_internal",
        "transport",
    },
)


class ActionPolicyConfigurationError(ValueError):
    """The qualified action policy cannot safely drive runtime enforcement."""


@dataclass(frozen=True)
class ActionPolicyEntry:
    action_key: str
    classification: str
    action_name: str | None
    enforcement: dict | None


@dataclass(frozen=True)
class ActionPolicy:
    entries: dict[str, ActionPolicyEntry]
    model_operation_guards: dict[tuple[str, str], ActionPolicyEntry]
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


def _read_json(path):
    try:
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


def _canonical_digest(surface, policy):
    # The generated policy may include its own result. Exclude that field from
    # the input so the digest is deterministic and non-recursive.
    policy_payload = {
        key: value
        for key, value in policy.items()
        if key != "qualified_policy_digest"
    }
    canonical = json.dumps(
        {"action_policy": policy_payload, "action_surface": surface},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _entry_records(policy):
    records = policy.get("actions", policy.get("classifications"))
    if records is None:
        message = "Qualified action policy must define 'actions' or 'classifications'."
        raise ActionPolicyConfigurationError(message)
    if isinstance(records, dict):
        for action_key, values in records.items():
            if not isinstance(values, dict):
                raise ActionPolicyConfigurationError(
                    f"Policy entry {action_key!r} must be a JSON object.",
                )
            yield action_key, values
        return
    if not isinstance(records, list):
        message = "Qualified action policy entries must be an object or an array."
        raise ActionPolicyConfigurationError(message)
    for values in records:
        if not isinstance(values, dict):
            message = "Every policy entry must be a JSON object."
            raise ActionPolicyConfigurationError(message)
        action_keys = values.get("action_keys")
        if action_keys is not None:
            if not isinstance(action_keys, list) or not all(
                isinstance(key, str) and key for key in action_keys
            ):
                message = (
                    "A grouped policy entry must contain non-empty string action_keys."
                )
                raise ActionPolicyConfigurationError(message)
            reviewed_digests = values.get("reviewed_digests")
            overrides = values.get("overrides", {})
            if not isinstance(reviewed_digests, dict) or set(reviewed_digests) != set(
                action_keys,
            ):
                message = (
                    "A grouped policy entry must bind every exact action key to its digest."
                )
                raise ActionPolicyConfigurationError(message)
            if not isinstance(overrides, dict) or not set(overrides) <= set(action_keys):
                message = (
                    "Grouped policy overrides may only name their exact action keys."
                )
                raise ActionPolicyConfigurationError(message)
            common = {
                key: value
                for key, value in values.items()
                if key not in {"action_keys", "id", "overrides", "reviewed_digests"}
            }
            for action_key in action_keys:
                override = overrides.get(action_key, {})
                if not isinstance(override, dict):
                    raise ActionPolicyConfigurationError(
                        f"Policy override for {action_key!r} must be a JSON object.",
                    )
                yield action_key, {
                    **common,
                    **override,
                    "reviewed_digest": reviewed_digests[action_key],
                }
            continue
        action_key = values.get("action_key", values.get("key"))
        if not isinstance(action_key, str) or not action_key:
            message = "Every policy entry must contain action_key, key, or action_keys."
            raise ActionPolicyConfigurationError(message)
        yield action_key, values


def _load_entries(policy):
    result = {}
    seen = set()
    for action_key, values in _entry_records(policy):
        if action_key in seen:
            raise ActionPolicyConfigurationError(
                f"Action {action_key!r} has more than one policy classification.",
            )
        seen.add(action_key)
        classification = values.get("classification")
        if classification not in _CLASSIFICATIONS:
            raise ActionPolicyConfigurationError(
                f"Action {action_key!r} has invalid classification {classification!r}.",
            )
        action_name = values.get("action_name", values.get("label"))
        if action_name is not None and not isinstance(action_name, str):
            raise ActionPolicyConfigurationError(
                f"Action {action_key!r} has a non-string action name.",
            )
        enforcement = values.get("enforcement")
        if enforcement is not None and not isinstance(enforcement, dict):
            raise ActionPolicyConfigurationError(
                f"Action {action_key!r} has invalid enforcement metadata.",
            )
        # Runtime enforcement only needs protected entries. The complete
        # one-to-one classification stays in the reviewed artifact and is
        # validated by the release gate, avoiding tens of thousands of
        # long-lived Python objects in every Odoo worker.
        if classification == "protected":
            result[action_key] = ActionPolicyEntry(
                action_key=action_key,
                classification=classification,
                action_name=action_name,
                enforcement=enforcement,
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


@lru_cache(maxsize=1)
def load_action_policy():
    surface = _read_json(_SURFACE_FILE)
    policy = _read_json(_POLICY_FILE)
    if surface.get("schema") != _SURFACE_SCHEMA:
        raise ActionPolicyConfigurationError(
            f"Qualified action surface must use schema {_SURFACE_SCHEMA!r}.",
        )
    if policy.get("schema") != _POLICY_SCHEMA:
        raise ActionPolicyConfigurationError(
            f"Qualified action policy must use schema {_POLICY_SCHEMA!r}.",
        )
    digest = _canonical_digest(surface, policy)
    declared_digest = policy.get("qualified_policy_digest")
    if declared_digest is not None and declared_digest != digest:
        message = (
            "The qualified action-policy digest does not match the canonical policy content."
        )
        raise ActionPolicyConfigurationError(message)
    entries = _load_entries(policy)
    return ActionPolicy(
        entries=MappingProxyType(entries),
        model_operation_guards=MappingProxyType(_model_operation_guards(entries)),
        qualified_policy_digest=digest,
    )
