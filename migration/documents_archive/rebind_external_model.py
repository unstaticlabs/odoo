#!/usr/bin/env python3
"""Rebind a restored vector index to an equivalent external model name."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import urllib.request
from pathlib import Path


class RebindError(ValueError):
    """Raised before an unsafe or ambiguous vector-index change."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_private_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def verify_external_model(endpoint: str, model: str, digest: str, dimension: int) -> None:
    endpoint = endpoint.rstrip("/")
    with urllib.request.urlopen(endpoint + "/api/tags", timeout=10) as response:
        models = json.load(response).get("models", [])
    matches = [item for item in models if item.get("name") == model]
    if len(matches) != 1 or matches[0].get("digest") != digest:
        raise RebindError("external model name or digest differs from the qualified release")
    request = urllib.request.Request(
        endpoint + "/api/embed",
        data=json.dumps({"model": model, "input": ["USL model identity rebind"]}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        embeddings = json.load(response).get("embeddings", [])
    if len(embeddings) != 1 or len(embeddings[0]) != dimension:
        raise RebindError("external model embedding dimension differs from the qualified release")


def _load_vector_extension(connection: sqlite3.Connection) -> None:
    try:
        import sqlite_vec
    except ImportError:
        return
    connection.enable_load_extension(True)
    sqlite_vec.load(connection)
    connection.enable_load_extension(False)


def _inventory(connection: sqlite3.Connection) -> dict:
    metadata = dict(connection.execute("SELECT key, value FROM index_meta").fetchall())
    return {
        "vector_rows": int(connection.execute("SELECT count(*) FROM documents").fetchone()[0]),
        "chunk_rows": int(
            connection.execute("SELECT count(*) FROM document_chunks").fetchone()[0]
        ),
        "indexed_documents": int(
            connection.execute("SELECT count(*) FROM document_meta").fetchone()[0]
        ),
        "vector_documents": int(
            connection.execute("SELECT count(DISTINCT document_id) FROM documents").fetchone()[0]
        ),
        "metadata": {str(key): str(value) for key, value in metadata.items()},
    }


def rebind(
    index: Path,
    *,
    source_model: str,
    target_model: str,
    expected_vector_rows: int,
    expected_documents: int,
    external_digest: str,
    external_dimension: int,
) -> dict:
    if not index.is_file():
        raise RebindError("vector index is missing")
    for suffix in ("-wal", "-shm"):
        if index.with_name(index.name + suffix).exists():
            raise RebindError("vector index has active SQLite sidecar state")
    before_sha256 = sha256_file(index)
    connection = sqlite3.connect(index)
    try:
        _load_vector_extension(connection)
        before = _inventory(connection)
        if (
            before["vector_rows"] != expected_vector_rows
            or before["chunk_rows"] != expected_vector_rows
            or before["indexed_documents"] != expected_documents
            or before["vector_documents"] != expected_documents
        ):
            raise RebindError("vector index counts differ from the accepted cohort")
        current_model = before["metadata"].get("embed_model")
        if current_model == source_model:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE index_meta SET value = ? WHERE key = 'embed_model' AND value = ?",
                (target_model, source_model),
            )
            if connection.total_changes != 1:
                raise RebindError("model identity update did not affect exactly one row")
            connection.commit()
            action = "rebound"
        elif current_model == target_model:
            action = "already_rebound"
        else:
            raise RebindError("stored model identity is neither the accepted nor target identity")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        after = _inventory(connection)
    finally:
        connection.close()
    if before | {"metadata": after["metadata"]} != after:
        raise RebindError("model rebind changed vector index content or counts")
    if after["metadata"].get("embed_model") != target_model:
        raise RebindError("target model identity was not persisted")
    return {
        "schema": "usl-external-model-rebind-v1",
        "status": "passed",
        "action": action,
        "source_model": source_model,
        "target_model": target_model,
        "external_model_digest": external_digest,
        "external_model_dimension": external_dimension,
        "before_sha256": before_sha256,
        "after_sha256": sha256_file(index),
        "vector_rows": after["vector_rows"],
        "indexed_documents": after["indexed_documents"],
        "vectors_rebuilt": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--source-model", required=True)
    parser.add_argument("--target-model", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--dimension", type=int, required=True)
    parser.add_argument("--expected-vector-rows", type=int, required=True)
    parser.add_argument("--expected-documents", type=int, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    try:
        verify_external_model(args.endpoint, args.target_model, args.digest, args.dimension)
        evidence = rebind(
            args.index,
            source_model=args.source_model,
            target_model=args.target_model,
            expected_vector_rows=args.expected_vector_rows,
            expected_documents=args.expected_documents,
            external_digest=args.digest,
            external_dimension=args.dimension,
        )
        write_private_json(args.evidence, evidence)
    except (OSError, sqlite3.Error, RebindError) as error:
        raise SystemExit(f"External model rebind rejected: {error}") from error
    print(json.dumps({"action": evidence["action"], "status": evidence["status"]}))


if __name__ == "__main__":
    main()
