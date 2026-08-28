from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts import action_risk_inventory as inventory

ZERO = "0" * 64
ONE = "1" * 64


class ActionRiskInventoryTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def write(self, relative: str, content: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def manifest(
        self,
        module: str,
        *,
        depends: tuple[str, ...] = (),
        auto_install: bool | list[str] = False,
        countries: tuple[str, ...] = (),
        origin: str = "custom-addons",
    ) -> None:
        values = {
            "name": module,
            "version": "19.0.1.0.0",
            "depends": list(depends),
            "auto_install": auto_install,
            "countries": list(countries),
            "installable": True,
        }
        self.write(f"{origin}/{module}/__manifest__.py", repr(values))

    def action(self, key: str, kind: str, **values) -> dict[str, object]:
        return {
            "key": key,
            "kind": kind,
            "digest": values.pop("digest", ZERO),
            "modules": ["app"],
            "sources": [{"path": "custom-addons/app/models.py"}],
            **values,
        }

    def surface(self, actions: list[dict[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {
            "schema": inventory.SURFACE_SCHEMA,
            "root_modules": ["app"],
            "country_codes": ["fr"],
            "module_set_sha256": ZERO,
            "modules": [],
            "actions": sorted(actions, key=lambda action: action["key"]),
            "diagnostics": [],
            "discovery": "source",
        }
        value["surface_sha256"] = inventory.surface_digest(value)
        return value

    @staticmethod
    def evidence() -> dict[str, object]:
        return {
            "contract": "The shared contract is exercised.",
            "tests": ["scripts/tests/test_action_risk_inventory.py"],
        }

    def entry(self, action: dict[str, object], classification: str, **values):
        return {
            "classification": classification,
            "domain": "test",
            "consequence": "Test consequence.",
            "rationale": "Test rationale.",
            "evidence_id": "test-contract",
            "reviewed_digest": action["digest"],
            **values,
        }

    def policy(self, actions: dict[str, object] | list[object]) -> dict[str, object]:
        return {
            "schema": inventory.POLICY_SCHEMA,
            "evidence_families": {"test-contract": self.evidence()},
            "actions": actions,
        }

    def test_stable_ast_dump_omits_version_specific_empty_fields(self):
        node = ast.parse("def action(value):\n    return value\n").body[0]

        dumped = inventory.stable_ast_dump(node)

        self.assertNotIn("posonlyargs", dumped)
        self.assertNotIn("kw_defaults", dumped)
        self.assertNotIn("decorator_list", dumped)
        self.assertIn("args=[arg(arg='value')]", dumped)
        self.assertIn("Return(value=Name(id='value', ctx=Load()))", dumped)


class TestDiscovery(ActionRiskInventoryTestCase):
    def _build_source_tree(self):
        self.manifest("base", origin="odoo/addons")
        self.manifest("app", depends=("base",))
        self.manifest("bridge", depends=("app",), auto_install=True)
        self.manifest(
            "other_country",
            depends=("app",),
            auto_install=["app"],
            countries=("be",),
        )
        self.write(
            "odoo/orm/models.py",
            """
class BaseModel:
    def read(self):
        return self

    def unlink(self):
        return True
""",
        )
        self.write(
            "custom-addons/app/models.py",
            """
import os
import requests
from pathlib import Path
from odoo import models

class Thing(models.Model):
    _name = "x.thing"

    def create(self, values):
        return super().create(values)

    def write(self, values):
        return super().write(values)

    def status(self):
        return {"ok": True}

    def guarded(self):
        self._usl_require_irreversible_action("test.guarded", "guarded action")
        self.env.cr.execute("UPDATE x_thing SET active = FALSE")

    def exact_policy_operation(self):
        self._usl_require_irreversible_action(
            entry.action_key,
            exact_policy_key=True,
        )

    def dom_cleanup(self, element, child, query):
        element.remove(child)
        query.unlink()

    def toggle_lock(self):
        self.is_locked = not self.is_locked

    def change_tax_lock_date(self):
        return self.write({"tax_lock_date": False})

    def evaluate_stored_context(self):
        return safe_eval(self.action_context)

    def _mock_peppol_deregister_participant(self):
        return self.unlink()

    def action_disconnect(self):
        requests.post("https://rtc.invalid/disconnect")

    def _totp_rate_limit_purge(self):
        return self.unlink()

    def filesystem_cleanup(self, target):
        os.unlink(target)
        Path.unlink(target)

    def call_other(self):
        return self.env["x.other"].submit()

    def mutate_via_private_helper(self):
        return self._persist_change()

    def _persist_change(self):
        return self.write({"name": "changed"})

    def elevated_write(self):
        return self.sudo().write({"name": "elevated"})
""",
        )
        self.write(
            "custom-addons/app/controllers.py",
            """
from odoo import http

class API(http.Controller):
    @http.route(["/x/action"], type="jsonrpc", methods=["POST"], auth="user")
    def action(self):
        return self._mutate()

    def _mutate(self):
        return request.env["x.thing"].write({"name": "changed"})
""",
        )
        self.write(
            "custom-addons/app/views/actions.xml",
            """
<odoo>
  <record id="cron_x" model="ir.cron">
    <field name="model_id" ref="model_x_thing"/>
    <field name="code">model.guarded()</field>
  </record>
  <record id="server_x" model="ir.actions.server">
    <field name="model_id" ref="model_x_thing"/>
    <field name="state">code</field>
    <field name="code">model.call_other()</field>
  </record>
  <record id="report_x" model="ir.actions.report">
    <field name="name">X report</field>
    <field name="model">x.thing</field>
    <field name="report_name">app.report_x</field>
  </record>
  <record id="client_x" model="ir.actions.client">
    <field name="name">X client action</field>
    <field name="tag">app.x_client</field>
  </record>
  <record id="view_x" model="ir.ui.view">
    <field name="model">x.thing</field>
    <field name="arch" type="xml">
      <form><button name="guarded" type="object"/></form>
    </field>
  </record>
</odoo>
""",
        )
        self.write(
            "custom-addons/app/static/src/action.js",
            """
export class Action {
    async submit() {
        return this.orm.call("x.thing", "guarded", []);
    }
}
""",
        )

    def test_discovers_all_surface_families_and_auto_install_fixed_point(self):
        self._build_source_tree()
        surface = inventory.discover_surface(
            root=self.root,
            root_modules={"app"},
            country_codes={"fr"},
        )
        self.assertEqual(
            [module["name"] for module in surface["modules"]],
            ["app", "base", "bridge"],
        )
        actions = {action["key"]: action for action in surface["actions"]}
        expected = {
            "rpc:x.thing.guarded",
            "rpc:x.thing.read",
            "route:jsonrpc:POST:/x/action",
            "cron:app.cron_x",
            "server_action:app.server_x",
            "ui:app.client_x",
            "ui:app.report_x",
            "ui:app.view_x:button:object:guarded:1",
            "guard:test.guarded",
        }
        self.assertTrue(expected <= actions.keys())
        self.assertTrue(any(key.startswith("client:app:") for key in actions))
        self.assertTrue(
            any(
                action.get("sink_kind") == "raw_sql_mutation"
                for action in surface["actions"]
            ),
        )
        self.assertIn(
            "orm_write",
            actions["route:jsonrpc:POST:/x/action"].get("sinks", []),
        )
        filesystem_sinks = [
            action
            for action in surface["actions"]
            if action.get("sink_kind") == "filesystem_delete"
        ]
        self.assertEqual(len(filesystem_sinks), 2)
        self.assertTrue(
            all("filesystem_cleanup" in action["key"] for action in filesystem_sinks),
        )
        dom_action = actions["rpc:x.thing.dom_cleanup"]
        self.assertNotIn("filesystem_delete", dom_action.get("sinks", []))
        self.assertNotIn("permanent_deletion", dom_action.get("risk_flags", []))
        self.assertNotIn(
            "lock_change", actions["rpc:x.thing.toggle_lock"].get("risk_flags", []),
        )
        self.assertIn(
            "lock_change",
            actions["rpc:x.thing.change_tax_lock_date"].get("risk_flags", []),
        )
        self.assertNotIn(
            "arbitrary_execution",
            actions["rpc:x.thing.evaluate_stored_context"].get("risk_flags", []),
        )
        mock_sink = next(
            action
            for action in surface["actions"]
            if "_mock_peppol_deregister_participant" in action["key"]
        )
        self.assertNotIn("external_deletion", mock_sink.get("risk_flags", []))
        self.assertNotIn("external_registration", mock_sink.get("risk_flags", []))
        rtc_action = actions["rpc:x.thing.action_disconnect"]
        self.assertIn("external_call", rtc_action.get("sinks", []))
        self.assertNotIn("external_deletion", rtc_action.get("risk_flags", []))
        self.assertIn(
            "orm_write",
            actions["rpc:x.thing.mutate_via_private_helper"].get("sinks", []),
        )
        self.assertIn("orm_create", actions["rpc:x.thing.create"].get("sinks", []))
        self.assertIn("orm_write", actions["rpc:x.thing.write"].get("sinks", []))
        self.assertEqual(
            {"orm_write", "sudo"},
            set(actions["rpc:x.thing.elevated_write"].get("sinks", [])),
        )
        totp_sink = next(
            action
            for action in surface["actions"]
            if "_totp_rate_limit_purge" in action["key"]
        )
        self.assertNotIn("permanent_deletion", totp_sink.get("risk_flags", []))
        self.assertEqual(surface["diagnostics"], [])
        self.assertEqual(
            surface,
            inventory.discover_surface(
                root=self.root,
                root_modules={"app"},
                country_codes={"fr"},
            ),
        )

    def test_module_identity_ignores_platform_dependency_tree(self):
        self._build_source_tree()
        before = inventory.discover_surface(
            root=self.root,
            root_modules={"app"},
            country_codes={"fr"},
        )

        self.write(
            "custom-addons/app/static/worker-build/node_modules/package/index.js",
            "export const platform = 'linux';\n",
        )
        self.write(
            "custom-addons/app/static/worker-build/node_modules/package/package.json",
            '{"os": ["linux"]}\n',
        )
        after = inventory.discover_surface(
            root=self.root,
            root_modules={"app"},
            country_codes={"fr"},
        )

        self.assertEqual(before["module_set_sha256"], after["module_set_sha256"])
        self.assertEqual(before["modules"], after["modules"])

    def test_runtime_facts_replace_approximate_rpc_and_routes(self):
        self._build_source_tree()
        runtime = {
            "schema": inventory.RUNTIME_SCHEMA,
            "country_codes": ["fr"],
            "modules": [
                {"name": "app", "version": "19.0.1.0.0"},
                {"name": "base", "version": "19.0.1.0.0"},
                {"name": "bridge", "version": "19.0.1.0.0"},
            ],
            "actions": [
                self.action(
                    "rpc:x.thing.status",
                    "rpc",
                    digest=ONE,
                    model="x.thing",
                    method="status",
                    abstract=False,
                    transient=False,
                ),
            ],
        }
        surface = inventory.discover_surface(
            root=self.root,
            root_modules={"app"},
            runtime=runtime,
        )
        rpc_actions = [
            action for action in surface["actions"] if action["kind"] == "rpc"
        ]
        self.assertEqual(
            [action["key"] for action in rpc_actions], ["rpc:x.thing.status"],
        )
        self.assertEqual(rpc_actions[0]["runtime_digest"], ONE)
        self.assertFalse(rpc_actions[0]["abstract"])
        self.assertFalse(rpc_actions[0]["transient"])
        self.assertEqual(surface["discovery"], "runtime+source")


class TestPolicyValidation(ActionRiskInventoryTestCase):
    def _valid_inventory(self):
        guard = self.action("guard:test.protected", "guard")
        delete = self.action(
            "rpc:x.thing.unlink",
            "rpc",
            model="x.thing",
            method="unlink",
        )
        read = self.action(
            "rpc:x.thing.status", "rpc", model="x.thing", method="status",
        )
        reverse = self.action(
            "rpc:x.thing.restore", "rpc", model="x.thing", method="restore",
        )
        recover = self.action(
            "rpc:x.thing.archive", "rpc", model="x.thing", method="archive",
        )
        ui = self.action(
            "ui:app.view:button:object:archive:1",
            "ui",
            delegates=[recover["key"]],
        )
        sink = self.action(
            "sink:app:models.py:helper:orm_write:1", "sink", sink_kind="orm_write",
        )
        actions = [guard, delete, read, reverse, recover, ui, sink]
        entries = {
            guard["key"]: self.entry(guard, "protected"),
            delete["key"]: self.entry(
                delete,
                "protected",
                enforcement={
                    "kind": "model_operation",
                    "model": "x.thing",
                    "operation": "unlink",
                },
            ),
            read["key"]: self.entry(read, "read_only"),
            reverse["key"]: self.entry(reverse, "read_only"),
            recover["key"]: self.entry(
                recover,
                "recoverable",
                reversal_action=reverse["key"],
            ),
            ui["key"]: self.entry(ui, "transport", targets=[recover["key"]]),
            sink["key"]: self.entry(
                sink,
                "system_internal",
                reachability_proof="Only the reviewed parent implementation invokes this sink.",
            ),
        }
        return self.surface(actions), self.policy(entries)

    def test_accepts_complete_exact_policy_and_model_operation_enforcement(self):
        surface, policy = self._valid_inventory()
        self.assertEqual(inventory.validate_inventory(surface, policy), [])
        expected = hashlib.sha256(
            json.dumps(
                {"action_policy": policy, "action_surface": surface},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode(),
        ).hexdigest()
        self.assertEqual(inventory.qualified_policy_digest(surface, policy), expected)

    def test_accepts_compact_groups_with_exact_digest_and_overrides(self):
        surface, object_policy = self._valid_inventory()
        entries = object_policy["actions"]
        read_keys = sorted(
            key
            for key, entry in entries.items()
            if entry["classification"] == "read_only"
        )
        common = entries[read_keys[0]]
        grouped_policy = self.policy(
            [
                {
                    "id": "read-only-model-queries",
                    "action_keys": read_keys,
                    "classification": "read_only",
                    "domain": common["domain"],
                    "consequence": common["consequence"],
                    "rationale": common["rationale"],
                    "evidence_id": common["evidence_id"],
                    "reviewed_digests": {
                        key: entries[key]["reviewed_digest"] for key in read_keys
                    },
                },
                *[
                    {
                        "id": f"single-{index}",
                        "action_keys": [key],
                        "classification": entry["classification"],
                        "domain": entry["domain"],
                        "consequence": entry["consequence"],
                        "rationale": entry["rationale"],
                        "evidence_id": entry["evidence_id"],
                        "reviewed_digests": {key: entry["reviewed_digest"]},
                        "overrides": {
                            key: {
                                item_key: item_value
                                for item_key, item_value in entry.items()
                                if item_key
                                not in {
                                    "classification",
                                    "domain",
                                    "consequence",
                                    "rationale",
                                    "evidence_id",
                                    "reviewed_digest",
                                }
                            },
                        },
                    }
                    for index, (key, entry) in enumerate(entries.items())
                    if key not in read_keys
                ],
            ],
        )
        self.assertEqual(inventory.validate_inventory(surface, grouped_policy), [])

    def test_reports_completeness_staleness_digest_and_evidence_failures(self):
        surface, policy = self._valid_inventory()
        missing_key = next(iter(policy["actions"]))
        policy["actions"].pop(missing_key)
        policy["actions"]["rpc:stale.entry"] = {
            "classification": "read_only",
            "domain": "test",
            "consequence": "none",
            "rationale": "none",
            "evidence_id": "missing",
            "reviewed_digest": ZERO,
        }
        changed_key = next(iter(policy["actions"]))
        policy["actions"][changed_key]["reviewed_digest"] = ONE
        policy["actions"][changed_key]["rationale"] = ""
        errors = inventory.validate_inventory(surface, policy)
        self.assertTrue(
            any(error == f"Unclassified action: {missing_key}" for error in errors),
        )
        self.assertTrue(
            any("Stale policy action: rpc:stale.entry" in error for error in errors),
        )
        self.assertTrue(
            any("Changed action requires review" in error for error in errors),
        )
        self.assertTrue(
            any("requires non-empty rationale" in error for error in errors),
        )

    def test_rejects_missing_automated_evidence_file(self):
        action = self.action("rpc:x.status", "rpc")
        policy = self.policy(
            {action["key"]: self.entry(action, "read_only")},
        )
        policy["evidence_families"]["test-contract"]["tests"] = [
            "scripts/tests/does_not_exist.py::TestMissing.test_missing",
        ]
        errors = inventory.validate_inventory(self.surface([action]), policy)
        self.assertTrue(
            any("references missing test" in error for error in errors),
            errors,
        )

    def test_reports_each_class_contract_and_mandatory_risk(self):
        actions = [
            self.action("rpc:x.read", "rpc", sinks=["orm_write"]),
            self.action("rpc:x.archive", "rpc"),
            self.action("rpc:x.protected", "rpc"),
            self.action("ui:x.transport", "ui"),
            self.action("rpc:x.internal", "rpc"),
            self.action(
                "rpc:x.install",
                "rpc",
                risk_flags=["module_lifecycle"],
            ),
        ]
        classifications = [
            "read_only",
            "recoverable",
            "protected",
            "transport",
            "system_internal",
            "recoverable",
        ]
        entries = {
            action["key"]: self.entry(action, classification)
            for action, classification in zip(actions, classifications, strict=True)
        }
        errors = inventory.validate_inventory(
            self.surface(actions), self.policy(entries),
        )
        expected_fragments = (
            "Read-only action",
            "requires an exact reversal_action",
            "requires guard_key or enforcement",
            "requires exact targets",
            "requires non-empty reachability_proof",
            "cannot be system_internal",
            "must be protected",
        )
        for fragment in expected_fragments:
            self.assertTrue(any(fragment in error for error in errors), fragment)

    def test_allows_mandatory_risk_on_internal_sink_but_not_public_action(self):
        internal = self.action(
            "sink:app:models.py:helper:orm_unlink:1",
            "sink",
            sink_kind="orm_unlink",
            risk_flags=["permanent_deletion"],
        )
        public = self.action(
            "rpc:x.thing.permanently_delete",
            "rpc",
            risk_flags=["permanent_deletion"],
        )
        surface = self.surface([internal, public])
        policy = self.policy(
            {
                internal["key"]: self.entry(
                    internal,
                    "system_internal",
                    reachability_proof=(
                        "The sink is reachable only through its separately reviewed "
                        "public parent action."
                    ),
                ),
                public["key"]: self.entry(
                    public,
                    "system_internal",
                    reachability_proof="Incorrectly treated as internal.",
                ),
            },
        )
        errors = inventory.validate_inventory(surface, policy)
        self.assertFalse(
            any(internal["key"] in error for error in errors),
            errors,
        )
        self.assertTrue(
            any(
                public["key"] in error and "cannot be system_internal" in error
                for error in errors
            ),
            errors,
        )
        self.assertTrue(
            any(
                public["key"] in error and "must be protected" in error
                for error in errors
            ),
            errors,
        )

    def test_reviewed_fixed_code_execution_can_be_operational(self):
        action = self.action(
            "server_action:project.action_server_share_project",
            "server_action",
            risk_flags=["arbitrary_execution"],
        )
        surface = self.surface([action])
        policy = self.policy(
            {
                action["key"]: self.entry(action, "operational"),
            },
        )

        self.assertEqual(inventory.validate_inventory(surface, policy), [])

    def test_rejects_wrong_and_duplicate_model_operation_enforcement(self):
        action = self.action(
            "rpc:x.thing.unlink", "rpc", model="x.thing", method="unlink",
        )
        duplicate = self.action("guard:duplicate", "guard")
        surface = self.surface([action, duplicate])
        enforcement = {
            "kind": "model_operation",
            "model": "x.thing",
            "operation": "unlink",
        }
        policy = self.policy(
            {
                action["key"]: self.entry(action, "protected", enforcement=enforcement),
                duplicate["key"]: self.entry(
                    duplicate,
                    "protected",
                    enforcement=enforcement,
                ),
            },
        )
        errors = inventory.validate_inventory(surface, policy)
        self.assertTrue(
            any("must be declared on rpc:x.thing.unlink" in error for error in errors),
        )
        self.assertTrue(any("is duplicated" in error for error in errors))

    def test_grouped_policy_rejects_ambiguity_and_incomplete_digest_map(self):
        action = self.action("rpc:x.status", "rpc")
        surface = self.surface([action])
        common = {
            "classification": "read_only",
            "domain": "test",
            "consequence": "none",
            "rationale": "query",
            "evidence_id": "test-contract",
        }
        policy = self.policy(
            [
                {
                    **common,
                    "id": "one",
                    "action_keys": [action["key"]],
                    "reviewed_digests": {},
                },
                {
                    **common,
                    "id": "two",
                    "action_keys": [action["key"]],
                    "reviewed_digests": {action["key"]: ZERO},
                },
            ],
        )
        errors = inventory.validate_inventory(surface, policy)
        self.assertTrue(
            any("reviewed_digests must exactly match" in error for error in errors),
        )
        self.assertTrue(
            any("more than one policy classification" in error for error in errors),
        )

    def test_duplicate_json_keys_are_rejected(self):
        path = self.write("duplicate.json", '{"actions": {}, "actions": {}}')
        with self.assertRaises(inventory.DuplicateKeyError):
            inventory.load_json(path)

    def test_compiles_exact_protected_runtime_policy_and_rejects_drift(self):
        surface, policy = self._valid_inventory()
        server_action = self.action(
            "server_action:test.fixed_operation",
            "server_action",
        )
        surface["actions"].append(server_action)
        surface["actions"].sort(key=lambda action: action["key"])
        surface["surface_sha256"] = inventory.surface_digest(surface)
        policy["actions"][server_action["key"]] = self.entry(
            server_action,
            "operational",
        )
        runtime_policy = inventory.build_runtime_policy(surface, policy)
        expected_keys = sorted(
            key
            for key, entry in policy["actions"].items()
            if entry["classification"] == "protected"
        )
        self.assertEqual(
            [entry["action_key"] for entry in runtime_policy["actions"]],
            expected_keys,
        )
        self.assertEqual(
            runtime_policy["server_actions"],
            [
                {
                    "action_key": server_action["key"],
                    "classification": "operational",
                },
            ],
        )
        self.assertEqual(
            runtime_policy["qualified_policy_digest"],
            inventory.qualified_policy_digest(surface, policy),
        )
        self.assertEqual(
            runtime_policy["runtime_policy_sha256"],
            inventory.runtime_policy_digest(runtime_policy),
        )
        self.assertEqual(
            inventory.validate_runtime_policy(surface, policy, runtime_policy),
            [],
        )

        runtime_policy["actions"].pop()
        errors = inventory.validate_runtime_policy(surface, policy, runtime_policy)
        self.assertTrue(any("digest mismatch" in error for error in errors), errors)
        self.assertTrue(any("stale" in error for error in errors), errors)

    def test_tracked_runtime_policy_is_compact(self):
        runtime_policy = inventory.load_json(inventory.DEFAULT_RUNTIME_POLICY)
        self.assertLess(
            inventory.DEFAULT_RUNTIME_POLICY.stat().st_size,
            inventory.MAX_RUNTIME_POLICY_BYTES,
        )
        self.assertLessEqual(len(runtime_policy["actions"]), 1_000)
        self.assertEqual(
            runtime_policy["runtime_policy_sha256"],
            inventory.runtime_policy_digest(runtime_policy),
        )

    def test_refresh_seals_policy_and_compiles_runtime_artifact(self):
        surface, policy = self._valid_inventory()
        policy.pop("qualified_policy_digest", None)
        candidate_path = self.root / "candidate.json"
        policy_path = self.root / "policy.json"
        surface_path = self.root / "surface.json"
        runtime_path = self.root / "runtime.json"
        inventory.write_json(candidate_path, surface)
        inventory.write_json(policy_path, policy)

        result = inventory.main(
            [
                "refresh",
                "--candidate",
                str(candidate_path),
                "--surface",
                str(surface_path),
                "--policy",
                str(policy_path),
                "--runtime-policy",
                str(runtime_path),
            ],
        )
        self.assertEqual(result, 0)
        sealed_policy = inventory.load_json(policy_path)
        runtime_policy = inventory.load_json(runtime_path)
        self.assertEqual(
            sealed_policy["qualified_policy_digest"],
            inventory.qualified_policy_digest(surface, sealed_policy),
        )
        self.assertEqual(
            inventory.validate_runtime_policy(
                surface,
                sealed_policy,
                runtime_policy,
            ),
            [],
        )


class TestDrift(ActionRiskInventoryTestCase):
    def test_reports_module_and_action_add_remove_change(self):
        unchanged = self.action("rpc:x.same", "rpc")
        changed = self.action("rpc:x.changed", "rpc")
        removed = self.action("rpc:x.removed", "rpc")
        expected = self.surface([unchanged, changed, removed])
        expected["modules"] = [{"name": "old", "version": "1"}]
        candidate_changed = self.action("rpc:x.changed", "rpc", digest=ONE)
        added = self.action("rpc:x.added", "rpc")
        candidate = self.surface([unchanged, candidate_changed, added])
        candidate["modules"] = [{"name": "new", "version": "1"}]
        candidate["module_set_sha256"] = ONE
        errors = inventory.compare_surfaces(expected, candidate)
        for fragment in (
            "Installed module set changed",
            "Added installed module: new",
            "Removed installed module: old",
            "Added action requires classification: rpc:x.added",
            "Removed action leaves stale review: rpc:x.removed",
            "Changed action requires review: rpc:x.changed",
        ):
            self.assertTrue(any(fragment in error for error in errors), fragment)

    def test_check_source_is_a_supported_cli_alias(self):
        args = inventory._parser().parse_args(["check-source"])
        self.assertEqual(args.command, "check-source")

    def test_ignores_nonsemantic_source_locator_drift(self):
        expected_action = self.action("ui:app.action", "ui", xmlid="app.action")
        expected_action["sources"] = [
            {"path": "database:ir.actions.act_window:41", "line": 12},
        ]
        candidate_action = self.action("ui:app.action", "ui", xmlid="app.action")
        candidate_action["sources"] = [
            {"path": "database:ir.actions.act_window:907", "line": 99},
        ]

        self.assertEqual(
            inventory.compare_surfaces(
                self.surface([expected_action]),
                self.surface([candidate_action]),
            ),
            [],
        )

    def test_source_file_move_still_requires_review(self):
        expected_action = self.action("rpc:x.changed", "rpc")
        expected_action["sources"] = [
            {"path": "custom-addons/app/models/old.py", "line": 12},
        ]
        candidate_action = self.action("rpc:x.changed", "rpc")
        candidate_action["sources"] = [
            {"path": "custom-addons/app/models/new.py", "line": 12},
        ]

        errors = inventory.compare_surfaces(
            self.surface([expected_action]),
            self.surface([candidate_action]),
        )
        self.assertEqual(
            errors,
            ["Changed action requires review: rpc:x.changed (sources)"],
        )


if __name__ == "__main__":
    unittest.main()
