from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from migration.documents_archive.rebind_external_model import RebindError, rebind


class ExternalModelRebindTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.index = Path(self.temporary.name) / "llmindex.db"
        connection = sqlite3.connect(self.index)
        connection.executescript(
            """
            CREATE TABLE documents (document_id INTEGER);
            CREATE TABLE document_chunks (id INTEGER);
            CREATE TABLE document_meta (document_id INTEGER);
            CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO documents VALUES (1), (1), (2);
            INSERT INTO document_chunks VALUES (1), (2), (3);
            INSERT INTO document_meta VALUES (1), (2);
            INSERT INTO index_meta VALUES
                ('embed_model', 'qualified-local-alias'),
                ('dim', '1024'),
                ('schema_version', '2'),
                ('total_inserts', '3');
            """
        )
        connection.commit()
        connection.close()

    def tearDown(self):
        self.temporary.cleanup()

    def apply(self):
        return rebind(
            self.index,
            source_model="qualified-local-alias",
            target_model="shared-runtime-name",
            expected_vector_rows=3,
            expected_documents=2,
            external_digest="a" * 64,
            external_dimension=1024,
        )

    def test_rebind_changes_only_the_model_label_and_is_idempotent(self):
        first = self.apply()
        second = self.apply()
        self.assertEqual(first["action"], "rebound")
        self.assertEqual(second["action"], "already_rebound")
        self.assertFalse(first["vectors_rebuilt"])
        connection = sqlite3.connect(self.index)
        self.assertEqual(
            connection.execute(
                "SELECT value FROM index_meta WHERE key = 'embed_model'",
            ).fetchone()[0],
            "shared-runtime-name",
        )
        self.assertEqual(connection.execute("SELECT count(*) FROM documents").fetchone()[0], 3)
        connection.close()

    def test_rebind_fails_on_count_or_identity_mismatch(self):
        with self.assertRaises(RebindError):
            rebind(
                self.index,
                source_model="wrong-alias",
                target_model="shared-runtime-name",
                expected_vector_rows=4,
                expected_documents=2,
                external_digest="a" * 64,
                external_dimension=1024,
            )
