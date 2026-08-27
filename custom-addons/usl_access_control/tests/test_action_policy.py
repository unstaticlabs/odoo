import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from odoo.tests import BaseCase, tagged

from odoo.addons.usl_access_control.models import action_policy


@tagged("post_install", "-at_install", "usl_access_control")
class TestActionPolicyLoader(BaseCase):
    def setUp(self):
        super().setUp()
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        directory = Path(self.temporary_directory.name)
        self.surface_path = directory / "action_surface.json"
        self.policy_path = directory / "action_policy.json"
        self.path_patches = (
            patch.object(action_policy, "_SURFACE_FILE", self.surface_path),
            patch.object(action_policy, "_POLICY_FILE", self.policy_path),
        )
        for path_patch in self.path_patches:
            path_patch.start()
            self.addCleanup(path_patch.stop)
        action_policy.load_action_policy.cache_clear()
        self.addCleanup(action_policy.load_action_policy.cache_clear)

    def _write_policy(self, *, actions, qualified_policy_digest=None):
        surface = {
            "actions": {"rpc:project.task.unlink": {"digest": "source-digest"}},
            "schema": "usl-action-risk-surface-v1",
        }
        policy = {
            "actions": actions,
            "schema": "usl-action-risk-policy-v1",
        }
        if qualified_policy_digest is not None:
            policy["qualified_policy_digest"] = qualified_policy_digest
        self.surface_path.write_text(json.dumps(surface), encoding="utf-8")
        self.policy_path.write_text(json.dumps(policy), encoding="utf-8")
        canonical = json.dumps(
            {"action_policy": policy, "action_surface": surface},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(canonical).hexdigest()

    def test_loads_semantic_and_model_operation_guards(self):
        expected_digest = self._write_policy(
            actions={
                "guard:accounting.lock.change": {
                    "classification": "protected",
                    "label": "change accounting lock dates",
                },
                "rpc:project.task.unlink": {
                    "classification": "protected",
                    "enforcement": {
                        "kind": "model_operation",
                        "model": "project.task",
                        "operation": "unlink",
                    },
                    "label": "permanently delete project.task",
                },
            },
        )
        policy = action_policy.load_action_policy()
        self.assertEqual(policy.qualified_policy_digest, expected_digest)
        self.assertEqual(
            policy.protected_guard("accounting.lock.change").action_key,
            "guard:accounting.lock.change",
        )
        self.assertEqual(
            policy.model_operation_guard("project.task", "unlink").action_key,
            "rpc:project.task.unlink",
        )
        self.assertIsNone(policy.model_operation_guard("project.task", "write"))
        with self.assertRaises(TypeError):
            policy.entries["guard:new"] = None

    def test_loads_compact_exact_key_groups_and_keeps_only_runtime_guards(self):
        self._write_policy(
            actions=[
                {
                    "id": "protected-project-delete",
                    "action_keys": ["rpc:project.task.unlink"],
                    "classification": "protected",
                    "domain": "projects",
                    "consequence": "Permanent task deletion.",
                    "rationale": "Tasks have an archive workflow.",
                    "evidence_id": "protected-contract",
                    "reviewed_digests": {
                        "rpc:project.task.unlink": "source-digest",
                    },
                    "overrides": {
                        "rpc:project.task.unlink": {
                            "enforcement": {
                                "kind": "model_operation",
                                "model": "project.task",
                                "operation": "unlink",
                            },
                        },
                    },
                },
                {
                    "id": "read-only-metadata",
                    "action_keys": ["rpc:project.task.fields_get"],
                    "classification": "read_only",
                    "domain": "projects",
                    "consequence": "Returns metadata.",
                    "rationale": "No mutation sink.",
                    "evidence_id": "read-only-contract",
                    "reviewed_digests": {
                        "rpc:project.task.fields_get": "metadata-digest",
                    },
                },
            ],
        )
        policy = action_policy.load_action_policy()
        self.assertEqual(
            policy.model_operation_guard("project.task", "unlink").action_key,
            "rpc:project.task.unlink",
        )
        self.assertNotIn("rpc:project.task.fields_get", policy.entries)

    def test_rejects_non_protected_guard_and_duplicate_model_enforcement(self):
        self._write_policy(
            actions={
                "guard:accounting.lock.change": {"classification": "recoverable"},
                "rpc:project.task.unlink": {
                    "classification": "protected",
                    "enforcement": {
                        "kind": "model_operation",
                        "model": "project.task",
                        "operation": "unlink",
                    },
                },
                "guard:duplicate.task.unlink": {
                    "classification": "protected",
                    "enforcement": {
                        "kind": "model_operation",
                        "model": "project.task",
                        "operation": "unlink",
                    },
                },
            },
        )
        with self.assertRaisesRegex(
            action_policy.ActionPolicyConfigurationError,
            "Multiple protected guards",
        ):
            action_policy.load_action_policy()

        action_policy.load_action_policy.cache_clear()
        self._write_policy(
            actions={
                "guard:accounting.lock.change": {"classification": "recoverable"},
            },
        )
        policy = action_policy.load_action_policy()
        with self.assertRaisesRegex(
            action_policy.ActionPolicyConfigurationError,
            "absent from the qualified policy",
        ):
            policy.protected_guard("accounting.lock.change")

    def test_rejects_declared_digest_mismatch(self):
        self._write_policy(
            actions={
                "guard:accounting.lock.change": {"classification": "protected"},
            },
            qualified_policy_digest="0" * 64,
        )
        with self.assertRaisesRegex(
            action_policy.ActionPolicyConfigurationError,
            "digest does not match",
        ):
            action_policy.load_action_policy()

    def test_rejects_unknown_policy_schema(self):
        self._write_policy(actions={})
        policy = json.loads(self.policy_path.read_text(encoding="utf-8"))
        policy["schema"] = "future-policy-schema"
        self.policy_path.write_text(json.dumps(policy), encoding="utf-8")
        with self.assertRaisesRegex(
            action_policy.ActionPolicyConfigurationError,
            "must use schema",
        ):
            action_policy.load_action_policy()
