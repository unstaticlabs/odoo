"""The three reviewed native structured reads are not a method-name grant."""

import ast
import copy
import unittest

from scripts import action_risk_inventory as inventory


MODELS = ("project.task", "project.project", "res.partner")
NATIVE_DIGEST = "fe5000878c0abc0ae893b805c2379ef4f5147d56e2875a51e83858a26d1c6d4b"


class TestNativeStructuredReadPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.surface = inventory.load_json(inventory.DEFAULT_SURFACE)
        cls.policy = inventory.load_json(inventory.DEFAULT_POLICY)
        errors = []
        cls.entries = inventory.normalize_policy_actions(cls.policy, errors)
        assert not errors, errors
        cls.actions = {row["key"]: row for row in cls.surface["actions"]}

    def test_exact_declared_native_bodies_have_readonly_decisions(self):
        native = inventory.ROOT / "addons/web/models/models.py"
        methods = [node for node in ast.walk(ast.parse(native.read_text())) if isinstance(node, ast.FunctionDef) and node.name == "web_search_read"]
        self.assertEqual(len(methods), 1)
        self.assertIn("api.readonly", [ast.unparse(node) for node in methods[0].decorator_list])
        for model in MODELS:
            with self.subTest(model=model):
                key = f"rpc:{model}.web_search_read"
                self.assertEqual(self.actions[key]["digest"], NATIVE_DIGEST)
                self.assertIn("addons/web/models/models.py", [row["path"] for row in self.actions[key]["sources"]])
                self.assertEqual(self.entries[key]["classification"], "read_only")
                self.assertEqual(self.entries[key]["reviewed_digest"], NATIVE_DIGEST)

    def test_compiled_scope_does_not_turn_business_mutations_into_reads(self):
        compiled = inventory.build_agent_readonly_runtime_policy(self.surface, self.policy)
        for model in MODELS:
            with self.subTest(model=model):
                key = f"rpc:{model}.web_search_read"
                self.assertIn(key, compiled["read_only_actions"])
                self.assertNotIn(key, compiled["write_actions"])
                for method in ("create", "write", "unlink"):
                    mutation = f"rpc:{model}.{method}"
                    self.assertNotIn(mutation, compiled["read_only_actions"])
                    self.assertNotIn(mutation, compiled["collaboration_actions"])

    def test_a_changed_override_requires_a_new_review(self):
        keys = {f"rpc:{model}.{method}" for model in MODELS for method in ("web_search_read", "write")}
        surface = {**self.surface, "actions": [copy.deepcopy(self.actions[key]) for key in sorted(keys)], "diagnostics": []}
        policy = {**self.policy, "actions": {key: self.entries[key] for key in sorted(keys)}}
        self.assertEqual(inventory.validate_inventory(surface, policy), [])
        surface["actions"][0]["digest"] = "1" * 64
        errors = inventory.validate_inventory(surface, policy)
        self.assertTrue(any("Changed action requires review" in error for error in errors), errors)

    def test_an_unreviewed_model_does_not_get_readonly_by_method_name(self):
        key = "rpc:qa.unreviewed.web_search_read"
        policy = {"schema": inventory.POLICY_SCHEMA, "evidence_families": {}, "actions": {
            key: {"classification": "recoverable", "domain": "test", "consequence": "Synthetic mutation control", "rationale": "A name is not a readonly proof", "evidence_id": "synthetic", "reviewed_digest": NATIVE_DIGEST}}}
        compiled = inventory.build_agent_readonly_runtime_policy({"actions": []}, policy)
        self.assertNotIn(key, compiled["read_only_actions"])
        self.assertIn(key, compiled["write_actions"])
