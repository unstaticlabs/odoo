"""Build and publish a complete Paperless index under its existing writer lock."""

import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path


class IndexModelMismatch(ValueError):
    """The prepared query or document belongs to another vector space."""


def needs_model_rebuild(store, model_name):
    # Legacy tables can predate the stored model name. An unlabelled nonempty
    # index cannot establish that its vectors belong to the requested model.
    return store.table_exists() and store.stored_model_name() != model_name


def require_index_model(store, model_name):
    if needs_model_rebuild(store, model_name):
        raise IndexModelMismatch(
            "The semantic index uses another embedding model. "
            "Run document_llmindex update to build the configured model's index."
        )


def _checkpoint_candidate(candidate):
    # sqlite3_close can succeed with committed frames left in the WAL after
    # an I/O error. Only a completed TRUNCATE checkpoint is safe to publish.
    result = candidate.client.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if tuple(result) != (0, 0, 0):
        raise RuntimeError("The replacement semantic index could not finish its SQLite checkpoint.")


def _verify_main_file(database):
    wal = Path(f"{database}-wal")
    if wal.exists() and wal.stat().st_size:
        raise RuntimeError("The replacement semantic index still depends on its SQLite journal.")
    # immutable=1 opens the main file in isolation, without creating or
    # consulting its journal. Validate the file that will actually be renamed.
    uri = database.resolve().as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as standalone:
        if standalone.execute("PRAGMA quick_check(1)").fetchall() != [("ok",)]:
            raise RuntimeError("The replacement semantic index failed its SQLite integrity check.")


@contextmanager
def replacement_index(live, model_name, directory, exclude_readers):
    from paperless_ai.vector_store import DB_FILENAME, PaperlessSqliteVecVectorStore

    # All callers hold settings.LLM_INDEX_LOCK. A killed build can leave only
    # these fixed candidate files; a retry starts with an empty database.
    candidate_dir = Path(directory) / "usl-index-build"
    if candidate_dir.is_symlink():
        raise RuntimeError("The temporary semantic index directory must be a real directory.")
    candidate_dir.mkdir(mode=0o700, exist_ok=True)
    database = candidate_dir / DB_FILENAME
    files = [database, Path(f"{database}-wal"), Path(f"{database}-shm")]
    try:
        for path in files:
            path.unlink(missing_ok=True)
        with PaperlessSqliteVecVectorStore(str(candidate_dir), model_name) as candidate:
            yield candidate
            _checkpoint_candidate(candidate)
        _verify_main_file(database)
        # The native compaction swap closes the old connection, replaces the
        # database in one rename and reopens it. Both paths share a filesystem.
        with exclude_readers():
            live._swap_in_compact(str(database), str(Path(directory) / DB_FILENAME))
    finally:
        for path in files:
            path.unlink(missing_ok=True)
        candidate_dir.rmdir()
