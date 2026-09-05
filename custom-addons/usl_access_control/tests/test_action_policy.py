import json
import os
import tempfile
from pathlib import Path
from unittest.mock import call, patch

from odoo.tests import BaseCase, tagged

from odoo.addons.usl_access_control.models import action_policy


@tagged("post_install", "-at_install", "usl_access_control")
class TestActionPolicyLoader(BaseCase):
    def setUp(self):
        super().setUp()
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.runtime_policy_path = (
            Path(self.temporary_directory.name) / "protected_runtime_policy.json"
        )
        self.agent_runtime_policy_path = (
            Path(self.temporary_directory.name) / "agent_readonly_runtime_policy.json"
        )
        path_patch = patch.object(
            action_policy,
            "_RUNTIME_POLICY_FILE",
            self.runtime_policy_path,
        )
        path_patch.start()
        self.addCleanup(path_patch.stop)
        agent_path_patch = patch.object(
            action_policy,
            "_AGENT_READONLY_RUNTIME_POLICY_FILE",
            self.agent_runtime_policy_path,
        )
        agent_path_patch.start()
        self.addCleanup(agent_path_patch.stop)
        action_policy.load_action_policy.cache_clear()
        action_policy.load_agent_readonly_policy.cache_clear()
        environment_patch = patch.dict(
            os.environ,
            {"USL_ACTION_RISK_POLICY_SHA256": "unverified"},
        )
        environment_patch.start()
        self.addCleanup(environment_patch.stop)
        self.addCleanup(action_policy.load_action_policy.cache_clear)
        self.addCleanup(action_policy.load_agent_readonly_policy.cache_clear)

    def _write_policy(
        self,
        *,
        actions,
        server_actions=None,
        qualified_policy_digest="a" * 64,
        runtime_policy_sha256=None,
        schema="usl-action-risk-protected-runtime-v2",
    ):
        policy = {
            "actions": actions,
            "qualified_policy_digest": qualified_policy_digest,
            "schema": schema,
            "server_actions": server_actions or [],
        }
        policy["runtime_policy_sha256"] = (
            runtime_policy_sha256 or action_policy._runtime_policy_digest(policy)
        )
        self.runtime_policy_path.write_text(json.dumps(policy), encoding="utf-8")
        return policy

    def _write_agent_policy(
        self,
        *,
        reads=None,
        collaboration=None,
        writes=None,
        qualified_policy_digest="a" * 64,
    ):
        policy = {
            "collaboration_actions": collaboration or [],
            "qualified_policy_digest": qualified_policy_digest,
            "read_only_actions": reads or [],
            "schema": "usl-agent-access-runtime-v2",
            "write_actions": writes or [],
        }
        policy["runtime_policy_sha256"] = action_policy._runtime_policy_digest(policy)
        self.agent_runtime_policy_path.write_text(json.dumps(policy), encoding="utf-8")
        return policy

    def test_loads_exact_agent_readonly_and_collaboration_methods(self):
        self._write_agent_policy(
            reads=["rpc:res.partner.search_read"],
            collaboration=["rpc:project.task.message_post"],
        )

        policy = action_policy.load_agent_readonly_policy()

        self.assertEqual(policy.access_for("res.partner", "search_read"), "read_only")
        self.assertEqual(policy.access_for("project.task", "message_post"), "collaboration")
        self.assertIsNone(policy.access_for("project.task", "write"))

        action_policy.load_agent_readonly_policy.cache_clear()
        self._write_agent_policy(writes=["rpc:project.task.write"])
        policy = action_policy.load_agent_readonly_policy()
        self.assertEqual(policy.access_for("project.task", "write"), "write")

    def test_rejects_stale_or_ambiguous_agent_readonly_policy(self):
        policy = self._write_agent_policy(
            reads=["rpc:res.partner.search_read"],
            collaboration=["rpc:res.partner.search_read"],
        )
        with self.assertRaisesRegex(
            action_policy.ActionPolicyConfigurationError,
            "overlap",
        ):
            action_policy.load_agent_readonly_policy()

        action_policy.load_agent_readonly_policy.cache_clear()
        policy["runtime_policy_sha256"] = "0" * 64
        self.agent_runtime_policy_path.write_text(json.dumps(policy), encoding="utf-8")
        with self.assertRaisesRegex(
            action_policy.ActionPolicyConfigurationError,
            "digest does not match",
        ):
            action_policy.load_agent_readonly_policy()

    def test_loads_semantic_and_model_operation_guards(self):
        self._write_policy(
            actions=[
                {
                    "action_key": "guard:accounting.lock.change",
                    "action_name": "change accounting lock dates",
                    "classification": "protected",
                },
                {
                    "action_key": "rpc:project.task.unlink",
                    "action_name": "permanently delete project.task",
                    "classification": "protected",
                    "enforcement": {
                        "kind": "model_operation",
                        "model": "project.task",
                        "operation": "unlink",
                    },
                },
            ],
        )
        original_reader = action_policy._read_json
        with patch.object(
            action_policy,
            "_read_json",
            wraps=original_reader,
        ) as reader:
            policy = action_policy.load_action_policy()
        self.assertEqual(
            reader.call_args_list,
            [
                call(
                    self.runtime_policy_path,
                    max_bytes=action_policy._RUNTIME_POLICY_MAX_BYTES,
                ),
            ],
        )
        self.assertEqual(policy.qualified_policy_digest, "a" * 64)
        self.assertEqual(
            policy.protected_guard("accounting.lock.change").action_key,
            "guard:accounting.lock.change",
        )
        self.assertEqual(
            policy.model_operation_guard("project.task", "unlink").action_key,
            "rpc:project.task.unlink",
        )
        self.assertIsNone(policy.model_operation_guard("project.task", "write"))
        self.assertIsNone(policy.server_action_classification("server_action:missing"))
        with self.assertRaises(TypeError):
            policy.entries["guard:new"] = None

    def test_loads_reviewed_server_action_classifications(self):
        self._write_policy(
            actions=[],
            server_actions=[
                {
                    "action_key": "server_action:project.open_tasks",
                    "classification": "read_only",
                },
                {
                    "action_key": "server_action:stock.validate_picking",
                    "classification": "operational",
                },
            ],
        )

        policy = action_policy.load_action_policy()

        self.assertEqual(
            policy.server_action_classification("server_action:project.open_tasks"),
            "read_only",
        )
        self.assertEqual(
            policy.server_action_classification("server_action:stock.validate_picking"),
            "operational",
        )

    def test_rejects_non_protected_and_duplicate_model_enforcement(self):
        enforcement = {
            "kind": "model_operation",
            "model": "project.task",
            "operation": "unlink",
        }
        self._write_policy(
            actions=[
                {
                    "action_key": "guard:accounting.lock.change",
                    "classification": "recoverable",
                },
            ],
        )
        with self.assertRaisesRegex(
            action_policy.ActionPolicyConfigurationError,
            "must be classified 'protected'",
        ):
            action_policy.load_action_policy()

        action_policy.load_action_policy.cache_clear()
        self._write_policy(
            actions=[
                {
                    "action_key": "guard:duplicate.task.unlink",
                    "classification": "protected",
                    "enforcement": enforcement,
                },
                {
                    "action_key": "rpc:project.task.unlink",
                    "classification": "protected",
                    "enforcement": enforcement,
                },
            ],
        )
        with self.assertRaisesRegex(
            action_policy.ActionPolicyConfigurationError,
            "Multiple protected guards",
        ):
            action_policy.load_action_policy()

    def test_rejects_runtime_digest_and_schema_mismatch(self):
        self._write_policy(actions=[], runtime_policy_sha256="0" * 64)
        with self.assertRaisesRegex(
            action_policy.ActionPolicyConfigurationError,
            "digest does not match",
        ):
            action_policy.load_action_policy()

    def test_rejects_runtime_policy_above_worker_budget_before_parsing(self):
        self.runtime_policy_path.write_bytes(
            b" " * (action_policy._RUNTIME_POLICY_MAX_BYTES + 1),
        )
        with self.assertRaisesRegex(
            action_policy.ActionPolicyConfigurationError,
            "exceeds its runtime size budget",
        ):
            action_policy.load_action_policy()

        action_policy.load_action_policy.cache_clear()
        self._write_policy(actions=[], schema="future-runtime-schema")
        with self.assertRaisesRegex(
            action_policy.ActionPolicyConfigurationError,
            "must use schema",
        ):
            action_policy.load_action_policy()

    def test_rejects_image_policy_digest_mismatch(self):
        self._write_policy(actions=[], qualified_policy_digest="a" * 64)
        with (
            patch.dict(
                "os.environ",
                {"USL_ACTION_RISK_POLICY_SHA256": "b" * 64},
            ),
            self.assertRaisesRegex(
                action_policy.ActionPolicyConfigurationError,
                "does not match the qualified image",
            ),
        ):
            action_policy.load_action_policy()

    def test_rejects_unsorted_or_unsupported_entries(self):
        self._write_policy(
            actions=[
                {"action_key": "rpc:z.unlink", "classification": "protected"},
                {"action_key": "rpc:a.unlink", "classification": "protected"},
            ],
        )
        with self.assertRaisesRegex(
            action_policy.ActionPolicyConfigurationError,
            "sorted by action_key",
        ):
            action_policy.load_action_policy()

        action_policy.load_action_policy.cache_clear()
        self._write_policy(
            actions=[
                {
                    "action_key": "guard:test",
                    "classification": "protected",
                    "unexpected": True,
                },
            ],
        )
        with self.assertRaisesRegex(
            action_policy.ActionPolicyConfigurationError,
            "unsupported fields",
        ):
            action_policy.load_action_policy()

    def test_rejects_invalid_reviewed_server_actions(self):
        self._write_policy(
            actions=[],
            server_actions=[
                {
                    "action_key": "server_action:z.last",
                    "classification": "operational",
                },
                {
                    "action_key": "server_action:a.first",
                    "classification": "operational",
                },
            ],
        )
        with self.assertRaisesRegex(
            action_policy.ActionPolicyConfigurationError,
            "unique and sorted",
        ):
            action_policy.load_action_policy()
