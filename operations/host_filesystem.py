"""Read host metadata without exposing the host filesystem to an operations image."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
import socket

from operations.runtime import CommandRunner, ReadOnlyRunner, RuntimeError

SCHEMA = "usl-host-filesystem-observe/v1"
SOCKET_PATH = "/var/lib/usl-odoo/host-filesystem-metadata/observe.sock"
MAX_PATHS = 128
MAX_PATH_BYTES = 4096
MAX_REQUEST_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
ERRORS = {"unauthorized", "invalid_request", "request_too_large", "wrong_namespace", "observation_failed", "deadline"}


def required(runner) -> bool:
    """Only real, host-bound local image executors need the host provider.

    SSH executes on the remote host; native host and explicit development
    namespaces already see their selected filesystems. Protocol-only test
    runners supply observations rather than executing commands.
    """
    while isinstance(runner, ReadOnlyRunner):
        runner = runner.inner
    if not isinstance(runner, CommandRunner):
        return False
    if runner.prefix and runner.prefix[0] == "ssh":
        return False
    if os.environ.get("USL_OPERATIONS_CONTAINER") != "1":
        return False
    if runner.filesystem_scope == "namespace":
        return False
    if runner.filesystem_scope != "host":
        raise RuntimeError("local image cleanup requires target.filesystem_scope=host or namespace")
    return True


def _request(paths: list[str]) -> bytes:
    return (json.dumps({"schema": SCHEMA, "paths": paths}, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def _chunks(paths: list[str]):
    current = []
    for path in paths:
        if not isinstance(path, str) or not path.startswith("/") or "\0" in path or len(path.encode()) > MAX_PATH_BYTES:
            raise RuntimeError("host filesystem metadata requires bounded absolute paths")
        if current and (len(current) == MAX_PATHS or len(_request([*current, path])) > MAX_REQUEST_BYTES):
            yield current
            current = []
        current.append(path)
    yield current


def _receive(paths: list[str]) -> dict:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(SOCKET_PATH)
            client.sendall(_request(paths))
            payload = bytearray()
            while b"\n" not in payload:
                part = client.recv(min(65536, MAX_RESPONSE_BYTES + 1 - len(payload)))
                if not part:
                    raise ValueError("incomplete response")
                payload.extend(part)
                if len(payload) > MAX_RESPONSE_BYTES:
                    raise ValueError("response too large")
            line, trailing = bytes(payload).split(b"\n", 1)
            if trailing.strip():
                raise ValueError("multiple responses")
            value = json.loads(line)
    except (OSError, ValueError, UnicodeError) as error:
        raise RuntimeError("host filesystem metadata unavailable; install and verify the root-only observation service before cleanup") from error
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise RuntimeError("host filesystem metadata response schema differs")
    if "error" in value:
        code = value["error"]
        if not isinstance(code, str) or code not in ERRORS or set(value) != {"schema", "error"}:
            raise RuntimeError("host filesystem metadata returned an invalid error")
        raise RuntimeError("host filesystem metadata refused observation: " + code)
    if set(value) != {"schema", "canonical_paths", "mounts", "observed_at", "mount_namespace_inode"}:
        raise RuntimeError("host filesystem metadata response fields differ")
    canonical = value["canonical_paths"]
    if not isinstance(canonical, list) or len(canonical) != len(paths) or any(
        not isinstance(path, str) or not path.startswith("/") or "\0" in path for path in canonical
    ):
        raise RuntimeError("host filesystem metadata canonical paths differ")
    if not isinstance(value["mounts"], list) or not value["mounts"] or any(
        not isinstance(mount, dict) or set(mount) != {"target", "fsroot", "maj:min", "fstype"}
        or any(not isinstance(field, str) for field in mount.values()) for mount in value["mounts"]
    ):
        raise RuntimeError("host filesystem metadata mount inventory differs")
    inode = value["mount_namespace_inode"]
    try:
        observed = datetime.fromisoformat(value["observed_at"].replace("Z", "+00:00"))
        if observed.tzinfo is None or observed.utcoffset() != timedelta(0):
            raise ValueError("UTC timestamp required")
    except (ValueError, TypeError, AttributeError) as error:
        raise RuntimeError("host filesystem metadata observation time differs") from error
    if not isinstance(inode, int) or isinstance(inode, bool) or inode <= 0:
        raise RuntimeError("host filesystem metadata namespace identity differs")
    return value


def observe(runner, paths: list[str]) -> dict | None:
    if not required(runner):
        return None
    canonical = []
    result = None
    for chunk in _chunks(paths):
        observed = _receive(chunk)
        if result is not None and observed["mount_namespace_inode"] != result["mount_namespace_inode"]:
            raise RuntimeError("host filesystem metadata namespace changed during observation")
        canonical.extend(observed["canonical_paths"])
        result = observed
    return {**result, "canonical_paths": canonical}
