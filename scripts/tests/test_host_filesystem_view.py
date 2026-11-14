"""Physical cleanup reads the filesystem where the selected executor owns data."""
import json
import socket
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
import os
import subprocess
import unittest
from unittest import mock

from operations import host_filesystem as host
from operations.runtime import CommandRunner, ReadOnlyRunner, RuntimeError, load_target, validate_target
from operations.stack import _canonical_filesystem_paths, _assert_capture_unmounted


class ExecutionScopeTests(unittest.TestCase):
    def test_host_image_without_provider_cannot_guess_from_its_own_namespace(self):
        runner = CommandRunner(filesystem_scope="host")
        with mock.patch.dict(os.environ, {"USL_OPERATIONS_CONTAINER": "1"}), \
                mock.patch.object(host, "SOCKET_PATH", "/tmp/task2374-missing-provider-" + str(os.getpid())), \
                mock.patch.object(runner, "run", return_value=subprocess.CompletedProcess([], 0, "/capture\n", "")) as command:
            with self.assertRaisesRegex(RuntimeError, "host filesystem metadata"):
                _canonical_filesystem_paths(runner, ["/capture"])
        command.assert_not_called()

    def test_unknown_real_local_image_scope_requires_an_explicit_context(self):
        runner = CommandRunner()
        with mock.patch.dict(os.environ, {"USL_OPERATIONS_CONTAINER": "1"}), \
                mock.patch.object(runner, "run", return_value=subprocess.CompletedProcess([], 0, "/capture\n", "")) as command:
            with self.assertRaisesRegex(RuntimeError, "filesystem_scope"):
                _canonical_filesystem_paths(runner, ["/capture"])
        command.assert_not_called()

    def test_native_host_ssh_and_explicit_namespace_keep_their_own_metadata_view(self):
        for marker, runner in (
            ("", CommandRunner(filesystem_scope="host")),
            ("1", CommandRunner(("ssh", "odoo", "--"), filesystem_scope="host")),
            ("1", CommandRunner(filesystem_scope="namespace")),
        ):
            with self.subTest(marker=marker, prefix=runner.prefix, scope=runner.filesystem_scope), \
                    mock.patch.dict(os.environ, {"USL_OPERATIONS_CONTAINER": marker}), \
                    mock.patch.object(runner, "run", return_value=subprocess.CompletedProcess([], 0, "/capture\n", "")) as command:
                self.assertEqual(_canonical_filesystem_paths(runner, ["/capture"]), ["/capture"])
                self.assertEqual(command.call_args.args[0], ["readlink", "-m", "--", "/capture"])


