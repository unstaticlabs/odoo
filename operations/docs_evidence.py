"""The docs evidence a qualification run produces and a release carries.

``usl-docs-evidence/v1`` says, for one qualified commit, which journeys ran,
when, and what they captured. The viewer reads it to stamp each generated
page "Last tested N days ago" and to show the proof. It is deliberately a
separate file from ``usl-qualification-evidence/v1``: that one is digest
bound and time free, an input of the release manifest; this one carries
timestamps and per-journey detail and rides beside it.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from operations.docs_generate import load_records, page_path_for

SCHEMA = "usl-docs-evidence/v1"
FIELDS = {
    "schema", "repository", "qualified_commit", "workflow_run_id", "run_url",
    "tested_at", "chromium", "mode", "journeys", "sha256",
}
JOURNEY_FIELDS = {"status", "page", "tour", "started", "finished", "viewports", "screenshots", "source_test"}
MODES = ("qualification", "recovery")
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")


class DocsEvidenceError(ValueError):
    """The evidence is malformed or does not describe the expected run."""


def _digest(body):
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build(records_root, *, repository, qualified_commit, workflow_run_id, run_url, committed_docs=None):
    """Assemble the evidence from the journey records of one run."""
    records = load_records(records_root)
    journeys = {}
    chromium = ""
    for (journey, viewport), record in sorted(records.items()):
        entry = journeys.setdefault(journey, {
            "status": "success",
            "page": page_path_for(record).as_posix(),
            "tour": record["tour"],
            "source_test": record["source_test"],
            "started": record["started"],
            "finished": record["finished"],
            "viewports": [],
            "screenshots": [],
        })
        entry["viewports"].append(viewport)
        entry["started"] = min(entry["started"], record["started"])
        entry["finished"] = max(entry["finished"], record["finished"])
        chromium = chromium or record.get("chromium", "")
        for step in record["steps"]:
            shot = step.get("screenshot")
            if not shot:
                continue
            committed = None
            if committed_docs is not None and viewport == "desktop":
                path = Path(committed_docs) / page_path_for(record).parent / journey / shot["file"]
                if path.is_file():
                    committed = hashlib.sha256(path.read_bytes()).hexdigest()
            entry["screenshots"].append({
                "step": step["id"],
                "viewport": viewport,
                "file": shot["file"],
                "observed_sha256": shot["sha256"],
                "committed_sha256": committed,
                "match": "exact" if committed == shot["sha256"] else ("tolerance" if committed else "uncommitted"),
            })
    body = {
        "schema": SCHEMA,
        "repository": repository,
        "qualified_commit": qualified_commit,
        "workflow_run_id": workflow_run_id,
        "run_url": run_url,
        "tested_at": _now(),
        "chromium": chromium,
        "mode": "qualification",
        "journeys": journeys,
    }
    return {**body, "sha256": _digest(body)}


def recovery(*, repository, commit):
    """Evidence for a release rebuilt from a recovery tag: it proves nothing."""
    body = {
        "schema": SCHEMA,
        "repository": repository,
        "qualified_commit": commit,
        "workflow_run_id": None,
        "run_url": None,
        "tested_at": _now(),
        "chromium": "",
        "mode": "recovery",
        "journeys": {},
    }
    return {**body, "sha256": _digest(body)}


def validate(payload, *, qualified_commit=None, workflow_run_id=None):
    if not isinstance(payload, dict) or set(payload) != FIELDS:
        raise DocsEvidenceError("docs evidence has the wrong field set")
    if payload["schema"] != SCHEMA:
        raise DocsEvidenceError(f"docs evidence schema must be {SCHEMA}")
    if not isinstance(payload["qualified_commit"], str) or not _SHA_RE.fullmatch(payload["qualified_commit"]):
        raise DocsEvidenceError("qualified_commit must be a full commit sha")
    if payload["mode"] not in MODES:
        raise DocsEvidenceError("mode must be qualification or recovery")
    if payload["mode"] == "qualification":
        if not isinstance(payload["workflow_run_id"], int) or payload["workflow_run_id"] <= 0:
            raise DocsEvidenceError("a qualification carries a positive workflow run id")
        if not isinstance(payload["run_url"], str) or not payload["run_url"].startswith("https://"):
            raise DocsEvidenceError("a qualification names its run URL")
    if not isinstance(payload["journeys"], dict):
        raise DocsEvidenceError("journeys must be a mapping")
    for name, entry in payload["journeys"].items():
        if not isinstance(entry, dict) or set(entry) != JOURNEY_FIELDS:
            raise DocsEvidenceError(f"journey {name} has the wrong field set")
        if entry["status"] != "success":
            raise DocsEvidenceError(f"journey {name} did not succeed; evidence records only green runs")
    body = {key: value for key, value in payload.items() if key != "sha256"}
    if payload["sha256"] != _digest(body):
        raise DocsEvidenceError("docs evidence digest does not match its body")
    if qualified_commit is not None and payload["qualified_commit"] != qualified_commit:
        raise DocsEvidenceError("docs evidence is for a different commit")
    if workflow_run_id is not None and payload["workflow_run_id"] != workflow_run_id:
        raise DocsEvidenceError("docs evidence is from a different workflow run")
    return payload


def load(path):
    return validate(json.loads(Path(path).read_text(encoding="utf-8")))


def dump(payload, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
