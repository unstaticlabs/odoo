"""Validated runtime targets and exact Docker Compose ownership checks."""

from __future__ import annotations

import json
import re
import select
import shlex
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from operations.release_manifest import ReleaseManifestError
from operations.staging_lifecycle import LifecycleError, validate_lifecycle
from operations.release_manifest import validate as validate_release

SCHEMA = "usl-runtime/v1"
TARGET_NAME = re.compile(r"[a-z][a-z0-9-]{1,31}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SECRET_KEY = re.compile(r"[A-Z][A-Z0-9_]*\Z")
RESERVED_SECRET_KEYS = {
    "COMPOSE_FILE",
    "COMPOSE_PROJECT_NAME",
    "ODOO_DB_NAME",
    "ODOO_DB_HOST",
    "ODOO_DB_PORT",
    "ODOO_HTTP_PORT",
    "ODOO_GEVENT_PORT",
    "ODOO_IMAGE",
    "ODOO_PUBLIC_BASE_URL",
    "ODOO_MCP_IMAGE",
    "ODOO_MCP_PUBLIC_ORIGIN",
    "PAPERLESS_DB_NAME",
    "PAPERLESS_DB_HOST",
    "PAPERLESS_DB_PORT",
    "PAPERLESS_HTTP_PORT",
    "PAPERLESS_IMAGE",
    "PAPERLESS_PUBLIC_URL",
    "RESTIC_REPOSITORY",
    "USL_BACKUP_CACHE_REPOSITORY",
    "USL_EXTERNAL_INGRESS_NETWORK",
    "USL_EXTERNAL_OLLAMA_NETWORK",
}
RESERVED_SECRET_SUFFIXES = ("_IMAGE", "_NETWORK", "_PORT", "_URL", "_VOLUME")


class RuntimeError(RuntimeError):
    """A target, secret binding, or Docker resource is unsafe."""


class Runner(Protocol):
    def run(
        self,
        command: list[str],
        *,
        check: bool = True,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]: ...


@dataclass
class CommandRunner:
    prefix: tuple[str, ...] = ()
    deadline_monotonic: float | None = None

    @contextmanager
    def advisory_lock(self, path: str, timeout: float = 30.0, wait: float = 0.0):
        """Hold ``flock`` on ``path``.  Fails at once unless ``wait`` seconds are allowed."""
        mode = ["--timeout", str(wait)] if wait > 0 else ["--nonblock"]
        timeout = max(timeout, wait + 10.0)
        command = [
            "flock", *mode, path, "sh", "-c", "printf 'locked\\n'; cat >/dev/null",
        ]
        invocation = [*self.prefix, *command]
        if self.prefix and self.prefix[0] == "ssh":
            invocation = [self.prefix[0], self.prefix[1], "--", shlex.join(command)]
        process = subprocess.Popen(
            invocation, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        readable, _, _ = select.select([process.stdout], [], [], timeout)
        if not readable:
            process.terminate()
            process.wait(timeout=10)
            raise RuntimeError(f"advisory lock acquisition timed out: {path}")
        ready = process.stdout.readline().strip() if process.stdout is not None else ""
        if ready != "locked":
            stderr = process.stderr.read().strip() if process.stderr is not None else ""
            process.wait()
            raise RuntimeError(f"another operation owns advisory lock: {path}: {stderr}")
        try:
            yield
        finally:
            if process.stdin is not None:
                process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)

    def run(
        self,
        command: list[str],
        *,
        check: bool = True,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        invocation = [*self.prefix, *command]
        if self.prefix and self.prefix[0] == "ssh":
            invocation = [self.prefix[0], self.prefix[1], "--", shlex.join(command)]
        timeout = None
        if self.deadline_monotonic is not None:
            timeout = self.deadline_monotonic - time.monotonic()
            if timeout <= 0:
                raise RuntimeError("command refused: operation deadline expired")
        try:
            process = subprocess.run(
                invocation,
                check=False,
                capture_output=True,
                text=True,
                input=input_text,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("command exceeded the operation deadline") from error
        if check and process.returncode:
            detail = (
                "subprocess output redacted because standard input was sensitive"
                if input_text is not None else (process.stderr or process.stdout).strip()
            )
            raise RuntimeError(f"command failed ({' '.join(command)}): {detail}")
        return process


# Observation ------------------------------------------------------------
#
# `scripts/usl-stack-observe` runs the same handlers as `scripts/usl-stack`,
# so "read-only" cannot rest on which subcommand was chosen: health and smoke
# once reached `_with_mcp_runtime_authority`, which creates a directory and
# writes an authority override on the host. Under `observation_only()` every
# runner a target hands out refuses, before execution, any command outside a
# short list of reads. A handler that tries to provision, pull, lock or write
# fails with the refused verb instead of doing it.

_OBSERVATION_ONLY = False

_READ_ONLY_HOST_COMMANDS = frozenset({
    "cat", "test", "stat", "df", "du", "findmnt", "sha256sum", "readlink",
})
_READ_ONLY_DOCKER_VERBS = frozenset({"ps", "inspect", "logs", "info"})
_READ_ONLY_DOCKER_OBJECT_VERBS = frozenset({"inspect", "ls"})
_DOCKER_OBJECTS = frozenset({"image", "volume", "network", "container"})
_COMPOSE_GLOBAL_OPTIONS = frozenset({
    "--project-name", "--project-directory", "--env-file", "--file", "--profile",
})
_COMPOSE_CONFIG_ARGUMENTS = frozenset({"--services", "--format", "json", "--images", "--quiet"})
# Exactly the options the stack's curl probes use; anything else, including
# every short or joined form, is refused rather than interpreted.
_CURL_FLAGS = frozenset({
    "--silent", "--show-error", "--fail", "--include", "--no-buffer", "--http1.1",
})
_CURL_VALUED = frozenset({"--write-out", "--max-time", "--header", "--output"})
_READ_ONLY_SQL = re.compile(r"\s*(?:SELECT|WITH)\b", re.IGNORECASE)
_SQL_WRITE = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|MERGE|TRUNCATE|ALTER|DROP|CREATE|GRANT|REVOKE|COPY|"
    r"VACUUM|REINDEX|CLUSTER|LOCK|NOTIFY|LISTEN|CALL|DO|SET|RESET|COMMENT|REFRESH|"
    r"PREPARE|EXECUTE|DISCARD|IMPORT|SECURITY|INTO)\b"
    r"|\b(?:pg_terminate_backend|pg_cancel_backend|pg_reload_conf|pg_rotate_logfile|"
    r"pg_switch_wal|pg_promote|pg_file_\w+|pg_advisory\w*|pg_create\w*|pg_drop\w*|"
    r"set_config|nextval|setval|lo_\w+|dblink\w*|pg_stat_reset\w*|pg_logical_emit_message|"
    r"pg_replication_slot_advance|pg_notify|pg_logical_slot_\w+|pg_replication_origin_\w+|"
    r"pg_backup_\w+|pg_wal_replay_\w+|pg_start_backup|pg_stop_backup|pg_import_system_collations|"
    r"pg_sequence\w*|txid_\w+|pg_current_xact_id\w*)\s*\(",
    re.IGNORECASE,
)
# Calls a probe program must not make: file-system, process and HTTP writes.
_PYTHON_WRITE_CALLS = frozenset({
    "remove", "unlink", "rmdir", "removedirs", "mkdir", "makedirs", "rename",
    "renames", "write", "writelines", "write_text", "write_bytes", "chmod", "chown",
    "lchown", "symlink", "symlink_to", "link", "hardlink_to", "truncate", "ftruncate",
    "rmtree", "move", "copy", "copy2", "copyfile", "copytree", "system", "popen",
    "execv", "execve", "execl", "execlp", "execvp", "spawnv", "kill", "fork", "touch",
    "mkfifo", "mknod", "utime", "post", "put", "patch", "delete", "exec", "eval",
    "compile", "__import__", "setattr", "getattr", "dup2", "FileIO",
})
# The only POST bodies a probe may send: the Sign DSS health check and the
# shared Ollama embedding. Both compute an answer and store nothing.
_PYTHON_POST_PATHS = frozenset({"/v1/health", "/api/embed"})
_PYTHON_WRITE_MODULES = frozenset({
    "subprocess", "shutil", "ctypes", "multiprocessing", "pty", "tempfile", "sqlite3",
    "psycopg2", "importlib", "http", "requests",
})


_PYTHON_OPEN_CALLS = frozenset({"open", "GzipFile", "BZ2File", "LZMAFile", "TarFile", "ZipFile"})


def _is_process_call(name: str) -> bool:
    return name.startswith(("exec", "spawn", "posix_spawn", "fork"))


def _python_probe_problem(program: str) -> str | None:
    """Refuse a probe program that could write, judged from its syntax tree."""
    import ast

    try:
        tree = ast.parse(program)
    except SyntaxError:
        return "python -c program that does not parse"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names]
            if isinstance(node, ast.ImportFrom):
                # `from os import remove as r` would hide the call name.
                if any(
                    name in _PYTHON_WRITE_CALLS or name in {"*", "open", "replace"} or _is_process_call(name)
                    for name in names
                ):
                    return f"python -c importing {names[0]}"
                names = [node.module or ""]
            for name in names:
                if name.split(".")[0] in _PYTHON_WRITE_MODULES:
                    return f"python -c importing {name}"
        # Naming a write function at all, called or not (`f = os.remove`),
        # and reaching around names (`os.__dict__[...]`) are refused.
        if isinstance(node, ast.Attribute) and (
            node.attr in _PYTHON_WRITE_CALLS or node.attr.startswith("__") or _is_process_call(node.attr)
        ):
            return f"python -c referencing {node.attr}"
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "replace"
            and isinstance(node.value, ast.Name)
            and node.value.id in {"os", "shutil"}
        ):
            return "python -c referencing os.replace"
        if isinstance(node, ast.Name) and (node.id in _PYTHON_WRITE_CALLS or _is_process_call(node.id)):
            return f"python -c referencing {node.id}"
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Attribute) for target in targets):
                # `request.data = ...` or `request.method = ...` after the fact.
                return "python -c assigning an attribute"
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", None)
        if name in _PYTHON_OPEN_CALLS:
            # Judged without trusting the receiver's name (`import os as o`):
            # a write mode in either of the first two positions or in mode=,
            # or a second positional argument that is not a constant read
            # mode (os.open flags), is refused.
            candidates = list(node.args[:2]) + [
                keyword.value for keyword in node.keywords if keyword.arg in {"mode", "flags"}
            ]
            for value in candidates:
                if isinstance(value, ast.Constant) and isinstance(value.value, str) and set(value.value) & set("wax+"):
                    return "python -c opening a file for writing"
            second = node.args[1] if len(node.args) > 1 else None
            if second is not None and not (
                isinstance(second, ast.Constant)
                and isinstance(second.value, str)
                and not set(second.value) & set("wax+")
            ):
                return "python -c opening a file with a non-constant mode"
            continue
        if name == "replace" and isinstance(function, ast.Attribute):
            # str.replace(old, new) is a read; os.replace(a, b) and
            # Path.replace(target) move files.
            if (isinstance(function.value, ast.Name) and function.value.id == "os") or len(node.args) < 2:
                return "python -c calling a file replace"
            continue
        if name in _PYTHON_WRITE_CALLS:
            return f"python -c calling {name}"
        if name == "Request" and any(
            keyword.arg == "method"
            and not (isinstance(keyword.value, ast.Constant) and keyword.value.value in {"GET", "HEAD"})
            for keyword in node.keywords
        ):
            return "python -c sending a non-GET request"
        sends_body = len(node.args) > 1 or any(keyword.arg == "data" for keyword in node.keywords)
        if name == "urlopen" and sends_body:
            return "python -c posting through urlopen"
        if name == "request":
            # requests.request / httpx.request / client.request(method, ...)
            return "python -c sending a request by method name"
        if name == "Request" and sends_body:
            # The URL must be `<origin variable> + '<reviewed path>'`, so no
            # other path can be prefixed onto a reviewed suffix.
            url = node.args[0] if node.args else None
            if not (
                isinstance(url, ast.BinOp)
                and isinstance(url.op, ast.Add)
                and isinstance(url.left, ast.Name)
                and isinstance(url.right, ast.Constant)
                and url.right.value in _PYTHON_POST_PATHS
            ):
                return "python -c posting to an unreviewed endpoint"
    return None


