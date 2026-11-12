"""Own the gateway marker only for the backup capture that created it."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from operations.runtime import RuntimeError

# Exclusive creation preserves release-owned and legacy markers, including
# empty files. A competing creator wins without being overwritten.
CLAIM_MARKER = """import os,pathlib,sys
p=pathlib.Path(sys.argv[1]); content=sys.stdin.buffer.read()
try:
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
except FileExistsError:
    print('preserved')
else:
    with os.fdopen(fd,'wb') as stream:
        stream.write(content); stream.flush(); os.fsync(stream.fileno())
    print('owned')
"""

# The existing runtime operation lock serializes backup/release operations.
# Compare exact owner bytes too, so an externally replaced marker survives.
RELEASE_MARKER = """import pathlib,sys
p=pathlib.Path(sys.argv[1]); expected=sys.stdin.buffer.read()
if p.is_symlink():
    print('preserved')
else:
    try:
        actual=p.read_bytes()
    except FileNotFoundError:
        print('absent')
    else:
        if actual==expected:
            p.unlink(); print('removed')
        else:
            print('preserved')
"""


def acquire(target, runner, identity: dict, run_id: str) -> tuple[str, str] | None:
    """Claim maintenance on an existing native gateway, without adopting one."""
    if target.name not in {"production", "staging"}:
        return None
    containers = runner.run([
        "docker", "ps", "--filter", f"label=com.docker.compose.project={identity['project']}",
        "--filter", "label=com.docker.compose.service=gateway", "--format", "{{.ID}}",
    ]).stdout.split()
    if not containers:  # Local/bootstrap/legacy runtime without a gateway.
        return None
    if len(containers) != 1:
        raise RuntimeError("backup gateway identity is ambiguous")
    marker = Path(target.value["state_directory"]).parents[1] / "gateway" / target.name / "maintenance"
    try:
        mounts = json.loads(runner.run([
            "docker", "inspect", containers[0], "--format", "{{json .Mounts}}",
        ]).stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("backup gateway mount evidence is invalid") from error
    matching = [
        item for item in mounts
        if isinstance(item, dict) and item.get("Destination") == "/run/usl-gateway"
    ] if isinstance(mounts, list) else []
    if len(matching) != 1 or matching[0].get("Source") != str(marker.parent):
        raise RuntimeError("backup gateway maintenance mount differs")
    content = json.dumps({
        "schema": "usl-backup-maintenance/v1", "target": target.name, "run_id": run_id,
        "owner": uuid.uuid4().hex, "enabled_at": datetime.now(UTC).isoformat(),
    }, sort_keys=True) + "\n"
    result = runner.run(["python3", "-c", CLAIM_MARKER, str(marker)], input_text=content)
    if result.stdout.strip() == "preserved":
        return None
    if result.stdout.strip() != "owned":
        raise RuntimeError("backup maintenance claim returned invalid evidence")
    return str(marker), content


def release(runner, claim: tuple[str, str] | None) -> None:
    """Reopen only this capture's marker after a successful healthy resume."""
    if claim is not None:
        path, content = claim
        result = runner.run(["python3", "-c", RELEASE_MARKER, path], input_text=content)
        if result.stdout.strip() not in {"removed", "preserved", "absent"}:
            raise RuntimeError("backup maintenance release returned invalid evidence")
