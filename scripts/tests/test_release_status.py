"""Read-only status must describe the fixed launcher that actually runs releases."""
from __future__ import annotations

import copy
import hashlib
import tempfile
from pathlib import Path
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from operations import stack
from operations.runtime import ReadOnlyRunner, RuntimeError, load_target, read_only_command_problem
from operations.release_controller import create as create_legacy, run as run_legacy, STAGES as LEGACY_STAGES
from scripts.tests.test_release_manifest import manifest

# Canonical fixed-launcher v3 order, including prepare, activation and the
# post-admission backup missing from the legacy controller's stage sequence.
STAGES = (
    "resolve", "prepare", "preflight", "maintenance", "quiesce",
    "pre-release-snapshot", "candidate-materialization", "candidate-upgrade",
    "candidate-qualification", "candidate-snapshot", "activation", "route-switch",
    "production-admission", "post-admission-backup", "reopen", "notify",
    "staging-refresh", "retention", "record",
)
RELEASE = "a" * 64
OTHER = "b" * 64
ROOT = "/var/lib/usl-odoo/runtime/production"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def seal(value):
    body = {key: item for key, item in value.items() if key != "checksum"}
    return {**body, "checksum": digest(body)}


def fixed_run(release=RELEASE, *, status="running", phase="post-admission-backup"):
    completed = list(STAGES if status == "admitted" else STAGES[:STAGES.index(phase)])
    value = {
        "schema": "usl-fixed-release-run/v3", "target": "production", "release": release,
        "attempt": "intent-0123456789abcdef", "status": status, "phase": phase,
        "completed": completed, "started_at": "2026-11-12T00:00:00+00:00",
        "evidence": {},
    }
    for stage in completed:
        detail = {"stage": stage, "synthetic_detail": "not-for-status-output"}
        value["evidence"][stage] = {"sha256": digest(detail), "value": detail}
    if status in {"failed", "recovered"}:
        value.update(failure={"stage": phase, "type": "SyntheticFailure", "occurred_at": value["started_at"]},
                     recovery="forward-fix-only" if STAGES.index(phase) >= STAGES.index("activation")
                     else "rollback-previous-generation")
    if status in {"admitted", "recovered"}:
        value["completed_at"] = "2026-11-12T00:01:00+00:00"
    return seal(value)


def run_file(value):
    return {f"{ROOT}/runs/release-{value['release']}.json": json.dumps(value)}


def legacy_run(*, status="running", target="production"):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "legacy.json"
        value = create_legacy(path, run_id="legacy-release", target=target, release=RELEASE)
        if status == "admitted":
            value = run_legacy(path, {stage: lambda: {"synthetic": True} for stage in LEGACY_STAGES})
        return value


def active_files():
    release = manifest()
    target = load_target("production", stack.DEFAULT_TARGETS)
    path = f"{ROOT}/generations/grel-synthetic/usl-release.json"
    state = {
        "schema": "usl-active-generation/v1", "target": "production", "generation": "grel-synthetic",
        "network": "synthetic-network", "snapshot": "c" * 64,
        "volumes": {role: item["name"] for role, item in target.value["volumes"].items()},
        "release_manifest": path, "previous": None,
    }
    return {f"{ROOT}/active.json": json.dumps(state), path: json.dumps(release)}, release["identity"]


class Host:
    def __init__(self, files=None, entries=None):
        self.files = files or {}
        self.entries = entries
        self.commands = []

    def run(self, command, *, check=True, input_text=None):
        self.commands.append(command)
        if command[0] == "cat":
            code = 0 if command[1] in self.files else 1
            result = subprocess.CompletedProcess(command, code, self.files.get(command[1], ""), "")
        elif command[0] == "find":
            names = self.entries if self.entries is not None else [
                path.rsplit("/", 1)[1] + "\tf" for path in self.files if path.startswith(ROOT + "/runs/")
            ]
            result = subprocess.CompletedProcess(command, 0, "\n".join(names), "")
        elif command[:3] == ["test", "!", "-e"]:
            result = subprocess.CompletedProcess(command, int(command[3] in self.files), "", "")
        else:
            raise AssertionError(f"unexpected host command: {command}")
        if check and result.returncode:
            raise RuntimeError("synthetic read failed")
        return result