@contextmanager
def provider(reply, fragment_delay=0):
    requests = []
    with tempfile.TemporaryDirectory(prefix="usl-meta-", dir="/tmp") as temporary:
        path = str(Path(temporary) / "observe.sock")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(path); listener.listen(); listener.settimeout(0.05)
        stopped = threading.Event()
        def serve():
            while not stopped.is_set():
                try: connection, _ = listener.accept()
                except socket.timeout: continue
                except OSError: return
                with connection:
                    connection.settimeout(1)
                    raw = bytearray()
                    while b"\n" not in raw:
                        part = connection.recv(65536)
                        if not part: break
                        raw.extend(part)
                    request = json.loads(raw)
                    requests.append(request)
                    value = reply(request, len(requests))
                    encoded = value if isinstance(value, bytes) else (json.dumps(value) + "\n").encode()
                    try:
                        size = max(1, len(encoded) // 8) if fragment_delay else len(encoded)
                        for start in range(0, len(encoded), size):
                            connection.sendall(encoded[start:start + size])
                            if fragment_delay: time.sleep(fragment_delay)
                    except (BrokenPipeError, ConnectionResetError): pass
        thread = threading.Thread(target=serve, daemon=True); thread.start()
        try:
            with mock.patch.object(host, "SOCKET_PATH", path): yield requests
        finally:
            stopped.set(); listener.close(); thread.join(2)
            if thread.is_alive(): raise AssertionError("synthetic provider did not stop")


def response(request, inode=123):
    return {"schema": host.SCHEMA, "canonical_paths": request["paths"],
            "mounts": [{"target": "/", "fsroot": "/", "maj:min": "8:1", "fstype": "ext4"}],
            "observed_at": "2026-11-13T00:00:00Z", "mount_namespace_inode": inode}


class HostObservationTests(unittest.TestCase):
    def setUp(self):
        self.runner = CommandRunner(filesystem_scope="host")
        self.marker = mock.patch.dict(os.environ, {"USL_OPERATIONS_CONTAINER": "1"})
        self.marker.start(); self.addCleanup(self.marker.stop)
        command = mock.patch.object(self.runner, "run", side_effect=AssertionError("image-local metadata must not be used"))
        command.start(); self.addCleanup(command.stop)

    def test_host_alias_and_late_host_mount_observations_replace_image_local_reads(self):
        def reply(request, number):
            value = response(request)
            value["canonical_paths"] = ["/runtime/capture" for _ in request["paths"]]
            if number == 2:
                value["mounts"].append({"target": "/host-only/late-bind", "fsroot": "/runtime/capture", "maj:min": "8:1", "fstype": "ext4"})
            return value
        with provider(reply) as requests:
            self.assertEqual(_canonical_filesystem_paths(ReadOnlyRunner(self.runner), ["/host-only/alias"]), ["/runtime/capture"])
            with self.assertRaisesRegex(RuntimeError, "mounted filesystem source"):
                _assert_capture_unmounted(self.runner, "/runtime/capture")
            _assert_capture_unmounted(self.runner, "/runtime/capture")
        self.assertEqual(len(requests), 3)
        self.assertTrue(all(set(request) == {"schema", "paths"} for request in requests))

    def test_requests_are_chunked_by_path_count_and_encoded_bytes_in_order(self):
        for paths in (["/capture/" + str(i) for i in range(260)], ["/" + "\x01" * 4094 for _ in range(8)]):
            with self.subTest(paths=len(paths)), provider(lambda request, number: response(request)) as requests:
                self.assertEqual(_canonical_filesystem_paths(self.runner, paths), paths)
                self.assertGreater(len(requests), 1)
                self.assertTrue(all(len(request["paths"]) <= 128 for request in requests))
                self.assertTrue(all(len(host._request(request["paths"])) <= host.MAX_REQUEST_BYTES for request in requests))

    def test_provider_errors_incomplete_or_malformed_responses_refuse_cleanup(self):
        variants = [lambda request: {"schema": host.SCHEMA, "error": "wrong_namespace"},
                    lambda request: {**response(request), "canonical_paths": []},
                    lambda request: {**response(request), "mount_namespace_inode": True},
                    lambda request: {**response(request), "mounts": []},
                    lambda request: b"incomplete",
                    lambda request: {"schema": host.SCHEMA, "error": {"private": "must not be reflected"}}]
        for variant in variants:
            with self.subTest(variant=variants.index(variant)), provider(lambda request, number: variant(request)):
                with self.assertRaisesRegex(RuntimeError, "host filesystem metadata"):
                    _assert_capture_unmounted(self.runner, "/capture")

    def test_continuous_fragments_cannot_reset_the_total_rpc_deadline(self):
        with provider(lambda request, number: response(request), fragment_delay=0.04), \
                mock.patch.object(host, "RPC_TIMEOUT_SECONDS", 0.1, create=True):
            with self.assertRaisesRegex(RuntimeError, "metadata.*deadline"):
                _canonical_filesystem_paths(self.runner, ["/capture"])

    def test_oversized_response_is_bounded_before_parsing(self):
        with provider(lambda request, number: b"x" * 1025 + b"\n"), mock.patch.object(host, "MAX_RESPONSE_BYTES", 1024):
            with self.assertRaisesRegex(RuntimeError, "metadata unavailable"):
                _canonical_filesystem_paths(self.runner, ["/capture"])

    def test_namespace_change_between_chunks_refuses_the_combined_observation(self):
        with provider(lambda request, number: response(request, inode=number)):
            with self.assertRaisesRegex(RuntimeError, "namespace changed"):
                _canonical_filesystem_paths(self.runner, ["/capture/" + str(i) for i in range(129)])

    def test_invalid_request_paths_never_reach_the_provider(self):
        for path in ("relative", "/nul\0", "/" + "x" * 4096):
            with self.subTest(path_length=len(path)), mock.patch.object(host, "_receive") as receive:
                with self.assertRaisesRegex(RuntimeError, "bounded absolute paths"):
                    _canonical_filesystem_paths(self.runner, [path])
                receive.assert_not_called()


class TargetScopeTests(unittest.TestCase):
    def test_baked_profiles_declare_host_and_development_placement(self):
        root = Path(__file__).resolve().parents[2] / "operations"
        for path, scope in ((root / "targets-host/production.json", "host"),
                            (root / "targets-host/staging.json", "host"),
                            (root / "targets/production.json", "host"),
                            (root / "targets/staging.json", "host"),
                            (root / "targets/local.json", "namespace")):
            target = load_target(path.stem, path.parent)
            self.assertEqual(target.runner().filesystem_scope, scope)

    def test_legacy_targets_remain_readable_and_invalid_declarations_are_actionable(self):
        root = Path(__file__).resolve().parents[2] / "operations/targets-host"
        value = json.loads((root / "staging.json").read_text())
        value.pop("filesystem_scope")
        self.assertNotIn("filesystem_scope", validate_target(value).value)
        for invalid in (False, [], "automatic"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(RuntimeError, "filesystem_scope"):
                validate_target({**value, "filesystem_scope": invalid})