def _psql_problem(program: list[str]) -> str | None:
    if (
        len(program) != 9
        or program[1] != "--username"
        or program[3] != "--dbname"
        or program[5:8] != ["--tuples-only", "--no-align", "--command"]
    ):
        return "psql with arguments outside the stack's probe shape"
    query = program[8]
    if not _READ_ONLY_SQL.match(query) or ";" in query.strip().rstrip(";"):
        return "psql with a query that is not a single SELECT"
    # String literals are data ('create' as an ACL label), not statements.
    match = _SQL_WRITE.search(re.sub(r"'(?:[^']|'')*'", "''", query))
    if match:
        return f"psql with a query containing {match.group(0).strip().rstrip('(').strip()}"
    return None


def _compose_problem(command: list[str]) -> str | None:
    index = 2
    while index < len(command) and command[index] in _COMPOSE_GLOBAL_OPTIONS:
        index += 2
    rest = command[index:]
    if not rest:
        return "docker compose without a subcommand"
    verb = rest[0]
    if verb in {"ps", "images", "ls"}:
        return None
    if verb == "config":
        unexpected = [argument for argument in rest[1:] if argument not in _COMPOSE_CONFIG_ARGUMENTS]
        return f"docker compose config {unexpected[0]}" if unexpected else None
    if verb != "exec":
        return f"docker compose {verb}"
    arguments = rest[1:]
    while arguments and arguments[0] in {"--no-TTY", "-T"}:
        arguments = arguments[1:]
    if len(arguments) < 2 or arguments[0].startswith("-"):
        return "docker compose exec without a service and a probe"
    program = arguments[1:]
    if program[0] == "psql":
        return _psql_problem(program)
    if program[:2] == ["python", "-c"] and len(program) == 3:
        return _python_probe_problem(program[2])
    return f"docker compose exec {program[0]}"


