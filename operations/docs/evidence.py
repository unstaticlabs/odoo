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

from operations.docs.generate import load_records, page_path_for

SCHEMA = "usl-docs-evidence/v1"
FIELDS = {
    "schema", "repository", "qualified_commit", "workflow_run_id", "run_url",
    "tested_at", "chromium", "mode", "journeys", "sha256",
}
#: ``qualified_tree`` is the git tree the run tested, which is how a
#: production release (a merge commit of the same tree) recognises its
#: evidence; ``release_commit`` is stamped when a release adopts the evidence.
OPTIONAL_FIELDS = {"qualified_tree", "release_commit"}
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


def build(records_root, *, repository, qualified_commit, workflow_run_id, run_url, committed_docs=None, qualified_tree=None):
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
    if qualified_tree:
        body["qualified_tree"] = qualified_tree
    return {**body, "sha256": _digest(body)}


def stamp_release(payload, release_commit):
    """Return ``payload`` adopted by the release at ``release_commit``, re-digested."""
    body = {key: value for key, value in payload.items() if key != "sha256"}
    body["release_commit"] = release_commit
    return {**body, "sha256": _digest(body)}


def validate_release(payload, release_commit):
    """Validate the evidence a release at ``release_commit`` carries.

    A staging release carries its own run's evidence, bound by
    ``qualified_commit``. A production release adopts the evidence of the run
    that qualified the merged pull request's tree, stamped with
    ``release_commit`` by :func:`stamp_release`; that stamp is then the binding.
    """
    validate(payload)
    if payload.get("release_commit", payload["qualified_commit"]) != release_commit:
        raise DocsEvidenceError("docs evidence is for a different commit")
    return payload


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


def validate(payload, *, qualified_commit=None, workflow_run_id=None, qualified_tree=None):
    if not isinstance(payload, dict) or not FIELDS <= set(payload) <= FIELDS | OPTIONAL_FIELDS:
        raise DocsEvidenceError("docs evidence has the wrong field set")
    for key in OPTIONAL_FIELDS & set(payload):
        if not isinstance(payload[key], str) or not _SHA_RE.fullmatch(payload[key]):
            raise DocsEvidenceError(f"{key} must be a full git sha")
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
    if qualified_tree is not None and payload.get("qualified_tree") != qualified_tree:
        raise DocsEvidenceError("docs evidence is for a different tree")
    return payload


def load(path):
    return validate(json.loads(Path(path).read_text(encoding="utf-8")))


def dump(payload, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
