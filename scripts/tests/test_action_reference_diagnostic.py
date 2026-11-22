"""Portable reference comparison for operator diagnostics on a restored registry."""

import ast
import copy
import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from scripts import action_risk_inventory as inventory


def shell_functions():
    # Odoo injects env in the shell. Load the same functions without opening a registry.
    path = Path(__file__).resolve().parents[1] / "odoo" / "action_risk_inventory.py"
    parsed = ast.parse(path.read_text())
    parsed.body = [node for node in parsed.body if isinstance(node, ast.FunctionDef)]
    scope = {"ast": ast, "json": json, "hashlib": hashlib}
    exec(compile(parsed, str(path), "exec"), scope)
    return scope


class Record:
    def __init__(self, model, record_id, **fields):
        self._name = model
        self.id = record_id
        self._fields = {key: SimpleNamespace(type="char") for key in fields}
        self.__dict__.update(fields)

    def exists(self):
        return self

    def __getitem__(self, key):
        return getattr(self, key)


class SyntheticRegistry:
    board_ref = "usl_feedback.project_product_feedback"
    windows = ("usl_feedback.action_feedback_collaborator", "usl_feedback.action_feedback_maintainer")

    def __init__(self, board_id=3):
        self.shell = shell_functions()
        self.board = Record("project.project", board_id, usl_feedback_project=True)
        self.operator = Record("res.users", 10, login="synthetic-private-login")
        self.server = Record("ir.actions.server", 15, name="Synthetic PDP", type="ir.actions.server")
        self.cron = Record("ir.cron", 21, ir_actions_server_id=self.server, user_id=self.operator)
        self.xmlids = {
            self.board_ref: self.board,
            "base.user_root": self.operator,
            inventory.REFERENCE_CRON: self.cron,
            inventory.REFERENCE_SERVER: self.server,
        }
        for index, xmlid in enumerate(self.windows):
            self.xmlids[xmlid] = Record(
                "ir.actions.act_window", 30 + index, name="Product Feedback",
                context=repr({"default_project_id": board_id, "create": bool(index)}), res_model="project.task",
            )
        self.metadata = Mock()
        self.metadata.sudo.return_value.search_count.side_effect = lambda domain: domain in [
            [("module", "=", "l10n_fr_pdp"), ("name", "=", xmlid.split(".", 1)[1]),
             ("model", "=", row._name), ("res_id", "=", row.id), ("noupdate", "=", True)]
            for xmlid, row in ((inventory.REFERENCE_CRON, self.cron), (inventory.REFERENCE_SERVER, self.server))
        ]
        env = type("Registry", (), {
            "ref": lambda _, name, raise_if_not_found: self.xmlids.get(name),
            "__getitem__": lambda _, name: {"ir.model.data": self.metadata}[name],
        })
        self.shell["env"] = env()
        self.module = {"name": "sale_project_stock_account", "version": "saas~19.3.1.0"}
        self.other_action = {"key": "ui:other.action", "digest": "a" * 64}
        self.cron_fields = {"interval_number": 8, "code": "model.run()", "model_id": "test.model"}
        self.server_fields = {"code": "model.run()", "model_id": "test.model", "state": "code"}

    def capture(self):
        digest = self.shell["digest"]
        values = self.shell["ui_action_values"]
        actions = [
            {"key": f"ui:{xmlid}", "digest": digest(values(self.xmlids[xmlid]))}
            for xmlid in (*self.windows, inventory.REFERENCE_SERVER)
        ]
        actions.extend([
            {"key": f"cron:{inventory.REFERENCE_CRON}", "digest": digest(self.cron_fields)},
            {"key": f"server_action:{inventory.REFERENCE_SERVER}", "digest": digest(self.server_fields)},
            self.other_action,
        ])
        return self.shell["reference_diagnostic"]({"modules": [self.module], "actions": actions})


class ActionReferenceDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.registry = SyntheticRegistry()
        self.baseline = self.registry.capture()

    def test_local_ids_and_retained_name_compare_across_registries(self):
        restored = SyntheticRegistry(board_id=90)
        restored.server.name = "Historical label"
        restored.module["version"] = "1.0"
        candidate = restored.capture()
        notes, errors = inventory.compare_reference_diagnostics(self.baseline, candidate)
        self.assertEqual(errors, [])
        self.assertEqual(len(notes), 3)
        self.assertNotIn(restored.operator.login, json.dumps(candidate))
        self.assertNotIn("Historical label", json.dumps(candidate))

    def test_wrong_project_model_or_unmarked_board_requires_review(self):
        for attribute, value in (("_name", "res.partner"), ("usl_feedback_project", False)):
            with self.subTest(attribute=attribute):
                registry = SyntheticRegistry()
                setattr(registry.board, attribute, value)
                self.assertTrue(registry.capture()["issues"])

    def test_unresolved_or_ambiguous_context_requires_review(self):
        registry = self.registry
        for source in ("{'default_project_id': 91}", "{'default_project_id': True}",
                       "{'default_project_id': ref('x')}", "{'default_project_id': 3, 'enable': active_id}",
                       "{'default_project_id':3, 'default_project_id':3}", "{**other}", "False", None):
            with self.subTest(source=source):
                registry.xmlids[registry.windows[0]].context = source
                candidate = registry.capture()
                self.assertTrue(candidate["issues"])
                self.assertTrue(inventory.compare_reference_diagnostics(candidate, candidate)[1])

    def test_other_context_values_and_action_model_stay_strict(self):
        entry = self.registry.xmlids[self.registry.windows[0]]
        for field, value in (("context", "{'default_project_id':3,'create':True}"),
                             ("name", "Changed title"), ("res_model", "other.model")):
            with self.subTest(field=field):
                old = getattr(entry, field)
                setattr(entry, field, value)
                notes, errors = inventory.compare_reference_diagnostics(self.baseline, self.registry.capture())
                self.assertFalse(notes)
                self.assertEqual(errors, ["Action changed: ui:" + self.registry.windows[0]])
                setattr(entry, field, old)

    def test_real_code_target_and_schedule_differences_are_visible(self):
        for field, value in (("code", "model.other()"), ("model_id", "another.model"), ("interval_number", 2)):
            with self.subTest(field=field):
                registry = SyntheticRegistry()
                registry.cron_fields[field] = value
                if field != "interval_number":
                    registry.server_fields[field] = value
                errors = inventory.compare_reference_diagnostics(self.baseline, registry.capture())[1]
                self.assertIn("Action changed: cron:" + inventory.REFERENCE_CRON, errors)
                if field != "interval_number":
                    self.assertIn("Action changed: server_action:" + inventory.REFERENCE_SERVER, errors)

    def test_principal_or_link_drift_and_missing_noupdate_require_review(self):
        for change in ("principal", "server", "metadata"):
            with self.subTest(change=change):
                registry = SyntheticRegistry()
                if change == "principal":
                    registry.cron.user_id = Record("res.users", 4, login="other-private-login")
                elif change == "server":
                    registry.cron.ir_actions_server_id = Record("ir.actions.server", 999)
                else:
                    registry.metadata.sudo.return_value.search_count.side_effect = lambda domain: False
                receipt = registry.capture()
                self.assertTrue(inventory.compare_reference_diagnostics(self.baseline, receipt)[1])
                self.assertTrue(receipt["issues"])
                self.assertNotIn("private-login", json.dumps(receipt))

    def test_module_and_unrelated_action_changes_are_visible(self):
        self.registry.module["version"] = "2.0"
        self.registry.other_action["digest"] = "b" * 64
        self.assertEqual(inventory.compare_reference_diagnostics(self.baseline, self.registry.capture())[1],
                         ["Installed module cohort changed.", "Other runtime actions changed."])

    def test_partial_or_inconsistent_receipts_fail_even_if_both_match(self):
        for field, value in (("schema", "v0"), ("references", {}), ("issues", None),
                             ("other_action_count", False), ("module_count", 0), ("modules_sha256", "bad")):
            with self.subTest(field=field):
                broken = dict(self.baseline, **{field: value})
                self.assertTrue(inventory.compare_reference_diagnostics(broken, broken)[1])
        broken = copy.deepcopy(self.baseline)
        broken["references"]["cron:" + inventory.REFERENCE_CRON]["meaning"] = "f" * 64
        self.assertTrue(inventory.compare_reference_diagnostics(broken, broken)[1])

    def test_offline_cli_distinguishes_exact_equivalent_and_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            expected = Path(tmp) / "clean.json"
            other = expected.with_name("restored.json")
            expected.write_text(json.dumps(self.baseline))
            command = ["compare-reference-diagnostics", "--expected", str(expected), "--candidate", str(other)]
            for receipt, status, message in (
                (self.baseline, 0, "identical"), (SyntheticRegistry(47).capture(), 0, "equivalent"),
                (dict(self.baseline, issues=["Private input must not be echoed"]), 1, "candidate observation"),
            ):
                other.write_text(json.dumps(receipt))
                with redirect_stdout(StringIO()) as out, redirect_stderr(StringIO()) as err:
                    self.assertEqual(inventory.main(command), status)
                output = out.getvalue() + err.getvalue()
                self.assertIn(message, output)
                self.assertNotIn("Private input", output)
