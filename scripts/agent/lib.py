from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = Path(os.environ.get("USL_AGENT_POLICY_PATH", ROOT / "agent" / "policy.json"))
HANDOFF_SCHEMA_PATH = ROOT / "agent" / "contracts" / "v1" / "feature-handoff.schema.json"
QA_SCHEMA_PATH = ROOT / "agent" / "contracts" / "v1" / "qa-environment.schema.json"


class AgentError(RuntimeError):
    pass


def run(
    *command: str,
    cwd: Path = ROOT,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> str:
    process = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and process.returncode:
        detail = process.stderr.strip() or process.stdout.strip()
        raise AgentError(f"{' '.join(command)} failed: {detail}")
    return process.stdout.strip()


def git(*arguments: str, check: bool = True) -> str:
    return run("git", *arguments, check=check)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise AgentError(f"Missing required file: {path}") from error
    except json.JSONDecodeError as error:
        raise AgentError(f"Invalid JSON in {path}: {error}") from error


def write_json(path: Path, payload: Any, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    if mode is not None:
        temporary.chmod(mode)
    temporary.replace(path)


def policy() -> dict[str, Any]:
    value = load_json(POLICY_PATH)
    if value.get("schema") != "usl-agent-policy/v1":
        raise AgentError(f"Unsupported agent policy schema in {POLICY_PATH}")
    return value


def branch_name() -> str:
    return git("branch", "--show-current") or "<detached>"


def head_sha() -> str:
    return git("rev-parse", "HEAD")


def resolve_ref(reference: str) -> str:
    return git("rev-parse", "--verify", f"{reference}^{{commit}}")


def merge_base(reference: str) -> str:
    return git("merge-base", "HEAD", reference)


def dirty_entries() -> list[str]:
    return [line for line in git("status", "--short").splitlines() if line]


def root_digest() -> str:
    return hashlib.sha256(str(ROOT.resolve()).encode()).hexdigest()[:8]


def qa_project() -> str:
    return f"{policy()['qa']['compose_project_prefix']}{root_digest()}"


def qa_ports() -> dict[str, int]:
    process = subprocess.run(
        ["cksum"],
        input=str(ROOT.resolve()),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode:
        raise AgentError(f"cksum failed: {process.stderr.strip()}")
    output = process.stdout.strip()
    seed = int(output.split()[0])
    base = 24000 + seed % 12000
    return {
        "odoo": base,
        "gevent": base + 1,
        "pocket_id": base + 2,
        "paperless": base + 3,
    }


def odoo_version() -> str:
    release = (ROOT / "odoo" / "release.py").read_text(encoding="utf-8")
    match = re.search(r"^version_info\s*=\s*\('([^']+)',\s*(\d+)", release, re.MULTILINE)
    if not match:
        raise AgentError("Unable to read Odoo version from odoo/release.py")
    return f"{match.group(1)}.{match.group(2)}"


def changed_paths(base: str) -> list[str]:
    base_sha = resolve_ref(base)
    common = git("merge-base", "HEAD", base_sha)
    paths = set(git("diff", "--name-only", common).splitlines())
    paths.update(git("ls-files", "--others", "--exclude-standard").splitlines())
    return sorted(path for path in paths if path)


def changed_addons(paths: list[str]) -> list[str]:
    modules: set[str] = set()
    for path in paths:
        parts = Path(path).parts
        if len(parts) >= 2 and parts[0] in {"custom-addons", "addons", "oca-addons"}:
            manifest = ROOT / parts[0] / parts[1] / "__manifest__.py"
            if manifest.is_file():
                modules.add(parts[1])
    return sorted(modules)


def qa_profile(project: str) -> str | None:
    state = ROOT / "artifacts" / "migration" / "private" / "qa-state" / f"{project}.json"
    if state.is_file():
        value = load_json(state)
        profile = value.get("profile")
        if isinstance(profile, str):
            return profile
    reports = sorted(
        (ROOT / "artifacts" / "migration" / "private" / "qa-runs").glob(f"{project}-*.json")
    )
    if reports:
        value = load_json(reports[-1])
        profile = value.get("profile")
        if isinstance(profile, str):
            return profile
    return None


def compose_project_status(project: str) -> dict[str, Any]:
    try:
        output = run(
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            '{{.Names}}|{{.State}}|{{.Status}}|{{.Label "com.docker.compose.service"}}|{{.Label "com.docker.compose.project.working_dir"}}',
        )
    except (AgentError, FileNotFoundError):
        return {"project": project, "health": "unavailable", "ownership": "unavailable", "owners": []}
    containers: list[dict[str, str]] = []
    for line in output.splitlines():
        parts = line.split("|", 4)
        if len(parts) != 5:
            continue
        containers.append(
            {
                "name": parts[0],
                "state": parts[1],
                "status": parts[2],
                "service": parts[3],
                "owner": parts[4],
            }
        )
    if not containers:
        return {"project": project, "health": "absent", "ownership": "unused", "owners": []}
    running = sum(item["state"] == "running" for item in containers)
    if running == len(containers):
        health = "ready"
    elif running:
        health = "partial"
    else:
        health = "stopped"
    owners = sorted({item["owner"] for item in containers if item["owner"]})
    if len(owners) > 1:
        ownership = "mixed"
    elif owners == [str(ROOT.resolve())]:
        ownership = "owned"
    else:
        ownership = "foreign"
    return {"project": project, "health": health, "ownership": ownership, "owners": owners}


def qa_status_payload() -> dict[str, Any]:
    project = qa_project()
    ports = qa_ports()
    compose = compose_project_status(project)
    branch = branch_name()
    return {
        "schema": "usl-qa-environment/v1",
        "identifier": project.removeprefix("usl-odoo-qa-"),
        "worktree": str(ROOT.resolve()),
        "branch": branch,
        "head_sha": head_sha(),
        "compose_project": project,
        "profile": qa_profile(project),
        "database": "odoo_dev",
        "health": compose["health"],
        "ownership": {"state": compose["ownership"], "owners": compose["owners"]},
        "urls": {
            "local_odoo": f"http://odoo.localhost:{ports['odoo']}/web/login?db=odoo_dev",
            "local_documents": f"http://paperless.localhost:{ports['paperless']}",
            "remote_https": None,
        },
        "authentication": {
            "provider": "Pocket ID QA tenant",
            "login_command": f"POCKET_ID_ENV_FILE=.pocket-id-qa-{root_digest()}.env make login-link USER=<username>",
            "secrets_in_output": False,
        },
        "isolation": {
            "database": True,
            "filestore": True,
            "compose_project": True,
            "shared_seed_read_only": True,
        },
        "cleanup": {
            "owner": "Lead Developer after merge and CI",
            "command": f"scripts/agent/qa-down --confirm {project}",
        },
    }


def schema_errors(instance: Any, schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    def resolve(candidate: dict[str, Any]) -> dict[str, Any]:
        reference = candidate.get("$ref")
        if not reference:
            return candidate
        if not reference.startswith("#/"):
            raise AgentError(f"Unsupported schema reference: {reference}")
        target: Any = schema
        for part in reference[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return target

    def matches_type(value: Any, expected: str) -> bool:
        return {
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "boolean": isinstance(value, bool),
            "null": value is None,
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        }.get(expected, False)

    def visit(value: Any, candidate: dict[str, Any], location: str) -> None:
        candidate = resolve(candidate)
        expected = candidate.get("type")
        if expected is not None:
            types = expected if isinstance(expected, list) else [expected]
            if not any(matches_type(value, item) for item in types):
                errors.append(f"{location}: expected {' or '.join(types)}")
                return
        if "const" in candidate and value != candidate["const"]:
            errors.append(f"{location}: expected constant {candidate['const']!r}")
        if "enum" in candidate and value not in candidate["enum"]:
            errors.append(f"{location}: expected one of {candidate['enum']!r}")
        if isinstance(value, str):
            if len(value) < candidate.get("minLength", 0):
                errors.append(f"{location}: string is too short")
            pattern = candidate.get("pattern")
            if pattern and not re.search(pattern, value):
                errors.append(f"{location}: does not match {pattern}")
        if isinstance(value, list):
            if len(value) < candidate.get("minItems", 0):
                errors.append(f"{location}: expected at least {candidate['minItems']} item(s)")
            if candidate.get("uniqueItems"):
                serialized = [json.dumps(item, sort_keys=True) for item in value]
                if len(serialized) != len(set(serialized)):
                    errors.append(f"{location}: items must be unique")
            item_schema = candidate.get("items")
            if item_schema:
                for index, item in enumerate(value):
                    visit(item, item_schema, f"{location}[{index}]")
        if isinstance(value, dict):
            required = candidate.get("required", [])
            for key in required:
                if key not in value:
                    errors.append(f"{location}: missing required property {key!r}")
            properties = candidate.get("properties", {})
            if candidate.get("additionalProperties") is False:
                for key in value:
                    if key not in properties:
                        errors.append(f"{location}: unexpected property {key!r}")
            for key, child in properties.items():
                if key in value:
                    visit(value[key], child, f"{location}.{key}")

    visit(instance, schema, "$")
    return errors


def validate_schema(instance: Any, schema_path: Path) -> list[str]:
    return schema_errors(instance, load_json(schema_path))
