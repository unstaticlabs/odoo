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
            "modules": [],
            "actions": sorted(actions, key=lambda action: action["key"]),
            "diagnostics": [],
            "discovery": "source",
        }
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
    def test_generated_runtime_policies_do_not_change_module_identity(self):
        self.manifest("app")
        self.write("custom-addons/app/models.py", "VALUE = 1\n")
        info = inventory.module_index(self.root)["app"]
        initial = inventory._module_source_digest(info)

        for name in (
            "action_policy.json",
            "action_surface.json",
            "agent_readonly_runtime_policy.json",
            "protected_runtime_policy.json",
        ):
            self.write(f"custom-addons/app/policy/{name}", '{"generated": true}\n')
        self.assertEqual(inventory._module_source_digest(info), initial)

        self.write("custom-addons/app/models.py", "VALUE = 2\n")
        self.assertNotEqual(inventory._module_source_digest(info), initial)

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

    @property
    def display_token(self):
        return self.id

    @classmethod
    def implementation_kind(cls):
        return "thing"

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

    def deescalated_read(self):
        return self.sudo(False).read(["name"])
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
        self.assertNotIn("rpc:x.thing.display_token", actions)
        self.assertNotIn("rpc:x.thing.implementation_kind", actions)
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
        self.assertNotIn(
            "sudo",
            actions["rpc:x.thing.deescalated_read"].get("sinks", []),
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

        self.assertEqual(
            inventory.module_set_digest(before),
            inventory.module_set_digest(after),
        )
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


class TestNativeGroupingReads(ActionRiskInventoryTestCase):
    def grouping_surface(self, override="", *, helper=True):
        self.manifest("base")
        self.manifest("web", depends=("base",), origin="addons")
        self.manifest("app", depends=("web",))
        self.write("odoo/orm/models.py", """
class BaseModel:
    def _read_grouping_sets(self, domain, grouping_sets, aggregates=(), order=None):
        if len(grouping_sets) > 1:
            return self._read_grouping_sets(domain, grouping_sets[:1], aggregates, order)
        return [[(3,)]]
""" if helper else "class BaseModel: pass")
        self.write("addons/web/models.py", """
from odoo import models
class Base(models.AbstractModel):
    _inherit = 'base'
    def formatted_read_grouping_sets(self, domain, grouping_sets, aggregates=()):
        return self._read_grouping_sets(domain, grouping_sets, aggregates)
    def grouping_and_write(self):
        self.write({'name': 'changed'})
        return self._read_grouping_sets([], [[]])
    def foreign_grouping(self, other):
        return other._read_grouping_sets([], [[]])
    def set_state(self):
        return self._set_state()
    def _set_state(self):
        return self.write({'name': 'changed'})
""")
        self.write("custom-addons/app/models.py", """
from odoo import models
class Thing(models.Model):
    _name = 'x.thing'
""" + override)
        return inventory.discover_surface(root=self.root, root_modules={"app"})

    def test_native_grouping_is_an_effect_edge_not_a_setter(self):
        surface = self.grouping_surface()
        actions = {action['key']: action for action in surface['actions']}
        grouping = actions['rpc:x.thing.formatted_read_grouping_sets']
        self.assertNotIn('orm_write', grouping.get('sinks', []))
        self.assertNotIn('rpc:x.thing._read_grouping_sets', actions)
        self.assertTrue(any(source['path'] == 'odoo/orm/models.py' for source in grouping['sources']))
        self.assertFalse(any('._read_grouping_sets:orm_write:' in key for key in actions))
        self.assertIn('orm_write', actions['rpc:x.thing.grouping_and_write'].get('sinks', []))
        self.assertIn('orm_write', actions['rpc:x.thing.foreign_grouping'].get('sinks', []))
        self.assertIn('orm_write', actions['rpc:x.thing.set_state'].get('sinks', []))

    def test_inherited_grouping_override_preserves_actual_writes(self):
        surface = self.grouping_surface("""
    def _read_grouping_sets(self, domain, grouping_sets, aggregates=(), order=None):
        self._persist_grouping_change()
        return super()._read_grouping_sets(domain, grouping_sets, aggregates, order)
    def _persist_grouping_change(self):
        self.write({'name': 'changed'})
""")
        actions = {action['key']: action for action in surface['actions']}
        grouping = actions['rpc:x.thing.formatted_read_grouping_sets']
        self.assertIn('orm_write', grouping.get('sinks', []))
        policy = self.policy({grouping['key']: self.entry(grouping, 'read_only')})
        errors = inventory.validate_inventory(self.surface([grouping]), policy)
        self.assertTrue(any('Read-only action' in error for error in errors), errors)

    def test_unresolved_grouping_is_not_assumed_read_only(self):
        surface = self.grouping_surface(helper=False)
        action = next(action for action in surface['actions'] if action['key'] == 'rpc:x.thing.formatted_read_grouping_sets')
        self.assertIn('orm_write', action.get('sinks', []))

    def test_helper_body_changes_the_inherited_action_digest(self):
        first = self.grouping_surface("""
    def _read_grouping_sets(self, domain, grouping_sets, aggregates=(), order=None):
        return super()._read_grouping_sets(domain, grouping_sets, aggregates, order)
""")
        second = self.grouping_surface("""
    def _read_grouping_sets(self, domain, grouping_sets, aggregates=(), order=None):
        self.write({'name': 'changed'})
        return super()._read_grouping_sets(domain, grouping_sets, aggregates, order)
""")
        def action(surface):
            return next(action for action in surface['actions'] if action['key'] == 'rpc:x.thing.formatted_read_grouping_sets')
        self.assertNotEqual(action(first)['digest'], action(second)['digest'])


class TestGroupingRuntimeFingerprint(unittest.TestCase):
    def test_runtime_fingerprint_binds_effective_helper_overrides(self):
        # Execute only the exporter's pure functions, without its Odoo shell I/O.
        tree = ast.parse((inventory.ROOT / "scripts/odoo/action_risk_inventory.py").read_text())
        pure_names = {
            'canonical', 'digest', 'module_name', 'source_path',
            'callable_fragment', 'public_method_chain', 'grouping_helper_chain',
        }
        functions = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in pure_names], type_ignores=[])
        import inspect
        import re
        namespace = {'ast': ast, 'json': json, 'hashlib': hashlib, 'inspect': inspect, 're': re}
        exec(compile(functions, '<runtime grouping functions>', 'exec'), namespace)
        fixtures = {"__name__": "grouping_runtime_fixture"}
        exec("""
class Native:
    def formatted_read_grouping_sets(self):
        return self._read_grouping_sets()
    def status(self):
        return True
    def _read_grouping_sets(self):
        return [[(3,)]]
class Extension(Native):
    def _read_grouping_sets(self):
        return super()._read_grouping_sets()
""", fixtures)
        Native, Extension = fixtures['Native'], fixtures['Extension']
        fingerprint = lambda cls, method: namespace['digest'](namespace['public_method_chain'](cls, method))
        before = fingerprint(Extension, 'formatted_read_grouping_sets')
        status = fingerprint(Extension, 'status')
        exec("""
def mutation(self):
    self.write({'name': 'changed'})
    return super(Extension, self)._read_grouping_sets()
""", fixtures)
        Extension._read_grouping_sets = fixtures['mutation']
        self.assertNotEqual(before, fingerprint(Extension, 'formatted_read_grouping_sets'))
        self.assertEqual(status, fingerprint(Extension, 'status'))
        chain = namespace['grouping_helper_chain'](Extension)
        self.assertEqual(len(chain), 2)
        self.assertEqual(chain[-1]['qualname'], 'Native._read_grouping_sets')


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
        # The compiled artifact records no digest of itself or of the review it
        # came from, so an unrelated reseal leaves it byte-identical.
        for field in inventory.RUNTIME_POLICY_DERIVED_FIELDS:
            self.assertNotIn(field, runtime_policy)
        self.assertEqual(
            inventory.validate_runtime_policy(surface, policy, runtime_policy),
            [],
        )

        runtime_policy["actions"].pop()
        errors = inventory.validate_runtime_policy(surface, policy, runtime_policy)
        self.assertTrue(any("stale" in error for error in errors), errors)

        stale = inventory.build_runtime_policy(surface, policy)
        stale["runtime_policy_sha256"] = ZERO
        errors = inventory.validate_runtime_policy(surface, policy, stale)
        self.assertTrue(
            any("derived field" in error for error in errors),
            errors,
        )

    def test_tracked_runtime_policy_is_compact(self):
        runtime_policy = inventory.load_json(inventory.DEFAULT_RUNTIME_POLICY)
        self.assertLess(
            inventory.DEFAULT_RUNTIME_POLICY.stat().st_size,
            inventory.MAX_RUNTIME_POLICY_BYTES,
        )
        self.assertLessEqual(len(runtime_policy["actions"]), 1_000)
        for field in inventory.RUNTIME_POLICY_DERIVED_FIELDS:
            self.assertNotIn(field, runtime_policy)

    def test_reviewed_report_navigation_is_available_without_a_second_name_list(self):
        action = self.action(
            "rpc:rebuild.account.report.export.wizard.report_client_open_sources",
            "rpc",
        )
        policy = self.policy({action["key"]: self.entry(action, "read_only")})

        compiled = inventory.build_agent_readonly_runtime_policy(
            self.surface([action]), policy,
        )

        self.assertIn(action["key"], compiled["read_only_actions"])
        self.assertEqual(compiled["write_actions"], [])
        self.assertEqual(compiled["collaboration_actions"], [])

    def test_ephemeral_report_dispatch_preserves_review_and_exact_model(self):
        for method in ("report_client_load", "report_client_export", "report_client_toggle_group"):
            for classification in ("read_only", "recoverable", "operational", "protected", "system_internal", "transport"):
                for model in ("rebuild.account.report.export.wizard", "x.other.report"):
                    action = self.action(f"rpc:{model}.{method}", "rpc")
                    compiled = inventory.build_agent_readonly_runtime_policy(
                        self.surface([action]),
                        self.policy({action["key"]: self.entry(action, classification)}),
                    )
                    exposed = action["key"] in compiled["collaboration_actions"]
                    with self.subTest(method=method, classification=classification, model=model):
                        self.assertEqual(
                            exposed,
                            model == "rebuild.account.report.export.wizard"
                            and classification in {"read_only", "recoverable", "operational"},
                        )

    def test_reviewed_classification_survives_method_renaming_and_input_order(self):
        # A reviewed read-only action does not need a second name-based grant.
        # The other five existing classifications retain their meaning.
        expected = {
            "read_only": "read_only_actions",
            "operational": "write_actions",
            "recoverable": "write_actions",
            "protected": None,
            "system_internal": None,
            "transport": None,
        }
        for method in ("search_read", "report_client_open_sources", "action_open_source"):
            actions = [
                self.action(f"rpc:x.{classification}.{method}", "rpc")
                for classification in expected
            ]
            for reverse in (False, True):
                ordered = list(reversed(actions)) if reverse else actions
                entries = {
                    action["key"]: self.entry(action, action["key"].split(".")[1])
                    for action in ordered
                }
                compiled = inventory.build_agent_readonly_runtime_policy(
                    self.surface(ordered), self.policy(entries),
                )
                for classification, destination in expected.items():
                    key = f"rpc:x.{classification}.{method}"
                    with self.subTest(method=method, reverse=reverse, classification=classification):
                        containing = [name for name, keys in compiled.items() if isinstance(keys, list) and key in keys]
                        self.assertEqual(containing, [destination] if destination else [])

    def test_compiles_exact_agent_readonly_and_collaboration_allowlists(self):
        read_action = self.action("rpc:res.partner.search_read", "rpc")
        navigation_read_action = self.action("rpc:res.partner.action_open_sources", "rpc")
        collaboration_action = self.action("rpc:project.task.message_post", "rpc")
        write_action = self.action("rpc:project.task.write", "rpc")
        surface = self.surface(
            [read_action, navigation_read_action, collaboration_action, write_action],
        )
        policy = self.policy(
            {
                read_action["key"]: self.entry(read_action, "read_only"),
                navigation_read_action["key"]: self.entry(
                    navigation_read_action,
                    "read_only",
                ),
                collaboration_action["key"]: self.entry(
                    collaboration_action,
                    "recoverable",
                ),
                write_action["key"]: self.entry(write_action, "operational"),
            },
        )

        runtime_policy = inventory.build_agent_readonly_runtime_policy(surface, policy)

        self.assertEqual(runtime_policy["read_only_actions"], sorted([read_action["key"], navigation_read_action["key"]]))
        self.assertEqual(
            runtime_policy["collaboration_actions"],
            [collaboration_action["key"]],
        )
        self.assertEqual(runtime_policy["write_actions"], [write_action["key"]])
        self.assertEqual(
            inventory.validate_agent_readonly_runtime_policy(
                surface,
                policy,
                runtime_policy,
            ),
            [],
        )
        for field in inventory.RUNTIME_POLICY_DERIVED_FIELDS:
            self.assertNotIn(field, runtime_policy)
        runtime_policy["read_only_actions"].append(write_action["key"])
        errors = inventory.validate_agent_readonly_runtime_policy(
            surface,
            policy,
            runtime_policy,
        )
        self.assertTrue(any("stale" in error for error in errors), errors)

        stale = inventory.build_agent_readonly_runtime_policy(surface, policy)
        stale["qualified_policy_digest"] = ZERO
        errors = inventory.validate_agent_readonly_runtime_policy(surface, policy, stale)
        self.assertTrue(any("derived field" in error for error in errors), errors)

    def test_mutating_classifications_do_not_become_readonly_by_method_name(self):
        classifications = {
            "action_archive": "operational", "action_unarchive": "recoverable",
            "activity_feedback": "operational", "activity_unlink": "recoverable",
            "button_immediate_install": "protected", "import_data": "protected",
            "run": "system_internal", "web_read": "operational", "write": "operational",
        }
        actions = [self.action(f"rpc:x.thing.{name}", "rpc") for name in classifications]
        policy = self.policy({
            action["key"]: self.entry(action, classifications[action["key"].rsplit(".", 1)[-1]])
            for action in actions
        })
        compiled = inventory.build_agent_readonly_runtime_policy(self.surface(actions), policy)
        self.assertEqual(compiled["read_only_actions"], [])
        self.assertEqual(compiled["collaboration_actions"], [])
        self.assertEqual(compiled["write_actions"], sorted(
            f"rpc:x.thing.{name}" for name, classification in classifications.items()
            if classification in {"operational", "recoverable"}
        ))

    def test_reviewed_readonly_collaboration_keeps_its_existing_precedence(self):
        actions = [self.action("rpc:stock.landed.cost.message_subscribe", "rpc")]
        action = actions[0]
        policy = self.policy({action["key"]: self.entry(action, "read_only")})
        compiled = inventory.build_agent_readonly_runtime_policy(self.surface(actions), policy)
        self.assertEqual(compiled["read_only_actions"], [])
        self.assertEqual(compiled["write_actions"], [])
        self.assertEqual(compiled["collaboration_actions"], [action["key"]])

    def test_native_helper_access_follows_its_reviewed_classification(self):
        action = self.action("rpc:usl.agent.current_identity", "rpc")
        surface = self.surface([action])

        runtime_policy = inventory.build_agent_readonly_runtime_policy(
            surface,
            self.policy({action["key"]: self.entry(action, "read_only")}),
        )
        self.assertEqual(runtime_policy["read_only_actions"], [action["key"]])

        runtime_policy = inventory.build_agent_readonly_runtime_policy(
            surface,
            self.policy({action["key"]: self.entry(action, "operational")}),
        )
        self.assertEqual(runtime_policy["read_only_actions"], [])

    def test_vendor_bill_merge_navigation_reads_do_not_allow_merge_mutations(self):
        keys = [
            "rpc:account.move.action_open_vendor_bill_merge_originals",
            "rpc:account.move.action_open_vendor_bill_merge_target",
            "rpc:account.move.action_merge_vendor_bills",
            "rpc:account.move.action_choose_vendor_bill_merge_winner",
        ]
        actions = [self.action(key, "rpc") for key in keys]
        policy = self.policy({
            action["key"]: self.entry(action, "read_only" if index < 2 else "recoverable")
            for index, action in enumerate(actions)
        })
        compiled = inventory.build_agent_readonly_runtime_policy(self.surface(actions), policy)
        self.assertEqual(compiled["read_only_actions"], sorted(keys[:2]))
        self.assertEqual(compiled["write_actions"], sorted(keys[2:]))

    def test_reconciliation_review_read_does_not_admit_annotation_writes(self):
        keys = [
            "rpc:account.reconciliation.review.get_review_status",
            "rpc:account.reconciliation.review.mark_reviewed",
            "rpc:account.reconciliation.review.dismiss_variance",
        ]
        actions = [self.action(key, "rpc") for key in keys]
        # The reviewed classification governs access; a second method-name
        # allowlist must not override native read-only capabilities.
        policy = self.policy({action["key"]: self.entry(action, "read_only" if index == 0 else "operational")
                              for index, action in enumerate(actions)})
        compiled = inventory.build_agent_readonly_runtime_policy(self.surface(actions), policy)
        self.assertEqual(compiled["read_only_actions"], keys[:1])
        self.assertEqual(compiled["write_actions"], sorted(keys[1:]))
        failures = []
        reviewed = inventory.normalize_policy_actions(
            inventory.load_json(inventory.DEFAULT_POLICY), failures,
        )
        self.assertEqual(failures, [])
        self.assertEqual(reviewed[keys[0]]["classification"], "read_only")
        for key in keys[1:]:
            self.assertEqual(reviewed[key]["classification"], "operational")

    def test_tracked_agent_readonly_runtime_policy_is_bounded(self):
        runtime_policy = inventory.load_json(
            inventory.DEFAULT_AGENT_READONLY_RUNTIME_POLICY,
        )
        self.assertLess(
            inventory.DEFAULT_AGENT_READONLY_RUNTIME_POLICY.stat().st_size,
            inventory.MAX_AGENT_READONLY_RUNTIME_POLICY_BYTES,
        )
        for field in inventory.RUNTIME_POLICY_DERIVED_FIELDS:
            self.assertNotIn(field, runtime_policy)
        failures = []
        reviewed = inventory.normalize_policy_actions(
            inventory.load_json(inventory.DEFAULT_POLICY), failures,
        )
        self.assertEqual(failures, [])
        expected = {
            key for key, entry in reviewed.items()
            if key.startswith("rpc:") and entry["classification"] == "read_only"
            and key.rsplit(".", 1)[-1] not in inventory.AGENT_COLLABORATION_METHODS
            and key not in inventory.AGENT_EPHEMERAL_REPORT_ACTIONS
        }
        self.assertEqual(set(runtime_policy["read_only_actions"]), expected)
        for name in ("collaboration_actions", "write_actions"):
            self.assertTrue(expected.isdisjoint(runtime_policy[name]))
        self.assertTrue(all(reviewed[key]["classification"] == "read_only" for key in expected))

    def test_refresh_seals_policy_and_compiles_runtime_artifact(self):
        surface, policy = self._valid_inventory()
        policy.pop("qualified_policy_digest", None)
        candidate_path = self.root / "candidate.json"
        policy_path = self.root / "policy.json"
        surface_path = self.root / "surface.json"
        runtime_path = self.root / "runtime.json"
        agent_runtime_path = self.root / "agent-runtime.json"
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
                "--agent-readonly-runtime-policy",
                str(agent_runtime_path),
            ],
        )
        self.assertEqual(result, 0)
        sealed_policy = inventory.load_json(policy_path)
        runtime_policy = inventory.load_json(runtime_path)
        agent_runtime_policy = inventory.load_json(agent_runtime_path)
        sealed_surface = inventory.load_json(surface_path)
        for field in inventory.POLICY_DERIVED_FIELDS:
            self.assertNotIn(field, sealed_policy)
        for field in inventory.SURFACE_DERIVED_FIELDS:
            self.assertNotIn(field, sealed_surface)
        self.assertEqual(
            inventory.validate_runtime_policy(
                surface,
                sealed_policy,
                runtime_policy,
            ),
            [],
        )
        self.assertEqual(
            inventory.validate_agent_readonly_runtime_policy(
                surface,
                sealed_policy,
                agent_runtime_policy,
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

    def test_names_the_database_when_only_a_database_defined_digest_moves(self):
        """The author is otherwise sent to reseal an action nobody changed."""
        expected_action = self.action(
            "ui:base.ir_cron_act", "ui", xmlid="base.ir_cron_act",
        )
        expected_action["sources"] = [
            {"path": "database:ir.actions.act_window:5"},
            {"path": "odoo/addons/base/views/ir_cron_views.xml", "line": 86},
        ]
        candidate_action = self.action(
            "ui:base.ir_cron_act", "ui", xmlid="base.ir_cron_act", digest=ONE,
        )
        candidate_action["sources"] = list(expected_action["sources"])

        errors = inventory.compare_surfaces(
            self.surface([expected_action]),
            self.surface([candidate_action]),
        )

        self.assertIn(
            "Changed action requires review: ui:base.ir_cron_act (digest)",
            errors,
        )
        diagnosis = [error for error in errors if "row id" in error]
        self.assertEqual(len(diagnosis), 1, errors)
        self.assertIn("ui:base.ir_cron_act", diagnosis[0])
        self.assertIn("raw database row id", diagnosis[0])
        self.assertIn("make action-risk-db", diagnosis[0])

    def test_stays_silent_when_a_source_defined_digest_moves(self):
        """Only a database record can move without the checkout moving."""
        expected_action = self.action("rpc:x.changed", "rpc")
        expected_action["sources"] = [
            {"path": "custom-addons/app/models/thing.py", "line": 12},
        ]
        candidate_action = self.action("rpc:x.changed", "rpc", digest=ONE)
        candidate_action["sources"] = list(expected_action["sources"])

        errors = inventory.compare_surfaces(
            self.surface([expected_action]),
            self.surface([candidate_action]),
        )

        self.assertEqual(
            errors,
            ["Changed action requires review: rpc:x.changed (digest)"],
        )

    def test_carry_moved_sinks_renames_only_identical_relocated_helpers(self):
        moved = self.action(
            "sink:app:custom-addons/app/models/old.py:app.thing._helper:sudo:1",
            "sink",
            digest=ONE,
        )
        changed = self.action(
            "sink:app:custom-addons/app/models/old.py:app.thing._edited:orm_write:1",
            "sink",
            digest=ONE,
        )
        kept = self.action("rpc:app.thing.action_run", "rpc")
        expected = self.surface([moved, changed, kept])
        relocated = self.action(
            "sink:app:custom-addons/app/models/new.py:app.thing._helper:sudo:1",
            "sink",
            digest=ONE,
        )
        rewritten = self.action(
            "sink:app:custom-addons/app/models/new.py:app.thing._edited:orm_write:1",
            "sink",
            digest=ZERO,
        )
        candidate = self.surface([relocated, rewritten, kept])
        policy = self.policy(
            [
                {
                    "id": "internal",
                    "classification": "system_internal",
                    "domain": "test",
                    "consequence": "Test consequence.",
                    "rationale": "Test rationale.",
                    "evidence_id": "test-contract",
                    "action_keys": [moved["key"], changed["key"]],
                    "reviewed_digests": {moved["key"]: ONE, changed["key"]: ONE},
                    "overrides": {moved["key"]: {"reachability_proof": "internal"}},
                },
                {
                    "id": "public",
                    "classification": "operational",
                    "domain": "test",
                    "consequence": "Test consequence.",
                    "rationale": "Test rationale.",
                    "evidence_id": "test-contract",
                    "action_keys": [kept["key"]],
                    "reviewed_digests": {kept["key"]: ZERO},
                },
            ],
        )

        renames, unmatched = inventory.carry_moved_sinks(expected, candidate, policy)

        self.assertEqual(renames, {moved["key"]: relocated["key"]})
        self.assertEqual(
            unmatched,
            [f"Removed sink has no single moved successor: {changed['key']}"],
        )
        internal = policy["actions"][0]
        self.assertEqual(internal["action_keys"], [relocated["key"], changed["key"]])
        self.assertEqual(internal["reviewed_digests"], {relocated["key"]: ONE, changed["key"]: ONE})
        self.assertEqual(internal["overrides"], {relocated["key"]: {"reachability_proof": "internal"}})
        self.assertEqual(policy["actions"][1]["action_keys"], [kept["key"]])
        normalized = inventory.normalize_policy_actions(policy, [])
        self.assertEqual(normalized[relocated["key"]]["reviewed_digest"], ONE)
        self.assertEqual(
            inventory.compare_surfaces(
                expected,
                candidate,
                action_kinds={"sink"},
            ),
            [
                f"Added action requires classification: {rewritten['key']}",
                f"Added action requires classification: {relocated['key']}",
                f"Removed action leaves stale review: {changed['key']}",
                f"Removed action leaves stale review: {moved['key']}",
            ],
        )

    def test_carry_moved_sinks_is_a_cli_subcommand(self):
        args = inventory._parser().parse_args(
            ["carry-moved-sinks", "--candidate", "c.json", "--module", "app"],
        )
        self.assertEqual((args.command, args.module), ("carry-moved-sinks", ["app"]))


if __name__ == "__main__":
    unittest.main()
