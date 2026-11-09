"""Find the docs evidence a release can carry.

The evidence is produced by the qualification *database* job, which runs on a
push to ``19-usl-staging`` and on a pull request into ``19-usl``, never on
the push to ``19-usl`` itself. So a staging release finds the evidence under
its own run, while a production release (the promotion or an ``urgent/**``
merge) has to find the run that qualified the pull request it merged. That
run tested a merge ref whose commit differs from the pushed merge commit but
whose *tree* is the same, which is also how the qualification evidence is
bound to production; the tree is what we verify.

When nothing matches, the release carries no docs evidence and the guide
says "Unverified build" rather than failing the release: a missing stamp on
the guide is never worth a stalled production release.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from operations.docs import evidence

WORKFLOW_NAME = "USL qualification"


class ReleaseEvidenceError(RuntimeError):
    """The GitHub lookups failed in a way that is not "nothing found"."""


def run_gh(arguments, *, cwd=None):
    """Run ``gh`` and return its stdout; the single seam the tests replace."""
    completed = subprocess.run(["gh", *arguments], cwd=cwd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise ReleaseEvidenceError(f"gh {' '.join(arguments[:3])} failed: {completed.stderr.strip()}")
    return completed.stdout


def _download(run_id, repository, gh):
    """Return the evidence payload of ``run_id``'s artifact, or ``None`` when it has none."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            gh(["run", "download", str(run_id), "--repo", repository, "--name", f"docs-evidence-{run_id}", "--dir", tmp])
        except ReleaseEvidenceError:
            return None
        path = Path(tmp) / "docs-evidence.json"
        if not path.is_file():
            return None
        return _json(path.read_text(encoding="utf-8"))


def _json(text):
    """Parse GitHub output; anything unparseable counts as nothing found, never as a failed release."""
    try:
        return json.loads(text or "null")
    except ValueError:
        return None


def _pull_request_runs(repository, commit, gh):
    """The qualification runs of the pull requests merged as ``commit``, newest first."""
    pulls = _json(gh(["api", f"repos/{repository}/commits/{commit}/pulls"])) or []
    run_ids = []
    for pull in pulls:
        head = (pull.get("head") or {}).get("sha")
        if not head:
            continue
        runs = _json(gh([
            "api", f"repos/{repository}/actions/runs?event=pull_request&head_sha={head}&status=success&per_page=50",
        ])) or {}
        for run in runs.get("workflow_runs", []):
            if run.get("name") == WORKFLOW_NAME:
                run_ids.append(int(run["id"]))
    return sorted(set(run_ids), reverse=True)


def resolve(*, repository, commit, tree, workflow_run_id, gh=run_gh):
    """Return the evidence for ``commit`` stamped with it, or ``None``.

    First the release's own qualification run (a staging push), verified by
    commit. Then the runs that qualified the pull requests merged as this
    commit (a production push), verified by tree.
    """
    own = _download(workflow_run_id, repository, gh)
    if isinstance(own, dict):
        for binding in ({"qualified_commit": commit}, {"qualified_tree": tree}):
            try:
                payload = evidence.validate(own, workflow_run_id=workflow_run_id, **binding)
            except evidence.DocsEvidenceError:
                continue
            return evidence.stamp_release(payload, commit)
    for run_id in _pull_request_runs(repository, commit, gh):
        candidate = _download(run_id, repository, gh)
        if not isinstance(candidate, dict):
            continue
        try:
            payload = evidence.validate(candidate, qualified_tree=tree, workflow_run_id=run_id)
        except evidence.DocsEvidenceError:
            continue
        return evidence.stamp_release(payload, commit)
    return None
