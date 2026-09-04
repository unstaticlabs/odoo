"""Emit Paperless and LLM-index release counters through ``manage.py shell``."""

# ruff: noqa: EM101, T201 - Paperless shell supplies the Django runtime.

import hashlib
import json
import sqlite3
from pathlib import Path

import sqlite_vec
from django.conf import settings
from documents.models import Document, PaperlessTask
from paperless_personal_ai.models import PersonalAIProfile


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


active_tasks = PaperlessTask.objects.exclude(
    status__in=("success", "failure", "revoked"),
).count()
failed_tasks = PaperlessTask.objects.filter(status="failure").count()
documents = Document.global_objects.all()
document_count = documents.count()
document_ids = list(documents.order_by("id").values_list("id", flat=True))
searchable_documents = documents.exclude(content="").count()
live_documents = Document.objects.count()
trash_documents = Document.deleted_objects.count()
personal_profiles = PersonalAIProfile.objects.count()

index_dir = Path("/usr/src/paperless/data/llm_index")
index_path = index_dir / "llmindex.db"
if not index_path.is_file():
    raise RuntimeError("Paperless LLM index is missing")
connection = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
try:
    connection.enable_load_extension(True)
    sqlite_vec.load(connection)
    connection.enable_load_extension(False)
    vector_rows = int(connection.execute("SELECT count(*) FROM documents").fetchone()[0])
    vector_documents = int(
        connection.execute(
            "SELECT count(DISTINCT document_id) FROM documents",
        ).fetchone()[0],
    )
    chunk_rows = int(
        connection.execute("SELECT count(*) FROM document_chunks").fetchone()[0],
    )
    indexed_documents = int(
        connection.execute("SELECT count(*) FROM document_meta").fetchone()[0],
    )
    index_metadata = {
        str(key): str(value)
        for key, value in connection.execute(
            "SELECT key, value FROM index_meta ORDER BY key",
        ).fetchall()
    }
finally:
    connection.close()

paperless_status = "passed" if active_tasks == 0 else "partial"
vector_status = (
    "passed"
    if vector_rows > 0
    and vector_rows == chunk_rows
    and indexed_documents == vector_documents
    and vector_documents == live_documents
    else "partial"
)
paperless = {
    "schema": "usl-paperless-release-inventory-v1",
    "status": paperless_status,
    "api_version": "v10",
    "document_count": document_count,
    "document_id_min": min(document_ids) if document_ids else None,
    "document_id_max": max(document_ids) if document_ids else None,
    "searchable_documents": searchable_documents,
    "live_documents": live_documents,
    "trash_documents": trash_documents,
    "active_tasks": active_tasks,
    "historical_failed_tasks": failed_tasks,
    "personal_profiles": personal_profiles,
}
vector = {
    "schema": "usl-paperless-vector-index-v1",
    "status": vector_status,
    "schema_version": index_metadata.get("schema_version", "2"),
    "dimension": 1024,
    "chunk_size": 512,
    "embedding_batch_size": settings.LLM_EMBEDDING_BATCH_SIZE,
    "overlap": 200,
    "vector_rows": vector_rows,
    "chunk_rows": chunk_rows,
    "indexed_documents": indexed_documents,
    "vector_documents": vector_documents,
    # Paperless deliberately excludes documents in Trash from semantic search.
    # The global count remains release evidence, while index parity is against
    # the live manager used by document_llmindex.
    "expected_indexed_documents": live_documents,
    "index_sha256": sha256_file(index_path),
    "index_size": index_path.stat().st_size,
    "wal_present": (index_path.with_name(index_path.name + "-wal")).exists(),
    "shm_present": (index_path.with_name(index_path.name + "-shm")).exists(),
    "metadata": index_metadata,
}
print("USL_PAPERLESS_RELEASE_INVENTORY=" + json.dumps(paperless, sort_keys=True))
print("USL_PAPERLESS_VECTOR_INVENTORY=" + json.dumps(vector, sort_keys=True))
