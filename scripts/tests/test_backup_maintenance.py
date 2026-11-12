"""Ordinary backup capture owns only the maintenance it created."""
from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, nullcontext, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from operations import stack
from operations.runtime import CommandRunner, RuntimeError, load_target


class BackupHost:
    def __init__(self, root, *, gateway=True, require_maintenance=True):
        self.root = Path(root)
        self.marker = self.root / "gateway/production/maintenance"
        self.marker.parent.mkdir(parents=True)
        self.gateway = gateway
        self.require_maintenance = require_maintenance
        self.commands = []
        self.events = []
        self.fail_stop = False
        self.fail_resume = False
        self.replace_before_claim = None

    def run(self, command, *, check=True, input_text=None):
        self.commands.append(command)
        if command[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(command, 0, (self.gateway if isinstance(self.gateway, str) else "gateway-id\n") if self.gateway else "", "")
        if command[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(command, 0, json.dumps([
                {"Destination": "/run/usl-gateway", "Source": str(self.marker.parent)},
            ]), "")
        if command[:2] == ["python3", "-c"]:
            if self.replace_before_claim is not None and not self.marker.exists():
                self.marker.write_bytes(self.replace_before_claim)
                self.replace_before_claim = None
            return CommandRunner().run(command, check=check, input_text=input_text)
        if command[0] in {"mkdir", "rmdir", "rm"}:
            return CommandRunner().run(command, check=check, input_text=input_text)
        if command[:2] == ["install", "-d"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:2] == ["docker", "compose"]:
            if "stop" in command:
                self.events.append("stop")
                if self.require_maintenance:
                    assert self.marker.is_file(), "writers stopped before gateway maintenance"
                if self.fail_stop:
                    raise RuntimeError("synthetic stop failure")
            elif "up" in command:
                self.events.append("resume")
                if self.fail_resume:
                    raise RuntimeError("synthetic unhealthy resume")
                assert "--wait" in command and "--no-recreate" in command and "--no-deps" in command
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(f"unexpected command: {command}")


class BackupMaintenanceTests(unittest.TestCase):
    def execute(self, host, *, target_name="production", quiesced=False, fail_capture=False,
                fail_upload=False, replace_during_capture=None, cas=None, real_lock=False):
        config = copy.deepcopy(load_target(target_name, stack.DEFAULT_TARGETS).value)
        config["state_directory"] = str(host.root / "runtime" / target_name)
        host.marker = host.root / "gateway" / target_name / "maintenance"
        host.marker.parent.mkdir(parents=True, exist_ok=True)
        identity = getattr(host, "identity", None) or {"project": "synthetic-backup", "working_directory": "/fixture", "environment_file": "/fixture/.env", "compose_files": ["/fixture/compose.yaml"]}
        if getattr(host, "native_gateway", False):
            for role in stack.BACKUP_WRITER_SERVICE_ROLES:
                config["services"][role] = "writer"
        target = SimpleNamespace(name=target_name, value=config, project=identity["project"], runner=lambda: host)
        release = {"identity": "a" * 64, "source": {"commit": "b" * 40}, "components": {"backup-tool": {"digest_reference": "fixture@sha256:" + "c" * 64}}}
        def cohort(_target, _runner, _image, action, *_args, **_kwargs):
            host.events.append(action)
            if action == "capture":
                if getattr(host, "on_capture", None):
                    host.on_capture()
                if replace_during_capture is not None:
                    host.marker.write_bytes(replace_during_capture)
                if fail_capture:
                    raise RuntimeError("synthetic capture failure")
                return {"status": "captured"}
            if action == "push":
                if fail_upload:
                    raise RuntimeError("synthetic upload failure")
                return {"durable_snapshot_id": "d" * 64}
            return {"status": "qualified"}
        arguments = stack.build_parser().parse_args(["--target", target_name, "backup", "create", "--run-id", "synthetic-backup-run", "--json"] + (["--leave-quiesced"] if quiesced else []))
        patches = {
            "load_target": target, "inspect_runtime": {"compose": identity, "volumes": {}},
            "_release": (release, "e" * 64, "{}"), "_secret_file": None,
            "_ensure_image": None, "_validate_runtime_release_images": {},
            "runtime_lock": nullcontext(), "_record_event": None,
            "_runtime_cas_sha256": "f" * 64, "compose_identity": identity,
            "_mcp_runtime_authority": None, "_write_remote": None,
        }
        if real_lock:
            patches.pop("runtime_lock")
        with ExitStack() as scope, redirect_stdout(StringIO()):
            for name, value in patches.items():
                scope.enter_context(patch.object(stack, name, return_value=value))
            scope.enter_context(patch.object(stack, "_run_cohort", side_effect=cohort))
            if cas is not None:
                scope.enter_context(patch.object(stack, "_runtime_cas_sha256", side_effect=cas))
            return stack.backup_command(arguments)

    def test_ordinary_production_capture_enters_maintenance_before_stopping_writers(self):
        with tempfile.TemporaryDirectory() as root:
            host = BackupHost(root)
            self.assertEqual(self.execute(host), 0)
            self.assertEqual(host.events, ["stop", "capture", "resume", "push", "qualify"])
            self.assertFalse(host.marker.exists())

    def test_ordinary_staging_capture_owns_and_removes_only_its_marker(self):
        with tempfile.TemporaryDirectory() as root:
            host = BackupHost(root)
            self.assertEqual(self.execute(host, target_name="staging"), 0)
            self.assertFalse(host.marker.exists())

    def test_preexisting_release_and_legacy_markers_survive_ordinary_and_quiesced_capture(self):
        markers = (b"", b"legacy-maintenance\n", b'{"schema":"usl-maintenance-marker/v1","attempt":"synthetic-release"}\n')
        for marker in markers:
            for quiesced in (False, True):
                with self.subTest(marker=marker, quiesced=quiesced), tempfile.TemporaryDirectory() as root:
                    host = BackupHost(root); host.marker.write_bytes(marker)
                    self.assertEqual(self.execute(host, quiesced=quiesced), 0)
                    self.assertEqual(host.marker.read_bytes(), marker)
                    self.assertEqual("resume" in host.events, not quiesced)

    def test_quiesced_capture_without_existing_marker_keeps_owned_maintenance(self):
        with tempfile.TemporaryDirectory() as root:
            host = BackupHost(root)
            self.assertEqual(self.execute(host, quiesced=True), 0)
            marker = json.loads(host.marker.read_text())
            self.assertEqual(marker["schema"], "usl-backup-maintenance/v1")
            self.assertNotIn("resume", host.events)

    def test_local_and_bootstrap_without_gateway_keep_existing_behavior(self):
        for target in ("local", "production", "staging"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as root:
                host = BackupHost(root, gateway=False, require_maintenance=False)
                self.assertEqual(self.execute(host, target_name=target), 0)
                self.assertFalse(host.marker.exists())
                if target == "local":
                    self.assertFalse(any(command[:2] == ["docker", "ps"] for command in host.commands))

    def test_capture_and_partial_stop_failures_resume_before_reopening(self):
        for stop_failure in (False, True):
            with self.subTest(stop_failure=stop_failure), tempfile.TemporaryDirectory() as root:
                host = BackupHost(root); host.fail_stop = stop_failure
                with self.assertRaisesRegex(RuntimeError, "synthetic (stop|capture) failure"):
                    self.execute(host, fail_capture=not stop_failure)
                self.assertIn("resume", host.events)
                self.assertFalse(host.marker.exists())

    def test_unhealthy_resume_keeps_maintenance(self):
        with tempfile.TemporaryDirectory() as root:
            host = BackupHost(root); host.fail_resume = True
            with self.assertRaisesRegex(RuntimeError, "unhealthy resume"):
                self.execute(host)
            self.assertTrue(host.marker.is_file())
            self.assertNotIn("push", host.events)

    def test_quiesced_upload_failure_reopens_only_after_healthy_unchanged_baseline(self):
        for mismatch in (False, True):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as root:
                host = BackupHost(root)
                with self.assertRaisesRegex(RuntimeError, "baseline" if mismatch else "upload failure"):
                    self.execute(host, quiesced=True, fail_upload=True, cas=["f" * 64, ("0" if mismatch else "f") * 64])
                self.assertIn("resume", host.events)
                self.assertEqual(host.marker.exists(), mismatch)

    def test_preexisting_marker_is_preserved_when_capture_fails(self):
        with tempfile.TemporaryDirectory() as root:
            host = BackupHost(root); marker = b"release-still-owns-maintenance"; host.marker.write_bytes(marker)
            with self.assertRaisesRegex(RuntimeError, "capture failure"):
                self.execute(host, quiesced=True, fail_capture=True)
            self.assertEqual(host.marker.read_bytes(), marker)

    def test_concurrent_creator_or_replacement_preserves_foreign_marker(self):
        for before in (False, True):
            with self.subTest(before=before), tempfile.TemporaryDirectory() as root:
                host = BackupHost(root); foreign = b"another-operation-owns-maintenance"
                if before:
                    host.replace_before_claim = foreign
                self.assertEqual(self.execute(host, replace_during_capture=None if before else foreign), 0)
                self.assertEqual(host.marker.read_bytes(), foreign)

    def test_existing_operation_lock_refuses_overlap_before_marker_or_stop(self):
        with tempfile.TemporaryDirectory() as root:
            host = BackupHost(root)
            value = copy.deepcopy(load_target("production", stack.DEFAULT_TARGETS).value)
            value["state_directory"] = str(host.root / "runtime/production")
            target = SimpleNamespace(name="production", value=value)
            with stack.runtime_lock(target, CommandRunner(), "existing", "existing-owner"):
                with self.assertRaisesRegex(RuntimeError, "another operation already owns"):
                    self.execute(host, real_lock=True)
            self.assertNotIn("stop", host.events)
            self.assertFalse(host.marker.exists())

    def test_ambiguous_gateway_is_rejected_before_writers_stop(self):
        with tempfile.TemporaryDirectory() as root:
            host = BackupHost(root, gateway="first\nsecond\n")
            with self.assertRaisesRegex(RuntimeError, "gateway identity is ambiguous"):
                self.execute(host)
            self.assertNotIn("stop", host.events)



if __name__ == "__main__":
    unittest.main()
