from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from operations.release_manifest import validate, ReleaseManifestError
from operations.runtime import load_target, read_active_state, RuntimeError
from operations.stack import _active_cron_policy, _compose_services, _release_images, _generation_overlay
from test_release_manifest import manifest


class ComponentTransitionTests(unittest.TestCase):
    def test_rollback_cron_policy_comes_from_active_release_not_candidate_tools(self):
        release = manifest()
        active_image = release["components"]["backup-tool"]["digest_reference"]
        policy = {
            "schema": "usl-production-cron-policy-v1",
            "gates": ["always"],
            "crons": {"base.autovacuum_job": {"gate": "always", "reason": "Maintenance"}},
        }
        target = SimpleNamespace(value={"cron_policy": {
            "mode": "managed", "path": "/opt/usl/deploy/production.cron-policy.json",
            "gates": {"always": True},
        }})
        runner = Mock()
        runner.run.return_value = subprocess.CompletedProcess([], 0, json.dumps(policy), "")
        with patch("operations.stack._release", return_value=(release, "digest", "raw")), patch(
            "operations.stack._ensure_image",
        ) as ensure, patch.dict("os.environ", {"USL_OPERATIONS_IMAGE": "candidate-tools"}):
            self.assertEqual(_active_cron_policy(target, runner), policy)
        ensure.assert_called_once_with(runner, active_image)
        command = runner.run.call_args.args[0]
        self.assertEqual(command[-2:], [active_image, target.value["cron_policy"]["path"]])
        self.assertNotIn("candidate-tools", command)

    def test_unmanaged_crons_do_not_load_an_image(self):
        runner = Mock()
        self.assertIsNone(_active_cron_policy(SimpleNamespace(value={
            "cron_policy": {"mode": "unmanaged"},
        }), runner))
        runner.run.assert_not_called()

    def test_current_release_remains_a_valid_backup_and_restore_input(self):
        release = manifest()
        for name in ("receipt-fetcher", "receipt-egress"):
            del release["components"][name]
        del release["identity"]
        release["identity"] = hashlib.sha256(json.dumps(release, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        validate(release)
        self.assertTrue(_release_images(release))
        overlay = json.loads(_generation_overlay({}, release))
        self.assertNotIn("usl-receipt-fetcher", overlay["services"])

    def test_half_a_receipt_component_pair_is_rejected(self):
        release = manifest()
        del release["components"]["receipt-egress"]
        with self.assertRaisesRegex(ReleaseManifestError, "both receipt"):
            validate(release)

    def test_existing_compose_can_lack_only_new_receipt_services(self):
        target = load_target("local", Path(__file__).resolve().parents[2] / "operations/targets")
        identity = {"project": "test", "working_directory": "/test", "environment_file": "/test/env", "compose_files": ["/test/compose.yaml"]}
        expected = set(target.value["services"].values())
        core = expected - {target.value["services"][role] for role in ("receipt_fetcher", "receipt_egress")}
        runner = Mock()
        runner.run.return_value = subprocess.CompletedProcess([], 0, "\n".join(core), "")
        self.assertEqual(set(_compose_services(target, identity, runner)), core)
        runner.run.return_value.stdout = "\n".join(core - {target.value["services"]["odoo"]})
        with self.assertRaisesRegex(RuntimeError, "missing required"):
            _compose_services(target, identity, runner)
        runner.run.return_value.stdout = "\n".join(expected | {"foreign-service"})
        self.assertEqual(set(_compose_services(target, identity, runner)), expected)

    def test_new_transient_volume_does_not_invalidate_current_backup(self):
        target = load_target("local", Path(__file__).resolve().parents[2] / "operations/targets")
        state = {"schema": "usl-active-generation/v1", "target": "local", "generation": "gtest", "volumes": {role: value["name"] for role, value in target.value["volumes"].items()}, "network": "test", "snapshot": "a" * 64, "release_manifest": target.value["state_directory"] + "/generations/gtest/usl-release.json", "previous": None}
        del state["volumes"]["receipt_control"]
        runner = Mock()
        runner.run.return_value = subprocess.CompletedProcess([], 0, json.dumps(state), "")
        self.assertEqual(read_active_state(target, runner)["volumes"], state["volumes"])
        del state["volumes"]["odoo_filestore"]
        runner.run.return_value.stdout = json.dumps(state)
        with self.assertRaisesRegex(RuntimeError, "volume perimeter"):
            read_active_state(target, runner)
