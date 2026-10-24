"""Release manifests must not travel in exec arguments or container environments."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from operations import cohort
from operations.runtime import CommandRunner, load_target
from operations.stack import _run_cohort
from scripts.tests.test_release_manifest import manifest, _sign


class BackupManifestTransportTests(unittest.TestCase):
    def test_large_manifest_crosses_real_local_and_ssh_process_stdin(self):
        payload = json.dumps({"context": "contexte-é" * 250_000}, ensure_ascii=False)
        self.assertGreater(len(payload.encode()), 2 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Exercise real exec/pipe boundaries, with Docker and SSH stand-ins.
            # Docker's stdin flag is required; omitting it loses the manifest.
            docker = root / "docker"
            docker.write_text(
                f"#!{sys.executable}\nimport hashlib,json,sys\n"
                "assert '-i' in sys.argv\n"
                "raw=sys.stdin.buffer.read()\n"
                "print(json.dumps({'sha256':hashlib.sha256(raw).hexdigest()}))\n"
            )
            ssh = root / "ssh"
            ssh.write_text(
                f"#!{sys.executable}\nimport os,sys\n"
                "os.execvp('sh',['sh','-c',sys.argv[-1]])\n"
            )
            docker.chmod(0o700)
            ssh.chmod(0o700)
            with patch.dict(os.environ, {"PATH": directory + os.pathsep + os.environ["PATH"]}):
                for prefix in ((), ("ssh", "synthetic-host")):
                    with self.subTest(prefix=prefix):
                        result = _run_cohort(
                            load_target("local", Path(__file__).resolve().parents[2] / "operations/targets"), CommandRunner(prefix),
                            "synthetic-image", "capture", [], input_text=payload,
                        )
                        self.assertEqual(result["sha256"], hashlib.sha256(payload.encode()).hexdigest())

    def test_capture_validates_and_embeds_exact_stdin_manifest_bytes(self):
        value = manifest()
        contract = value["mcp_contract"]
        contract["actions"] = sorted(contract["actions"] + [
            f"synthetic.qa.action_{number:07d}" for number in range(100_000)
        ])
        contract["sha256"] = hashlib.sha256(json.dumps(
            {key: item for key, item in contract.items() if key != "sha256"},
            sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        raw = json.dumps(_sign(value), sort_keys=True, separators=(",", ":"))
        self.assertGreater(len(raw.encode()), 2 * 1024 * 1024)
        environment = self._environment(raw)
        with patch.dict(os.environ, environment, clear=True), patch.object(sys, "stdin", io.StringIO(raw)):
            self.assertEqual(cohort._capture_release_manifest(), raw)

    def _environment(self, raw):
        value = json.loads(raw)
        return {
            "USL_RELEASE_MANIFEST_STDIN": "1",
            "USL_RELEASE_MANIFEST_SHA256": hashlib.sha256(raw.encode()).hexdigest(),
            "USL_RELEASE_COMMIT": value["source"]["commit"],
            "USL_RELEASE_IDENTITY": value["identity"],
        }

    def test_legacy_environment_input_remains_supported(self):
        raw = json.dumps(manifest())
        environment = self._environment(raw)
        environment.pop("USL_RELEASE_MANIFEST_STDIN")
        environment["USL_RELEASE_MANIFEST_JSON"] = raw
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(cohort._capture_release_manifest(), raw)

    def test_capture_rejects_corruption_and_wrong_release_without_echoing_payload(self):
        raw = json.dumps(manifest())
        for field, value in (
            ("USL_RELEASE_MANIFEST_SHA256", "0" * 64),
            ("USL_RELEASE_COMMIT", "0" * 40),
            ("USL_RELEASE_IDENTITY", "0" * 64),
        ):
            with self.subTest(field=field):
                environment = self._environment(raw)
                environment[field] = value
                with patch.dict(os.environ, environment, clear=True), patch.object(sys, "stdin", io.StringIO(raw)):
                    with self.assertRaises(cohort.CohortError) as caught:
                        cohort._capture_release_manifest()
                    self.assertNotIn(raw, str(caught.exception))
        with patch.dict(os.environ, self._environment(raw), clear=True), patch.object(sys, "stdin", io.StringIO(raw + " ")):
            with self.assertRaisesRegex(cohort.CohortError, "digest"):
                cohort._capture_release_manifest()

    def test_transport_failure_redacts_payload_for_local_and_ssh(self):
        payload = "private-manifest-sentinel"
        failure = subprocess.CompletedProcess([], 1, payload, payload)
        for prefix in ((), ("ssh", "synthetic-host")):
            with self.subTest(prefix=prefix), patch("operations.runtime.subprocess.run", return_value=failure) as run:
                with self.assertRaises(RuntimeError) as caught:
                    _run_cohort(load_target("local", Path(__file__).resolve().parents[2] / "operations/targets"), CommandRunner(prefix), "synthetic-image", "capture", [], input_text=payload)
                self.assertNotIn(payload, str(caught.exception))
                self.assertNotIn(payload, " ".join(run.call_args.args[0]))


if __name__ == "__main__":
    unittest.main()
