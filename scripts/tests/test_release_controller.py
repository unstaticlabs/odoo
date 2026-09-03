from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from operations import stack
from operations.release_controller import (
    STAGES,
    ReleaseControllerError,
    abort,
    create,
    load,
    parse,
    run,
)


class ReleaseControllerTests(unittest.TestCase):
    def handlers(self, fail=None, calls=None):
        calls = calls if calls is not None else []

        def handler(stage):
            def execute():
                calls.append(stage)
                if stage == fail:
                    raise RuntimeError("injected")
                return {"stage": stage}
            return execute

        return {stage: handler(stage) for stage in STAGES}

    def test_resumes_without_repeating_completed_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            create(path, run_id="release-run", target="staging", release="a" * 64)
            run(path, self.handlers(), stop_after="candidate-upgrade")
            calls = []
            result = run(path, self.handlers(calls=calls))
            self.assertEqual(result["status"], "admitted")
            self.assertNotIn("resolve", calls)
            self.assertEqual(calls[-1], "record")

    def test_failure_injection_at_every_stage_records_safe_recovery(self):
        for failed_stage in STAGES:
            with self.subTest(stage=failed_stage), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "state.json"
                create(path, run_id="release-run", target="production", release="a" * 64)
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    run(path, self.handlers(fail=failed_stage))
                state = load(path)
                expected = "forward-fix-only" if STAGES.index(failed_stage) >= STAGES.index("reopen") else "rollback-previous-generation"
                self.assertEqual(state["recovery"], expected)

    def test_checksum_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            create(path, run_id="release-run", target="staging", release="a" * 64)
            path.write_text(path.read_text().replace('"staging"', '"production"'), encoding="utf-8")
            with self.assertRaisesRegex(ReleaseControllerError, "checksum"):
                load(path)

    def test_nested_evidence_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            create(path, run_id="release-run", target="staging", release="a" * 64)
            run(path, self.handlers(), stop_after="resolve")
            text = path.read_text().replace('"stage": "resolve"', '"stage": "preflight"')
            with self.assertRaisesRegex(ReleaseControllerError, "checksum"):
                parse(text)

    def test_non_object_state_is_rejected_cleanly(self):
        with self.assertRaisesRegex(ReleaseControllerError, "schema"):
            parse("[]")

    def test_abort_rechecks_state_and_recomputes_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = create(
                path,
                run_id="release-run",
                target="production",
                release="a" * 64,
            )
            aborted = abort(state)
            self.assertEqual(parse(json.dumps(aborted))["status"], "aborted")

    def test_abort_is_forbidden_at_the_reopen_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            create(
                path,
                run_id="release-run",
                target="production",
                release="a" * 64,
            )
            run(path, self.handlers(), stop_after="production-admission")
            state = load(path)
            state["phase"] = "reopen"
            with self.assertRaisesRegex(ReleaseControllerError, "forward fix"):
                abort(state)

    def test_public_status_rejects_tampered_state(self):
        runner = SimpleNamespace(
            run=lambda *_args, **_kwargs: subprocess.CompletedProcess(
                [], 0, '{"schema":"usl-release-run/v1","checksum":"bad"}', ""
            )
        )
        target = SimpleNamespace(
            name="staging",
            value={"state_directory": "/state"},
            runner=lambda: runner,
        )
        arguments = SimpleNamespace(
            action="status", target="staging", targets=Path("targets"), json=True
        )
        with patch.object(stack, "load_target", return_value=target), self.assertRaisesRegex(
            stack.RuntimeError, "checksum"
        ):
            stack.release_command(arguments)

    def test_public_abort_writes_a_valid_checksummed_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            create(
                path,
                run_id="release-run",
                target="production",
                release="a" * 64,
            )
            runner = SimpleNamespace(
                run=lambda *_args, **_kwargs: subprocess.CompletedProcess(
                    [], 0, path.read_text(), ""
                )
            )
            target = SimpleNamespace(
                name="production",
                value={"state_directory": "/state"},
                runner=lambda: runner,
            )
            arguments = SimpleNamespace(
                action="abort",
                target="production",
                targets=Path("targets"),
                json=True,
            )
            written = {}
            with patch.object(stack, "load_target", return_value=target), patch.object(
                stack,
                "_write_remote",
                side_effect=lambda _target, _runner, _path, text, *_args: written.setdefault(
                    "text", text
                ),
            ), redirect_stdout(StringIO()):
                self.assertEqual(stack.release_command(arguments), 0)
            self.assertEqual(parse(written["text"])["status"], "aborted")


if __name__ == "__main__":
    unittest.main()
