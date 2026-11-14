"""Validate historical byte preservation without certifying modern recovery.

Older cohort manifests can predate keys required by today's runtime. An exact
Restic archive read-back preserves their historical bytes; it does not make
those cohorts bootable, qualified modern recovery points.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

SCHEMA = "usl-historical-capture-archive/v1"
TAG = "usl-historical-capture-archive"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")


class HistoricalCaptureError(ValueError):
    pass


def _identity(value):
    return (
        isinstance(value, dict) and set(value) == {"files", "bytes", "sha256"}
        and all(isinstance(value[k], int) and not isinstance(value[k], bool)
                and value[k] >= 0 for k in ("files", "bytes"))
        and SHA256.fullmatch(str(value["sha256"])) is not None
    )


def capture_identity(manifest_raw: str, state: object, *, target: str, capture: str) -> dict:
    """Identify a finished historical payload, keeping its original manifest."""
    try:
        manifest = json.loads(manifest_raw)
        release = manifest["release"]
        if (
            manifest["schema"] not in {"usl-recovery-cohort/v1", "usl-recovery-cohort/v2"}
            or manifest["target"] != target or manifest["run_id"] != capture
            or not COMMIT.fullmatch(str(release["commit"]))
            or not SHA256.fullmatch(str(release["manifest_sha256"]))
            or release["path"] != "durable/release.json"
            or not _identity(manifest["durable"]) or not _identity(manifest["cache"])
        ):
            raise HistoricalCaptureError("historical capture identity differs")
        created = datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            raise HistoricalCaptureError("historical capture timestamp has no timezone")
        if (
            not isinstance(state, dict) or state.get("schema") != "usl-recovery-cohort-state/v1"
            or state.get("target") != target or state.get("run_id") != capture
            or state.get("status") != "qualified"
            or any(not SHA256.fullmatch(str(state.get(k, "")))
                   for k in ("durable_snapshot_id", "cache_snapshot_id"))
        ):
            raise HistoricalCaptureError("historical capture is unfinished or belongs to another target")
        return {
            "manifest_sha256": hashlib.sha256(manifest_raw.encode()).hexdigest(),
            "release_commit": release["commit"],
            "durable_snapshot_id": state["durable_snapshot_id"],
            "cache_snapshot_id": state["cache_snapshot_id"],
        }
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as error:
        raise HistoricalCaptureError("historical capture metadata is invalid") from error


def validate_archive(value: object, *, target: str, capture: str, manifest_sha256: str,
                     repository: str, actual_tree: dict, actual_metadata_sha256: str) -> dict:
    """Bind an operator's durable read-back receipt to the exact local payload."""
    fields = {"schema", "target", "capture", "repository", "snapshot_id", "tag",
              "manifest_sha256", "source_tree", "restored_tree", "status", "verified_at", "sha256",
              "source_metadata_sha256", "restored_metadata_sha256", "archive_path"}
    if not isinstance(value, dict) or set(value) != fields:
        raise HistoricalCaptureError("historical archive receipt fields differ")
    unsigned = {key: item for key, item in value.items() if key != "sha256"}
    digest = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if (
        value["sha256"] != digest or value["schema"] != SCHEMA or value["tag"] != TAG
        or value["target"] != target or value["capture"] != capture
        or value["repository"] != repository or value["manifest_sha256"] != manifest_sha256
        or value["status"] != "verified" or not SHA256.fullmatch(str(value["snapshot_id"]))
        or not _identity(value["source_tree"]) or value["source_tree"]["files"] < 3
        or value["source_tree"] != value["restored_tree"] or value["source_tree"] != actual_tree
        or not SHA256.fullmatch(str(value["source_metadata_sha256"]))
        or value["source_metadata_sha256"] != value["restored_metadata_sha256"]
        or value["source_metadata_sha256"] != actual_metadata_sha256
        or not isinstance(value["archive_path"], str) or not value["archive_path"].startswith("/")
        or value["archive_path"] == "/" or ".." in value["archive_path"].split("/")
    ):
        raise HistoricalCaptureError("historical archive does not preserve this exact payload")
    try:
        verified = datetime.fromisoformat(value["verified_at"].replace("Z", "+00:00"))
        if verified.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as error:
        raise HistoricalCaptureError("historical archive verification timestamp is invalid") from error
    return value


def verify_remote_archive(value):
    """Read current immutable snapshot metadata immediately before retirement."""
    from operations import cohort
    environment = cohort.restic_environment("RESTIC_REPOSITORY", "RESTIC_PASSWORD")
    if environment["RESTIC_REPOSITORY"] != value["repository"]:
        raise HistoricalCaptureError("historical archive remote destination differs")
    result = cohort.retry_restic(["restic", "snapshots", "--json", value["snapshot_id"]], environment)
    try:
        snapshots = json.loads(result.stdout)
        if (not isinstance(snapshots, list) or len(snapshots) != 1
                or snapshots[0]["id"] != value["snapshot_id"]
                or TAG not in snapshots[0].get("tags", [])
                or snapshots[0].get("paths") != [value["archive_path"]]):
            raise HistoricalCaptureError("historical archive remote anchor is absent or differs")
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise HistoricalCaptureError("historical archive remote metadata is invalid") from error
    return {"status": "present"}