class ReleaseStatusTests(unittest.TestCase):
    def observe(self, files=None, *, entries=None, target="production"):
        host = Host(files, entries)
        configured = load_target(target, stack.DEFAULT_TARGETS)
        selected = SimpleNamespace(name=target, value=configured.value, runner=lambda: ReadOnlyRunner(host))
        arguments = stack.build_parser().parse_args(["--target", target, "release", "status", "--json"])
        output = StringIO()
        with patch.object(stack, "load_target", return_value=selected), redirect_stdout(output):
            self.assertEqual(stack.release_command(arguments), 0)
        return json.loads(output.getvalue()), host

    def test_native_running_run_is_not_idle_and_private_evidence_is_omitted(self):
        run = fixed_run()
        value, host = self.observe({f"{ROOT}/runs/release-{RELEASE}.json": json.dumps(run)})
        self.assertEqual(value["status"], "running")
        self.assertEqual(value["source_schema"], "usl-fixed-release-run/v3")
        self.assertEqual(value["phase"], "post-admission-backup")
        self.assertEqual(value["release"], RELEASE)
        self.assertEqual(value["source_checksum"], run["checksum"])
        self.assertEqual(value["evidence_sha256"], {key: item["sha256"] for key, item in run["evidence"].items()})
        self.assertNotIn("not-for-status-output", json.dumps(value))
        self.assertTrue(all(read_only_command_problem(command) is None for command in host.commands))

    def test_canonical_launcher_generated_fixture(self):
        native = json.loads((Path(__file__).parent / "fixtures/fixed-release-run-v3.json").read_text())
        value, _ = self.observe(run_file(native))
        self.assertEqual(value["schema"], "usl-release-status/v1")
        self.assertEqual(value["source_checksum"], "6d79d040eb1a059f1ce5e2604c5aba1cdc299294f83e913349d7e2d7752a78d0")
        self.assertEqual(value["completed"], list(STAGES[:13]))
        self.assertEqual((value["status"], value["phase"]), ("running", "post-admission-backup"))
        self.assertNotIn("not-for-status-output", json.dumps(value))

    def test_running_run_precedes_old_admitted_history_and_active_release(self):
        files, active_identity = active_files()
        files.update(run_file(fixed_run(active_identity, status="admitted", phase="record")))
        files.update(run_file(fixed_run()))
        value, _ = self.observe(files)
        self.assertEqual((value["release"], value["status"]), (RELEASE, "running"))

    def test_active_identity_selects_terminal_run_without_timestamp_guessing(self):
        files, identity = active_files()
        expected = fixed_run(identity, status="admitted", phase="record")
        unrelated = fixed_run(OTHER, status="failed")
        unrelated["started_at"] = "2099-01-01T00:00:00+00:00"
        files.update(run_file(seal(unrelated)))
        files.update(run_file(expected))
        value, _ = self.observe(files)
        self.assertEqual((value["release"], value["status"]), (identity, "admitted"))

    def test_multiple_running_runs_are_ambiguous_even_with_active_binding(self):
        files, identity = active_files()
        files.update(run_file(fixed_run(identity)))
        files.update(run_file(fixed_run()))
        with self.assertRaisesRegex(RuntimeError, "ambiguous: multiple running"):
            self.observe(files)

    def test_legacy_and_fixed_running_are_ambiguous(self):
        with self.assertRaisesRegex(RuntimeError, "ambiguous: multiple running"):
            self.observe({**run_file(fixed_run()), f"{ROOT}/release-state.json": json.dumps(legacy_run())})

    def test_terminal_history_without_authoritative_match_is_not_idle(self):
        for active in ({}, active_files()[0]):
            with self.subTest(active=bool(active)), self.assertRaisesRegex(RuntimeError, "no run matches"):
                self.observe({**active, **run_file(fixed_run(status="failed"))})

    def test_legacy_response_is_preserved_for_running_and_terminal_states(self):
        for status in ("running", "admitted"):
            expected = legacy_run(status=status)
            value, _ = self.observe({f"{ROOT}/release-state.json": json.dumps(expected)})
            self.assertEqual(value, expected)

    def test_local_and_bootstrap_idle_preserve_the_legacy_contract(self):
        for target in ("production", "staging", "local"):
            value, host = self.observe(target=target)
            self.assertEqual(value, {"schema": "usl-release-run/v1", "target": target, "status": "idle"})
            if target == "local":
                self.assertFalse(any(command[0] == "find" for command in host.commands))
        local = legacy_run(target="local")
        value, _ = self.observe({"/tmp/usl-odoo-local/runtime/release-state.json": json.dumps(local)}, target="local")
        self.assertEqual(value, local)

    def test_fixed_failed_and_recovered_states_expose_safe_recovery_metadata(self):
        for status in ("failed", "recovered"):
            files, identity = active_files()
            native = fixed_run(identity, status=status)
            native["failure_context"] = {"phase": "post-admission-backup", "code": "SyntheticFailure"}
            files.update(run_file(seal(native)))
            value, _ = self.observe(files)
            self.assertEqual(value["status"], status)
            self.assertEqual(value["failure_context"]["code"], "SyntheticFailure")
            self.assertEqual(value["recovery"], "forward-fix-only")
            self.assertNotIn("value", json.dumps(value))

    def test_full_envelope_and_nested_evidence_are_verified_before_projection(self):
        original = fixed_run()
        tampered = copy.deepcopy(original)
        tampered["evidence"]["resolve"]["value"]["synthetic_detail"] = "tampered"
        for value, message in ((tampered, "checksum"), (seal(tampered), "evidence digest")):
            with self.subTest(message=message), self.assertRaisesRegex(RuntimeError, message):
                self.observe(run_file(value))

    def test_malformed_native_states_fail_explicitly(self):
        changes = (
            {"schema": "unexpected/v9"}, {"target": "staging"}, {"release": OTHER},
            {"attempt": RELEASE}, {"phase": "invented"}, {"status": "invented"},
            {"completed": ["prepare"]}, {"evidence": {}}, {"unexpected": True},
            {"started_at": {"synthetic": "not-a-timestamp"}},
            {"failure_context": {"phase": "record", "code": "not a safe code"}},
            {"status": "admitted"}, {"status": "failed", "failure": []},
        )
        for changed in changes:
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                value = seal({**fixed_run(), **changed})
                self.observe({f"{ROOT}/runs/release-{RELEASE}.json": json.dumps(value)})
        for text in ("[]", "{bad-json"):
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                self.observe({f"{ROOT}/runs/release-{RELEASE}.json": text})

    def test_failed_archives_and_sidecars_are_not_selected_or_read(self):
        run = fixed_run()
        files = {**run_file(run), f"{ROOT}/runs/release-{OTHER}.failed-1234567890abcdef.json": "not JSON"}
        value, host = self.observe(files)
        self.assertEqual(value["release"], RELEASE)
        self.assertFalse(any("failed-" in " ".join(command) for command in host.commands))
        with self.assertRaisesRegex(RuntimeError, "not a regular file"):
            self.observe(entries=[f"release-{RELEASE}.json\tl"])
        with self.assertRaisesRegex(RuntimeError, "inventory is invalid"):
            self.observe(entries=["malformed-listing"])

    def test_malformed_active_and_legacy_states_are_not_hidden(self):
        for files in (
            {f"{ROOT}/release-state.json": "[]"},
            {f"{ROOT}/release-state.json": json.dumps(legacy_run(target="staging"))},
            {**run_file(fixed_run(status="failed")), f"{ROOT}/active.json": "[]"},
        ):
            with self.subTest(files=list(files)), self.assertRaises(RuntimeError):
                self.observe(files)
        files, identity = active_files()
        path = f"{ROOT}/generations/grel-synthetic/usl-release.json"
        value = json.loads(files[path]); value["identity"] = "0" * 64
        files[path] = json.dumps(value)
        with self.assertRaisesRegex(RuntimeError, "active release manifest is invalid"):
            self.observe({**files, **run_file(fixed_run(identity, status="admitted", phase="record"))})

    def test_every_native_running_phase_is_observable(self):
        for phase in STAGES:
            with self.subTest(phase=phase):
                value, _ = self.observe(run_file(fixed_run(phase=phase)))
                self.assertEqual(value["phase"], phase)
                self.assertEqual(value["completed"], list(STAGES[:STAGES.index(phase)]))
                self.assertEqual(value["status"], "running")

    def test_unreadable_state_and_inventory_are_not_reported_idle(self):
        original = Host.run
        for denied in (f"{ROOT}/release-state.json", f"{ROOT}/runs"):
            def deny(host, command, *, check=True, input_text=None):
                if command[0] in {"cat", "find"} and command[1] == denied:
                    return subprocess.CompletedProcess(command, 1, "", "")
                if command == ["test", "!", "-e", denied]:
                    return subprocess.CompletedProcess(command, 1, "", "")
                return original(host, command, check=check, input_text=input_text)
            with self.subTest(denied=denied), patch.object(Host, "run", deny):
                with self.assertRaisesRegex(RuntimeError, "cannot be (read|inspected)"):
                    self.observe()

    def test_find_allowlist_refuses_mutation_variants_before_execution(self):
        _, host = self.observe(run_file(fixed_run()))
        command = next(command for command in host.commands if command[0] == "find")
        self.assertIsNone(read_only_command_problem(command))
        variants = (
            [*command, "-delete"], [*command, "-exec", "sh", "-c", "true", ";"],
            [*command[:-2], "-fls", "/tmp/synthetic"],
            [*command[:2], "-L", *command[2:]],
            [command[0], "/var/lib/../runs", *command[2:]],
        )
        for variant in variants:
            with self.subTest(command=variant):
                self.assertIsNotNone(read_only_command_problem(variant))
                isolated = Host()
                with self.assertRaisesRegex(RuntimeError, "write-capable"):
                    ReadOnlyRunner(isolated).run(variant)
                self.assertEqual(isolated.commands, [])
        self.assertIsNotNone(read_only_command_problem(command, "input"))


if __name__ == "__main__":
    unittest.main()