def _curl_problem(command: list[str]) -> str | None:
    arguments = command[1:]
    urls = 0
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in _CURL_FLAGS:
            index += 1
            continue
        if argument in _CURL_VALUED:
            if index + 1 >= len(arguments):
                return f"curl {argument} without a value"
            if argument == "--output" and arguments[index + 1] != "/dev/null":
                return "curl --output to a file"
            if argument == "--write-out" and "%output" in arguments[index + 1]:
                return "curl --write-out to a file"
            index += 2
            continue
        if re.fullmatch(r"https?://[^\s]+", argument):
            urls += 1
            index += 1
            continue
        return f"curl {argument.split('=', 1)[0]}"
    return None if urls == 1 else "curl without exactly one URL"


def read_only_command_problem(command: list[str], input_text: str | None = None) -> str | None:
    """Return why ``command`` may write, or None when it is a known read."""
    if input_text is not None:
        return f"{command[0] if command else 'command'} with standard input"
    if not command:
        return "empty command"
    head = command[0]
    if head in _READ_ONLY_HOST_COMMANDS:
        return None
    if command == ["containerd", "config", "dump"]:
        return None
    if head == "curl":
        return _curl_problem(command)
    if head != "docker" or len(command) < 2:
        return head
    verb = command[1]
    if verb == "compose":
        return _compose_problem(command)
    if verb in _READ_ONLY_DOCKER_VERBS:
        return None
    if verb in _DOCKER_OBJECTS and len(command) > 2 and command[2] in _READ_ONLY_DOCKER_OBJECT_VERBS:
        return None
    if (
        verb == "run"
        and command[2:8] == ["--rm", "--network", "none", "--read-only", "--entrypoint", "cat"]
        and len(command) == 10
        and re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", command[8])
    ):
        # An ephemeral, networkless, read-only container that prints one file
        # of an immutable image: how the cron policy shipped in a release is read.
        return None
    return f"docker {verb}" + (f" {command[2]}" if verb in _DOCKER_OBJECTS and len(command) > 2 else "")


