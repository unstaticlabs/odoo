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

    def _get_external_ids(self):
        xmlid = getattr(self, "xmlid", None)
        return {self.id: [xmlid] if xmlid else []}

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
        self.rows = {}
        diagnostic_views = []
        for index, suffix in enumerate(("board_view", "list_view", "search_view")):
            view = self.add_row("ir.ui.view", 100 + index, f"usl_feedback.{suffix}")
            diagnostic_views.append(view)
        for index, xmlid in enumerate(self.windows):
            self.xmlids[xmlid] = Record(
                "ir.actions.act_window", 30 + index, name="Product Feedback",
                context=repr({"default_project_id": board_id, "create": bool(index)}), res_model="project.task",
                res_id=False, view_mode="kanban,list", mobile_view_mode="kanban", filter=False, cache=True,
                limit=80, usage=False, help=False, explanation=False, view_id=None,
                search_view_id=diagnostic_views[-1], group_ids=[],
                view_ids=[
                    SimpleNamespace(sequence=i, id=i + 1, view_mode=mode, view_id=diagnostic_views[i], multi=False)
                    for i, mode in enumerate(("kanban", "list"))
                ],
                views=[(diagnostic_views[0].id, "kanban"), (diagnostic_views[1].id, "list")],
            )
        self.metadata = Mock()
        self.metadata.sudo.return_value.search_count.side_effect = lambda domain: domain in [
            [("module", "=", "l10n_fr_pdp"), ("name", "=", xmlid.split(".", 1)[1]),
             ("model", "=", row._name), ("res_id", "=", row.id), ("noupdate", "=", True)]
            for xmlid, row in ((inventory.REFERENCE_CRON, self.cron), (inventory.REFERENCE_SERVER, self.server))
        ]
        self.embedded = Mock()
        self.embedded.sudo.return_value.search_count.return_value = 0
        def table(name):
            if name == "ir.model.data":
                return self.metadata
            if name == "ir.embedded.actions":
                return self.embedded
            return SimpleNamespace(browse=lambda row_id: self.rows.get((name, row_id)))
        env = type("Registry", (), {
            "ref": lambda _, name, raise_if_not_found: self.xmlids.get(name),
            "__getitem__": lambda _, name: table(name),
            "__contains__": lambda _, name: name in {"ir.ui.view", "project.task"},
        })
        self.shell["env"] = env()
        self.module = {"name": "sale_project_stock_account", "version": "saas~19.3.1.0"}
        self.other_action = {"key": "ui:other.action", "digest": "a" * 64}
        self.cron_fields = {"interval_number": 8, "code": "model.run()", "model_id": "test.model"}
        self.server_fields = {"code": "model.run()", "model_id": "test.model", "state": "code"}

    def add_row(self, model, row_id, xmlid):
        row = Record(model, row_id)
        row.xmlid = xmlid
        self.rows[model, row_id] = row
        if xmlid:
            self.xmlids[xmlid] = row
        return row

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

    def test_record_destination_uses_its_external_id_across_databases(self):
        clean = self.registry
        restored = SyntheticRegistry(85)
        key = clean.windows[0]
        clean.xmlids[key].res_id = clean.add_row("project.task", 10, "fixture.ticket").id
        restored.xmlids[key].res_id = restored.add_row("project.task", 77, "fixture.ticket").id
        self.assertFalse(inventory.compare_reference_diagnostics(clean.capture(), restored.capture())[1])
        restored.xmlids[key].res_id = restored.add_row("project.task", 10, "fixture.another_ticket").id
        self.assertTrue(inventory.compare_reference_diagnostics(clean.capture(), restored.capture())[1])
        for unresolved in (None, "__export__.project_task_10"):
            restored.add_row("project.task", 10, unresolved)
            receipt = restored.capture()
            self.assertTrue(inventory.compare_reference_diagnostics(receipt, receipt)[1])

    def test_window_modes_views_order_search_and_groups_are_compared(self):
        for field in ("view_mode", "mobile_view_mode", "view_id", "views", "search_view_id", "group_ids"):
            with self.subTest(field=field):
                registry = SyntheticRegistry()
                window = registry.xmlids[registry.windows[0]]
                alternate_view = registry.add_row("ir.ui.view", 17, "fixture.alternate_view")
                changes = {
                    "view_mode": "form", "mobile_view_mode": "list", "view_id": alternate_view,
                    "views": list(reversed(window.views)), "search_view_id": alternate_view,
                    "group_ids": [registry.add_row("res.groups", 17, "fixture.group")],
                }
                setattr(window, field, changes[field])
                errors = inventory.compare_reference_diagnostics(self.baseline, registry.capture())[1]
                self.assertEqual(errors, ["Action changed: ui:" + registry.windows[0]])

    def test_native_views_can_use_other_local_ids_but_unbound_views_cannot_compare(self):
        registry = self.registry
        window = registry.xmlids[registry.windows[0]]
        view = registry.add_row("ir.ui.view", 5, "usl_feedback.board_view")
        for xmlid in registry.windows:
            window = registry.xmlids[xmlid]
            window.views = [(view.id, "kanban"), window.views[1]]
            window.view_ids[0].view_id = view
        self.assertFalse(inventory.compare_reference_diagnostics(self.baseline, registry.capture())[1])
        view.xmlid = None
        self.assertTrue(registry.capture()["issues"])

    def test_changed_view_bindings_are_visible_even_when_computed_views_are_still_cached(self):
        for field in ("sequence", "view_id", "multi"):
            registry = SyntheticRegistry()
            window = registry.xmlids[registry.windows[0]]
            changes = {"sequence": 2, "view_id": window.search_view_id, "multi": True}
            setattr(window.view_ids[0], field, changes[field])
            errors = inventory.compare_reference_diagnostics(self.baseline, registry.capture())[1]
            self.assertEqual(errors, ["Action changed: ui:" + registry.windows[0]])

    def test_implicit_view_resolution_requires_review(self):
        for field, value in (("search_view_id", None), ("views", [(False, "kanban")])):
            registry = SyntheticRegistry()
            setattr(registry.xmlids[registry.windows[0]], field, value)
            receipt = registry.capture()
            self.assertTrue(inventory.compare_reference_diagnostics(receipt, receipt)[1])

        registry = SyntheticRegistry()
        registry.xmlids[registry.windows[0]].view_ids[0].view_id = None
        self.assertTrue(registry.capture()["issues"])

    def test_new_embedded_action_is_inconclusive_even_for_equal_receipts(self):
        self.registry.embedded.sudo.return_value.search_count.return_value = 1
        candidate = self.registry.capture()
        self.assertTrue(inventory.compare_reference_diagnostics(candidate, candidate)[1])

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
        for field, value in (("schema", "usl-action-reference-diagnostic/v1"), ("references", {}), ("issues", None),
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