def metadata_identity(root):
    """Fingerprint restorable modes, ownership, mtimes and extended attributes."""
    import os
    from pathlib import Path
    import stat
    root = Path(root)
    rows = []
    for path in [root, *sorted(root.rglob("*"))]:
        item = path.lstat()
        if stat.S_ISLNK(item.st_mode) or not (stat.S_ISDIR(item.st_mode) or stat.S_ISREG(item.st_mode)):
            raise HistoricalCaptureError("historical archive contains a linked or special path")
        attributes = {name: hashlib.sha256(os.getxattr(path, name)).hexdigest()
                      for name in sorted(os.listxattr(path))}
        rows.append({"path": path.relative_to(root).as_posix(), "mode": stat.S_IMODE(item.st_mode),
                     "uid": item.st_uid, "gid": item.st_gid, "mtime_ns": item.st_mtime_ns,
                     "kind": "directory" if stat.S_ISDIR(item.st_mode) else "file", "xattrs": attributes})
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def archive_capture(source, scratch, receipt_path, *, target, capture, repository):
    """Use ordinary Restic to preserve and independently read back one payload.

    The caller owns scratch lifecycle and the existing target-operation lock.
    Nothing in the source capture is rewritten or removed by this operation.
    """
    from datetime import UTC
    from pathlib import Path
    import shutil
    from operations import cohort
    from operations.runtime import CommandRunner
    from operations.stack import _write_recovery_proof_evidence
    source, scratch, receipt_path = map(Path, (source, scratch, receipt_path))
    if not source.is_absolute() or source.resolve() != source or not source.is_dir():
        raise HistoricalCaptureError("historical archive source must be a direct absolute directory")
    if (not scratch.is_absolute() or scratch.resolve() != scratch or source == scratch
            or source in scratch.parents or scratch in source.parents):
        raise HistoricalCaptureError("historical archive scratch overlaps the source")
    if not receipt_path.is_absolute() or receipt_path.resolve() != receipt_path:
        raise HistoricalCaptureError("historical archive receipt must be a direct absolute path")
    if source == receipt_path or source in receipt_path.parents or scratch == receipt_path or scratch in receipt_path.parents:
        raise HistoricalCaptureError("historical archive receipt must remain outside source and scratch")
    restore_root = scratch / "historical-readback"
    if restore_root.exists():
        raise HistoricalCaptureError("historical read-back scratch already exists")
    manifest_raw = (source / "manifest.json").read_text()
    identity = capture_identity(manifest_raw, json.loads((source / "state.json").read_text()),
                                target=target, capture=capture)
    tree, metadata = cohort.tree_identity(source), metadata_identity(source)
    environment = cohort.restic_environment("RESTIC_REPOSITORY", "RESTIC_PASSWORD")
    if environment["RESTIC_REPOSITORY"] != repository:
        raise HistoricalCaptureError("historical archive repository differs from declared destination")
    if receipt_path.exists():
        existing = json.loads(receipt_path.read_text())
        validate_archive(existing, target=target, capture=capture,
                         manifest_sha256=identity["manifest_sha256"], repository=repository,
                         actual_tree=tree, actual_metadata_sha256=metadata)
        snapshot = existing["snapshot_id"]
    else:
        snapshot = cohort.restic_backup([source], environment,
                                       [TAG, "cio-2374", f"target-{target}", f"capture-{capture}"])
    snapshot = cohort.restore_snapshot(snapshot, restore_root, environment)
    restored = restore_root / source.relative_to("/")
    restored_tree, restored_metadata = cohort.tree_identity(restored), metadata_identity(restored)
    if cohort.tree_identity(source) != tree or metadata_identity(source) != metadata:
        raise HistoricalCaptureError("historical source changed during archive")
    value = dict(schema=SCHEMA, target=target, capture=capture, repository=repository,
                 snapshot_id=snapshot, tag=TAG, archive_path=str(source),
                 manifest_sha256=identity["manifest_sha256"],
                 source_tree=tree, restored_tree=restored_tree, source_metadata_sha256=metadata,
                 restored_metadata_sha256=restored_metadata, status="verified",
                 verified_at=datetime.now(UTC).isoformat())
    from operations.stack import _digested_document
    validate_archive(_digested_document(value), target=target, capture=capture,
                     manifest_sha256=identity["manifest_sha256"], repository=repository,
                     actual_tree=tree, actual_metadata_sha256=metadata)
    receipt = _write_recovery_proof_evidence(None, CommandRunner(), str(receipt_path), value)
    shutil.rmtree(restore_root)
    return receipt


def main():
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--target", required=True, choices=("production", "staging"))
    parser.add_argument("--capture", required=True)
    parser.add_argument("--repository", required=True)
    args = parser.parse_args()
    archive_capture(args.source, args.scratch, args.receipt, target=args.target,
                    capture=args.capture, repository=args.repository)
    print(json.dumps({"status": "verified", "receipt": str(args.receipt)}))


if __name__ == "__main__":
    main()