@dataclass
class ReadOnlyRunner:
    """A runner that executes only reads; anything else is refused unexecuted."""

    inner: Runner
    read_only: bool = True

    def run(
        self,
        command: list[str],
        *,
        check: bool = True,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        problem = read_only_command_problem(command, input_text)
        if problem is not None:
            raise RuntimeError(f"observation refused a write-capable command: {problem}")
        return self.inner.run(command, check=check, input_text=input_text)

    def run_reviewed_read(
        self,
        command: list[str],
        *,
        check: bool = True,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run one program that is a read by review, not by its shape.

        The release-announcement audit is an ``odoo shell`` session: it can
        write in principle, and reads in practice because its fixed program
        never commits and the shell rolls back at exit. It is the only caller;
        ``scripts/tests/test_observation_read_only.py`` fails if another appears.
        """
        return self.inner.run(command, check=check, input_text=input_text)

    @contextmanager
    def advisory_lock(self, path: str, timeout: float = 30.0, wait: float = 0.0):
        raise RuntimeError(f"observation refused a write-capable command: flock {path}")
        yield  # pragma: no cover


@contextmanager
def observation_only():
    """Make every target runner read-only for the duration of the block."""
    global _OBSERVATION_ONLY
    previous = _OBSERVATION_ONLY
    _OBSERVATION_ONLY = True
    try:
        yield
    finally:
        _OBSERVATION_ONLY = previous


def _exact(value: object, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise RuntimeError(f"{label} fields differ: {actual}")
    return value


def _strings(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise RuntimeError(f"{label} must be a non-empty string list")
    return value


@dataclass(frozen=True)
class Target:
    path: Path
    value: dict[str, Any]

    @property
    def name(self) -> str:
        return self.value["name"]

    @property
    def project(self) -> str:
        return self.value["compose"]["project"]

    @property
    def protected(self) -> bool:
        return self.value["protected"]

    def runner(self) -> CommandRunner | ReadOnlyRunner:
        transport = self.value["transport"]
        if transport["type"] == "local":
            runner = CommandRunner()
        else:
            runner = CommandRunner(("ssh", transport["host"], "--"))
        return ReadOnlyRunner(runner) if _OBSERVATION_ONLY else runner


def validate_target(payload: object, path: Path = Path("<memory>")) -> Target:
    root = _exact(
        payload,
        {
            "schema",
            "name",
            "environment",
            "protected",
            "transport",
            "compose",
            "services",
            "databases",
            "storage",
            "volumes",
            "paths",
            "external_networks",
            "endpoints",
            "admission_endpoints",
            "ingress",
            "ollama",
            "backup",
            "release_manifest",
            "plan_signing",
            "cron_policy",
            "secrets",
            "state_directory",
            "lifecycle",
        },
        "target",
    )
    if root["schema"] != SCHEMA:
        raise RuntimeError(f"unsupported target schema: {root['schema']!r}")
    if not TARGET_NAME.fullmatch(str(root["name"])):
        raise RuntimeError("target.name is invalid")
    if root["environment"] not in {"production", "staging", "local"}:
        raise RuntimeError("target.environment is invalid")
    if not isinstance(root["protected"], bool):
        raise RuntimeError("target.protected must be boolean")

    transport = root["transport"]
    if not isinstance(transport, dict) or transport.get("type") not in {"local", "ssh"}:
        raise RuntimeError("transport.type must be local or ssh")
    expected_transport = {"type"} if transport.get("type") == "local" else {"type", "host"}
    _exact(transport, expected_transport, "transport")
    if transport.get("type") == "ssh" and not TARGET_NAME.fullmatch(str(transport.get("host"))):
        raise RuntimeError("transport.host is invalid")

    compose_fields = {
        "project", "anchor_service", "profiles", "default_network", "resource_overlay",
    }
    if isinstance(root["compose"], dict) and "adoption" in root["compose"]:
        compose_fields.add("adoption")
    if isinstance(root["compose"], dict) and "canonical" in root["compose"]:
        compose_fields.add("canonical")
    compose = _exact(root["compose"], compose_fields, "compose")
    if not TARGET_NAME.fullmatch(str(compose["project"])):
        raise RuntimeError("compose.project is invalid")
    if not isinstance(compose["anchor_service"], str) or not compose["anchor_service"]:
        raise RuntimeError("compose.anchor_service is required")
    if not isinstance(compose["profiles"], list) or not all(
        isinstance(item, str) and item for item in compose["profiles"]
    ):
        raise RuntimeError("compose.profiles must be a string list")
    if not isinstance(compose["default_network"], str) or not compose["default_network"]:
        raise RuntimeError("compose.default_network is required")
    resource_overlay = compose["resource_overlay"]
    if resource_overlay is not None and (
        not isinstance(resource_overlay, str)
        or resource_overlay.startswith("/")
        or ".." in Path(resource_overlay).parts
        or not resource_overlay.endswith(".json")
    ):
        raise RuntimeError("compose.resource_overlay must be a safe relative JSON path")
    canonical = compose.get("canonical")
    if canonical is not None:
        canonical = _exact(
            canonical,
            {"working_directory", "compose_files", "environment_file"},
            "compose.canonical",
        )
        working = canonical["working_directory"]
        if (
            not isinstance(working, str)
            or not working
            or working.startswith("/")
            or ".." in Path(working).parts
        ):
            raise RuntimeError("compose.canonical.working_directory must be relative")
        files = canonical["compose_files"]
        if (
            not isinstance(files, list)
            or not files
            or not all(
                isinstance(item, str)
                and bool(item)
                and not item.startswith("/")
                and ".." not in Path(item).parts
                for item in files
            )
        ):
            raise RuntimeError("compose.canonical.compose_files must be safe relative paths")
        environment_file = canonical["environment_file"]
        if not isinstance(environment_file, str) or not environment_file.startswith("/"):
            raise RuntimeError("compose.canonical.environment_file must be absolute")
    adoption = compose.get("adoption")
    if adoption is not None:
        if root["name"] != "staging" or root["environment"] != "staging":
            raise RuntimeError("compose.adoption is staging-only")
        adoption = _exact(
            adoption,
            {"schema", "legacy_anchor_service", "legacy_release_schema", "candidate"},
            "compose.adoption",
        )
        if adoption["schema"] != "usl-compose-adoption/v1":
            raise RuntimeError("compose.adoption schema is invalid")
        if adoption["legacy_anchor_service"] == compose["anchor_service"]:
            raise RuntimeError("compose.adoption legacy anchor must differ")
        if adoption["legacy_release_schema"] != "usl-release/v2":
            raise RuntimeError("compose.adoption release schema is invalid")
        candidate = _exact(
            adoption["candidate"],
            {"working_directory", "compose_files", "environment_file"},
            "compose.adoption.candidate",
        )
        for field in ("working_directory", "environment_file"):
            if not isinstance(candidate[field], str) or not candidate[field].startswith("/"):
                raise RuntimeError(f"compose.adoption.candidate.{field} must be absolute")
        if (
            not isinstance(candidate["compose_files"], list)
            or not candidate["compose_files"]
            or not all(
                isinstance(item, str) and item.startswith("/")
                for item in candidate["compose_files"]
            )
        ):
            raise RuntimeError("compose.adoption.candidate.compose_files must be absolute paths")
        approved_working_root = "/etc/komodo/stacks/usl-odoo-production-main/"
        if not candidate["working_directory"].startswith(approved_working_root):
            raise RuntimeError("compose.adoption candidate working directory is outside GitOps")
        if any(
            not item.startswith(candidate["working_directory"] + "/")
            for item in candidate["compose_files"]
        ):
            raise RuntimeError("compose.adoption candidate file is outside its working directory")
        if not candidate["environment_file"].startswith("/opt/usl-odoo/staging/"):
            raise RuntimeError("compose.adoption candidate environment is outside staging")

    services = root["services"]
    required_services = {
        "odoo",
        "odoo_db",
        "paperless",
        "paperless_db",
        "paperless_broker",
        "paperless_gotenberg",
        "paperless_tika",
        "mcp",
        "renderer",
        "receipt_fetcher",
        "receipt_egress",
        "sign",
        "sign_ca",
    }
    _exact(services, required_services, "services")
    if not all(isinstance(value, str) and value for value in services.values()):
        raise RuntimeError("service names must be non-empty strings")

    databases = _exact(root["databases"], {"odoo", "paperless"}, "databases")
    for name, database in databases.items():
        _exact(database, {"service", "name", "user", "password_key"}, f"databases.{name}")
        if database["service"] not in services.values():
            raise RuntimeError(f"databases.{name}.service is not declared")
        for field in ("name", "user", "password_key"):
            if not isinstance(database[field], str) or not database[field]:
                raise RuntimeError(f"databases.{name}.{field} is required")
        if not SECRET_KEY.fullmatch(database["password_key"]):
            raise RuntimeError(f"databases.{name}.password_key is invalid")

    storage = _exact(root["storage"], {"tiers"}, "storage")
    tiers = storage["tiers"]
    if not isinstance(tiers, dict) or set(tiers) != {"bulk", "database", "local"}:
        raise RuntimeError("storage.tiers must declare bulk, database, and local")
    for name, tier in tiers.items():
        _exact(tier, {"path", "reserve_bytes"}, f"storage.tiers.{name}")
        if not isinstance(tier["path"], str) or not tier["path"].startswith("/"):
            raise RuntimeError(f"storage.tiers.{name}.path must be absolute")
        if type(tier["reserve_bytes"]) is not int or tier["reserve_bytes"] < 0:
            raise RuntimeError(f"storage.tiers.{name}.reserve_bytes must be non-negative")
    if len({tier["path"] for tier in tiers.values()}) != len(tiers):
        raise RuntimeError("storage tier paths must be distinct")

    volumes = root["volumes"]
    required_volumes = {
        "odoo_filestore",
        "paperless_media",
        "paperless_data",
        "paperless_trash",
        "paperless_consume",
        "odoo_postgres",
        "paperless_postgres",
        "paperless_broker",
        "paperless_export",
        "mcp_oauth",
        "receipt_control",
    }
    if not isinstance(volumes, dict) or not required_volumes <= set(volumes):
        raise RuntimeError(f"target volumes must include {sorted(required_volumes)}")
    for role, volume in volumes.items():
        _exact(volume, {"name", "class", "tier"}, f"volumes.{role}")
        if volume["class"] not in {"durable", "cache", "transient"}:
            raise RuntimeError(f"volumes.{role}.class is invalid")
        if not isinstance(volume["name"], str) or not volume["name"]:
            raise RuntimeError(f"volumes.{role}.name is required")
        if volume["tier"] not in tiers:
            raise RuntimeError(f"volumes.{role}.tier is invalid")

    paths = root["paths"]
    required_paths = {"sign_secrets", "sign_evidence"}
    if not isinstance(paths, dict) or not required_paths <= set(paths):
        raise RuntimeError(f"target paths must include {sorted(required_paths)}")
    for role, definition in paths.items():
        _exact(definition, {"path", "class", "required", "tier"} | ({"files"} if "files" in definition else set()), f"paths.{role}")
        if "files" in definition:
            files = definition["files"]
            if role != "mcp_secrets" or not isinstance(files, dict) or not files:
                raise RuntimeError(f"paths.{role}.files must map MCP recovery filenames to source paths")
            for name, source in files.items():
                if not isinstance(name, str) or Path(name).name != name or name in {".", ".."}:
                    raise RuntimeError(f"paths.{role}.files contains an invalid filename")
                if not isinstance(source, str) or not source.startswith("/"):
                    raise RuntimeError(f"paths.{role}.files source must be absolute")
        if definition["class"] not in {"durable", "cache", "transient"}:
            raise RuntimeError(f"paths.{role}.class is invalid")
        if not isinstance(definition["path"], str) or not definition["path"].startswith("/"):
            raise RuntimeError(f"paths.{role}.path must be absolute")
        if not isinstance(definition["required"], bool):
            raise RuntimeError(f"paths.{role}.required must be boolean")
        if definition["tier"] not in tiers:
            raise RuntimeError(f"paths.{role}.tier is invalid")
    sign_root = Path(paths["sign_secrets"]["path"])
    evidence_root = Path(paths["sign_evidence"]["path"])
    if sign_root == evidence_root or sign_root in evidence_root.parents or evidence_root in sign_root.parents:
        raise RuntimeError("Sign secrets and evidence paths must not overlap")

    if not isinstance(root["external_networks"], dict) or not all(
        isinstance(key, str) and isinstance(value, str) and key and value
        for key, value in root["external_networks"].items()
    ):
        raise RuntimeError("external_networks must map roles to names")
    endpoint_fields = {"odoo", "paperless", "mcp"}
    if (
        not isinstance(root["endpoints"], dict)
        or set(root["endpoints"]) != endpoint_fields
        or not all(
            isinstance(value, str) and value.startswith(("http://", "https://"))
            for value in root["endpoints"].values()
        )
    ):
        raise RuntimeError("endpoints must contain the public HTTP URLs")
    admission_fields = {*endpoint_fields, "odoo_websocket"}
    if (
        not isinstance(root["admission_endpoints"], dict)
        or set(root["admission_endpoints"]) != admission_fields
        or not all(
            isinstance(value, str) and value.startswith(("http://", "https://"))
            for value in root["admission_endpoints"].values()
        )
    ):
        raise RuntimeError("admission_endpoints must contain the internal HTTP URLs")
    if root["environment"] in {"production", "staging"} and any(
        urlsplit(value).hostname != "127.0.0.1"
        for value in root["admission_endpoints"].values()
    ):
        raise RuntimeError("remote admission endpoints must use target-host loopback")

    ingress = _exact(
        root["ingress"],
        {"proxy_mode", "list_db", "dbfilter", "websocket"},
        "ingress",
    )
    for field in ("proxy_mode", "list_db", "websocket"):
        if not isinstance(ingress[field], bool):
            raise RuntimeError(f"ingress.{field} must be boolean")
    if not isinstance(ingress["dbfilter"], str) or not ingress["dbfilter"]:
        raise RuntimeError("ingress.dbfilter is required")
    if ingress["proxy_mode"] and not root["endpoints"]["odoo"].startswith("https://"):
        raise RuntimeError("proxy-mode targets require a public HTTPS Odoo endpoint")

    ollama = _exact(
        root["ollama"],
        {"mode", "container", "network", "url", "model", "manifest_sha256", "dimension"},
        "ollama",
    )
    if ollama["mode"] not in {"native", "external"}:
        raise RuntimeError("ollama.mode must be native or external")
    if not SHA256.fullmatch(str(ollama["manifest_sha256"])) or ollama["dimension"] != 1024:
        raise RuntimeError("Ollama identity is invalid")

    backup = _exact(root["backup"], {"durable_repository", "cache_repository"}, "backup")
    for key, value in backup.items():
        local_repository = (
            root["environment"] == "staging"
            and value == f"/var/lib/usl-odoo/restic/staging/{key.removesuffix('_repository')}"
        )
        if not isinstance(value, str) or not (value.startswith(("s3:", "rest:")) or local_repository):
            raise RuntimeError(f"backup.{key} is not a supported Restic repository")
    if backup["durable_repository"] == backup["cache_repository"]:
        raise RuntimeError("durable and cache repositories must differ")
    if not isinstance(root["release_manifest"], str) or not root["release_manifest"].startswith("/"):
        raise RuntimeError("release_manifest must be absolute")

    plan_signing = _exact(root["plan_signing"], {"private_key", "public_key"}, "plan_signing")
    for field, value in plan_signing.items():
        if value is not None and (not isinstance(value, str) or not value.startswith("/")):
            raise RuntimeError(f"plan_signing.{field} must be an absolute path or null")
    if root["environment"] == "staging" and not all(plan_signing.values()):
        raise RuntimeError("staging requires plan signing and verification keys")
    if root["environment"] == "production" and not plan_signing["public_key"]:
        raise RuntimeError("production requires the staging plan verification key")
    if root["environment"] == "production" and plan_signing["private_key"] is not None:
        raise RuntimeError("production must not receive the staging plan signing key")

    cron_policy = _exact(root["cron_policy"], {"mode", "path", "gates"}, "cron_policy")
    if cron_policy["mode"] not in {"managed", "neutralized", "unmanaged"}:
        raise RuntimeError("cron_policy.mode is invalid")
    if cron_policy["mode"] == "unmanaged":
        if cron_policy["path"] is not None or cron_policy["gates"] != {}:
            raise RuntimeError("unmanaged cron policy must not declare a path or gates")
    else:
        if not isinstance(cron_policy["path"], str) or not cron_policy["path"].startswith("/"):
            raise RuntimeError("managed cron policy path must be absolute")
        if not isinstance(cron_policy["gates"], dict) or any(
            not isinstance(key, str) or type(value) is not bool
            for key, value in cron_policy["gates"].items()
        ):
            raise RuntimeError("cron policy gates must be boolean decisions")

    secrets = _exact(root["secrets"], {"env_file", "allowed_keys"}, "secrets")
    if not isinstance(secrets["env_file"], str) or not secrets["env_file"]:
        raise RuntimeError("secrets.env_file is required")
    allowed = _strings(secrets["allowed_keys"], "secrets.allowed_keys")
    for key in allowed:
        if not SECRET_KEY.fullmatch(key) or is_topology_key(key):
            raise RuntimeError(f"secret allowlist contains a topology key: {key}")
    if not isinstance(root["state_directory"], str) or not root["state_directory"].startswith("/"):
        raise RuntimeError("state_directory must be absolute")
    try:
        validate_lifecycle(root["lifecycle"])
    except LifecycleError as error:
        raise RuntimeError(str(error)) from error
    if root["lifecycle"]["mode"] == "on_demand" and root["environment"] != "staging":
        raise RuntimeError("only staging may be put to sleep: on_demand lifecycle is not allowed here")
    return Target(path, root)


def load_target(name: str, directory: Path) -> Target:
    path = directory / f"{name}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read target definition: {path}") from error
    return validate_target(payload, path)


def is_topology_key(key: str) -> bool:
    return key in RESERVED_SECRET_KEYS or key.endswith(RESERVED_SECRET_SUFFIXES)


def validate_secret_text(text: str, allowed_keys: list[str]) -> list[str]:
    found: list[str] = []
    allowed = set(allowed_keys)
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError(f"secret line {number} is not KEY=VALUE")
        key = line.split("=", 1)[0].strip()
        if not SECRET_KEY.fullmatch(key):
            raise RuntimeError(f"secret line {number} has an invalid key")
        if is_topology_key(key):
            raise RuntimeError(f"secret file contains topology key: {key}")
        if key not in allowed:
            raise RuntimeError(f"secret key is not allowlisted: {key}")
        found.append(key)
    return sorted(found)


def _json_lines(output: str) -> list[dict[str, Any]]:
    values = []
    for line in output.splitlines():
        if line.strip():
            value = json.loads(line)
            values.extend(value if isinstance(value, list) else [value])
    return values


def compose_identity(target: Target, runner: Runner) -> dict[str, Any]:
    anchor = target.value["compose"]["anchor_service"]

    def service_containers(service: str) -> list[str]:
        process = runner.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"label=com.docker.compose.project={target.project}",
                "--filter",
                f"label=com.docker.compose.service={service}",
                "--format",
                "{{.ID}}",
            ],
        )
        return [line for line in process.stdout.splitlines() if line]

    identifiers = service_containers(anchor)
    selected_anchor = anchor
    active_state = None
    active_release_schema = None
    adoption = target.value["compose"].get("adoption")
    staging_transition = adoption is not None
    if staging_transition:
        legacy_anchor = adoption["legacy_anchor_service"]
        legacy_identifiers = service_containers(legacy_anchor)
        if identifiers and legacy_identifiers:
            active_state = read_active_state(target, runner)
            if active_state is not None:
                active_release_schema = read_active_release(target, runner, active_state)["schema"]
            if active_release_schema != "usl-release/v3":
                raise RuntimeError(
                    f"both canonical and legacy anchors exist for {target.project}",
                )
        elif not identifiers and len(legacy_identifiers) == 1:
            active_state = read_active_state(target, runner)
            if active_state is not None:
                active_release_schema = read_active_release(target, runner, active_state)["schema"]
            if active_release_schema != adoption["legacy_release_schema"]:
                raise RuntimeError("legacy staging anchor is not an active v2 release")
            identifiers = legacy_identifiers
            selected_anchor = legacy_anchor
    if len(identifiers) != 1:
        raise RuntimeError(
            f"expected one {target.project}/{anchor} container, found {len(identifiers)}",
        )
    inspect = runner.run(
        ["docker", "inspect", identifiers[0], "--format", "{{json .Config.Labels}}"],
    )
    labels = json.loads(inspect.stdout)
    if labels.get("com.docker.compose.project") != target.project:
        raise RuntimeError("anchor container belongs to another Compose project")
    if labels.get("com.docker.compose.service") != selected_anchor:
        raise RuntimeError("anchor container has the wrong Compose service label")
    if selected_anchor != anchor:
        state = json.loads(
            runner.run(
                ["docker", "inspect", identifiers[0], "--format", "{{json .State}}"],
            ).stdout,
        )
        if (
            state.get("Running") is not True
            or state.get("Health", {}).get("Status") != "healthy"
        ):
            raise RuntimeError("legacy staging anchor is not running and healthy")
    files = [
        item
        for item in labels.get("com.docker.compose.project.config_files", "").split(",")
        if item
    ]
    directory = labels.get("com.docker.compose.project.working_dir", "")
    env_file = labels.get("com.docker.compose.project.environment_file", "")
    env_files = [item for item in env_file.split(",") if item]
    if (
        not files
        or not directory.startswith("/")
        or not env_files
        or any(not item.startswith("/") for item in files)
        or any(not item.startswith("/") for item in env_files)
    ):
        raise RuntimeError("anchor container has incomplete Compose provenance")
    identity = {
        "container_id": identifiers[0],
        "anchor_service": selected_anchor,
        "project": target.project,
        "working_directory": directory,
        "compose_files": files,
        "environment_file": env_file,
        "profiles": target.value["compose"]["profiles"],
    }
    if selected_anchor != anchor:
        paths = [directory, *files, *env_files]
        if runner.run(["test", "-d", directory], check=False).returncode:
            raise RuntimeError("legacy Compose working directory is missing")
        for path in paths:
            if path != directory and runner.run(
                ["test", "-f", path], check=False,
            ).returncode:
                raise RuntimeError(f"legacy Compose identity file is missing: {path}")
            resolved = runner.run(["readlink", "-f", "--", path]).stdout.strip()
            if resolved != path:
                raise RuntimeError(f"legacy Compose identity path is not direct: {path}")
        services = set(
            runner.run(compose_command(identity, ["config", "--services"])).stdout.splitlines(),
        )
        expected = set(target.value["services"].values())
        expected.remove(anchor)
        expected.add(selected_anchor)
        if not expected.issubset(services) or anchor in services:
            raise RuntimeError("legacy Compose service perimeter differs")
    return identity


def compose_command(identity: dict[str, Any], arguments: list[str]) -> list[str]:
    command = [
        "docker",
        "compose",
        "--project-name",
        identity["project"],
        "--project-directory",
        identity["working_directory"],
    ]
    for path in identity["environment_file"].split(","):
        command.extend(("--env-file", path))
    for path in identity["compose_files"]:
        command.extend(("--file", path))
    for profile in identity.get("profiles", []):
        command.extend(("--profile", profile))
    return [*command, *arguments]


def read_active_state(target: Target, runner: Runner) -> dict[str, Any] | None:
    path = f"{target.value['state_directory']}/active.json"
    process = runner.run(["cat", path], check=False)
    if process.returncode:
        return None
    try:
        state = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"active generation state is invalid: {path}") from error
    expected = {
        "schema",
        "target",
        "generation",
        "volumes",
        "network",
        "snapshot",
        "release_manifest",
        "previous",
    }
    if not isinstance(state, dict) or set(state) != expected:
        raise RuntimeError("active generation state fields differ")
    if state["schema"] != "usl-active-generation/v1" or state["target"] != target.name:
        raise RuntimeError("active generation state belongs to another target")
    if not TARGET_NAME.fullmatch(str(state["generation"])):
        raise RuntimeError("active generation name is invalid")
    recorded_roles = set(state["volumes"])
    target_roles = set(target.value["volumes"])
    if recorded_roles - target_roles or any(
        target.value["volumes"][role]["class"] != "transient"
        for role in target_roles - recorded_roles
    ):
        raise RuntimeError("active generation volume perimeter differs")
    if not isinstance(state["network"], str) or not state["network"]:
        raise RuntimeError("active generation network is invalid")
    if not SHA256.fullmatch(str(state["snapshot"])):
        raise RuntimeError("active generation snapshot is invalid")
    if not isinstance(state["release_manifest"], str) or not state["release_manifest"].startswith(
        target.value["state_directory"] + "/generations/",
    ):
        raise RuntimeError("active generation release manifest is invalid")
    return state


def read_active_release(target: Target, runner: Runner, state: dict[str, Any]) -> dict[str, Any]:
    expected = (
        f"{target.value['state_directory']}/generations/"
        f"{state['generation']}/usl-release.json"
    )
    if state["release_manifest"] != expected:
        raise RuntimeError("active release manifest path does not match its generation")
    try:
        payload = json.loads(runner.run(["cat", expected]).stdout)
        return validate_release(payload)
    except (json.JSONDecodeError, ReleaseManifestError) as error:
        raise RuntimeError("active release manifest is invalid") from error


def effective_volumes(target: Target, runner: Runner) -> tuple[dict[str, dict[str, str]], str | None]:
    state = read_active_state(target, runner)
    if state is None:
        return target.value["volumes"], None
    volumes = {
        role: {
            "name": name,
            "class": target.value["volumes"][role]["class"],
            "tier": target.value["volumes"][role]["tier"],
        }
        for role, name in state["volumes"].items()
        if isinstance(name, str) and name
    }
    if set(volumes) != set(state["volumes"]):
        raise RuntimeError("active generation contains an invalid volume name")
    return volumes, state["generation"]


def inspect_runtime(target: Target, runner: Runner) -> dict[str, Any]:
    identity = compose_identity(target, runner)
    process = runner.run(compose_command(identity, ["ps", "--all", "--format", "json"]))
    containers = _json_lines(process.stdout)
    foreign = [
        item.get("Name")
        for item in containers
        if item.get("Project") not in {None, "", target.project}
    ]
    if foreign:
        raise RuntimeError(f"Compose returned foreign containers: {foreign}")
    active_state = read_active_state(target, runner)
    definitions, generation = effective_volumes(target, runner)
    volumes = {}
    for role, definition in definitions.items():
        inspected = runner.run(
            ["docker", "volume", "inspect", definition["name"], "--format", "{{json .Labels}}"],
        )
        labels = json.loads(inspected.stdout)
        legacy_owner = labels.get("com.docker.compose.project") == target.project
        generation_owner = (
            labels.get("com.unstaticlabs.runtime.project") == target.project
            and labels.get("com.unstaticlabs.runtime.target") == target.name
            and labels.get("com.unstaticlabs.runtime.generation") == generation
            and labels.get("com.unstaticlabs.runtime.role") == role
        )
        if not legacy_owner and not generation_owner:
            raise RuntimeError(f"volume {definition['name']} is not owned by {target.project}")
        volumes[role] = {
            "name": definition["name"],
            "class": definition["class"],
            "tier": definition["tier"],
        }
    return {
        "schema": "usl-runtime-status/v1",
        "target": target.name,
        "environment": target.value["environment"],
        "protected": target.protected,
        "compose": identity,
        "containers": containers,
        "volumes": volumes,
        "generation": generation or "adopted",
        "active_state": active_state,
        "paths": target.value["paths"],
    }
