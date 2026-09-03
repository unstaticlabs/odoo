"""Validate a runtime cron inventory against the versioned production policy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SCHEMA = "usl-production-cron-policy-v1"
MODES = frozenset({"managed", "neutralized", "unmanaged"})

INVENTORY_SQL = r"""
WITH identities AS (
  SELECT
    cron.id,
    cron.active,
    count(data.id) AS identity_count,
    min(data.module || '.' || data.name) AS xmlid
  FROM ir_cron cron
  LEFT JOIN ir_model_data data
    ON data.model = 'ir.cron' AND data.res_id = cron.id
  GROUP BY cron.id, cron.active
)
SELECT json_build_object(
  'installed', coalesce(json_agg(xmlid ORDER BY xmlid) FILTER (WHERE identity_count = 1), '[]'::json),
  'active', coalesce(json_agg(xmlid ORDER BY xmlid) FILTER (WHERE identity_count = 1 AND active), '[]'::json),
  'invalid_identity_count', count(*) FILTER (WHERE identity_count <> 1)
) FROM identities;
""".strip()


class CronPolicyError(ValueError):
    """A cron policy or observed inventory is incomplete or unsafe."""


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CronPolicyError(f"cannot read cron policy: {path}") from error
    if not isinstance(value, dict) or set(value) != {"schema", "gates", "crons"}:
        raise CronPolicyError("cron policy fields differ")
    if value.get("schema") != SCHEMA:
        raise CronPolicyError("cron policy schema differs")
    gates = value.get("gates")
    crons = value.get("crons")
    if (
        not isinstance(gates, list)
        or not gates
        or len(gates) != len(set(gates))
        or not all(isinstance(item, str) and item for item in gates)
        or not isinstance(crons, dict)
        or not crons
    ):
        raise CronPolicyError("cron policy inventory is invalid")
    for xmlid, rule in crons.items():
        if (
            not isinstance(xmlid, str)
            or "." not in xmlid
            or not isinstance(rule, dict)
            or set(rule) != {"gate", "reason"}
            or rule["gate"] not in {*gates, None}
            or not isinstance(rule["reason"], str)
            or not rule["reason"].strip()
        ):
            raise CronPolicyError(f"invalid cron policy entry: {xmlid!r}")
    return value


def validate_runtime(
    policy: dict[str, Any] | None,
    *,
    mode: str,
    gates: dict[str, bool],
    installed: list[str],
    active: list[str],
    invalid_identity_count: int,
) -> dict[str, Any]:
    if mode not in MODES:
        raise CronPolicyError("cron policy mode is invalid")
    if (
        len(installed) != len(set(installed))
        or len(active) != len(set(active))
        or set(active) - set(installed)
        or invalid_identity_count < 0
    ):
        raise CronPolicyError("observed cron inventory is invalid")
    if mode == "unmanaged":
        if policy is not None or gates:
            raise CronPolicyError("unmanaged cron policy must not declare policy or gates")
        return {
            "mode": mode,
            "installed_xmlids": sorted(installed),
            "active_xmlids": sorted(active),
            "status": "observed",
        }
    if policy is None:
        raise CronPolicyError("managed cron policy is unavailable")
    expected_gates = set(policy["gates"])
    if set(gates) != expected_gates or any(type(value) is not bool for value in gates.values()):
        raise CronPolicyError("cron gate decision is incomplete")
    if gates.get("always") is not True:
        raise CronPolicyError("the always cron gate must be enabled")
    expected_installed = set(policy["crons"])
    if invalid_identity_count or set(installed) != expected_installed:
        raise CronPolicyError(
            "installed cron inventory differs: "
            + json.dumps(
                {
                    "invalid_identity_count": invalid_identity_count,
                    "missing": sorted(expected_installed - set(installed)),
                    "unknown": sorted(set(installed) - expected_installed),
                },
                sort_keys=True,
            ),
        )
    desired = set()
    if mode == "managed":
        desired = {
            xmlid
            for xmlid, rule in policy["crons"].items()
            if rule["gate"] is not None and gates[rule["gate"]]
        }
    if set(active) != desired:
        raise CronPolicyError(
            "active cron inventory differs: "
            + json.dumps(
                {
                    "missing": sorted(desired - set(active)),
                    "unexpected": sorted(set(active) - desired),
                },
                sort_keys=True,
            ),
        )
    return {
        "mode": mode,
        "installed_xmlids": sorted(installed),
        "active_xmlids": sorted(active),
        "disabled_xmlids": sorted(expected_installed - desired),
        "gates": gates,
        "status": "passed",
    }


def desired_active(
    policy: dict[str, Any],
    *,
    mode: str,
    gates: dict[str, bool],
) -> list[str]:
    """Return the exact desired cron set after validating every decision."""
    installed = sorted(policy.get("crons", {}))
    desired = (
        sorted(
            xmlid
            for xmlid, rule in policy.get("crons", {}).items()
            if rule.get("gate") is not None and gates.get(rule["gate"])
        )
        if mode == "managed"
        else []
    )
    validate_runtime(
        policy,
        mode=mode,
        gates=gates,
        installed=installed,
        active=desired,
        invalid_identity_count=0,
    )
    return desired


def render_odoo_apply_script(
    policy: dict[str, Any],
    *,
    mode: str,
    gates: dict[str, bool],
) -> str:
    """Render a self-contained Odoo-shell program for candidate convergence."""
    desired = desired_active(policy, mode=mode, gates=gates)
    policy_json = json.dumps(policy, sort_keys=True, separators=(",", ":"))
    desired_json = json.dumps(desired, separators=(",", ":"))
    return f'''\
import json

policy = json.loads({policy_json!r})
desired = set(json.loads({desired_json!r}))
Cron = env["ir.cron"].sudo().with_context(active_test=False)
crons = Cron.search([])
rows = env["ir.model.data"].sudo().search([
    ("model", "=", "ir.cron"),
    ("res_id", "in", crons.ids),
])
xmlids_by_id = {{}}
for row in rows:
    xmlids_by_id.setdefault(row.res_id, []).append(f"{{row.module}}.{{row.name}}")
ambiguous = {{record_id: values for record_id, values in xmlids_by_id.items() if len(values) != 1}}
missing_ids = sorted(set(crons.ids) - set(xmlids_by_id))
if ambiguous or missing_ids:
    raise RuntimeError("Every installed cron must have exactly one XML ID: " + json.dumps({{"ambiguous": ambiguous, "missing_ids": missing_ids}}, sort_keys=True))
installed = {{values[0]: crons.browse(record_id) for record_id, values in xmlids_by_id.items()}}
unknown = sorted(set(installed) - set(policy["crons"]))
missing = sorted(set(policy["crons"]) - set(installed))
if unknown or missing:
    raise RuntimeError("Installed cron inventory differs from policy: " + json.dumps({{"unknown": unknown, "missing": missing}}, sort_keys=True))
currently_active = {{xmlid for xmlid, cron in installed.items() if cron.active}}
to_enable = sorted(desired - currently_active)
to_disable = sorted(currently_active - desired)
if to_disable:
    Cron.browse([installed[xmlid].id for xmlid in to_disable]).write({{"active": False}})
if to_enable:
    Cron.browse([installed[xmlid].id for xmlid in to_enable]).write({{"active": True}})
env.cr.commit()
print("USL_CRON_POLICY_RESULT=" + json.dumps({{
    "schema": "usl-cron-policy-application/v1",
    "status": "applied",
    "active_xmlids": sorted(desired),
    "disabled_xmlids": sorted(set(installed) - desired),
    "enabled": to_enable,
    "disabled": to_disable,
}}, sort_keys=True))
'''
