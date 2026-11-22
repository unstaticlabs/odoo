import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase
from documents.models import Document
from filelock import Timeout
from llama_index.core.schema import NodeRelationship, RelatedNodeInfo, TextNode
from paperless_ai import indexing, semantic_api


class TestIndexGeneration(TestCase):
    def setUp(self):
        super().setUp()
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(self.settings(
            LLM_INDEX_DIR=self.directory,
            LLM_INDEX_LOCK=self.directory / 'index.lock',
            LLM_INDEX_RWLOCK=self.directory / 'index.rwlock',
            LLM_INDEX_COMPACTION_LOCK_TIMEOUT=0.1,
        ))
        self.model_name = self.enterContext(patch.object(indexing, 'get_configured_model_name', return_value='a'))
        self.provider = self.enterContext(patch.object(indexing, 'get_embedding_model')).return_value
        self.provider.get_text_embedding_batch.side_effect = lambda texts: [
            [1.0, 0.0] if self.model_name.return_value == 'a' else [0.0, 1.0] for _ in texts
        ]
        self.enterContext(patch.object(indexing, 'build_document_node', side_effect=self.build_node))
        self.docs = [
            Document.objects.create(
                title=f'Synthetic {number}',
                content='Synthetic page',
                checksum=f'synthetic-generation-{number}',
                mime_type='text/plain',
            )
            for number in (1, 2)
        ]
        indexing.update_llm_index(rebuild=True)

    @staticmethod
    def build_node(doc, *, chunk_size):
        node = TextNode(
            id_=f'node-{doc.pk}',
            text=doc.content,
            metadata={'modified': doc.modified.isoformat(), 'document_id': doc.pk},
            relationships={NodeRelationship.SOURCE: RelatedNodeInfo(node_id=str(doc.pk))},
        )
        return [node]

    def assert_index(self, expected_model, documents):
        with indexing.read_store() as store:
            self.assertEqual(store.stored_model_name(), expected_model)
            self.assertEqual(set(store.get_modified_times()), {str(doc.pk) for doc in documents})
            vectors = {tuple(node.get_embedding()) for node in store.get_nodes()}
            self.assertEqual(vectors, {(1.0, 0.0)} if expected_model == 'a' else {(0.0, 1.0)})

    def test_failed_rebuild_keeps_old_generation_readable_then_retry_publishes_both_documents(self):
        self.model_name.return_value = 'b'
        calls = 0

        def fail_second(texts):
            nonlocal calls
            self.assert_index('a', self.docs)
            calls += 1
            if calls == 2:
                raise OSError('Synthetic provider interruption')
            return [[0., 1.] for _ in texts]

        self.provider.get_text_embedding_batch.side_effect = fail_second
        with self.assertRaisesRegex(OSError, 'Synthetic provider interruption'):
            indexing.update_llm_index(rebuild=False)
        self.assert_index('a', self.docs)
        self.assertFalse((self.directory / 'usl-index-build').exists())
        self.provider.get_text_embedding_batch.side_effect = lambda texts: [[0., 1.] for _ in texts]
        indexing.update_llm_index(rebuild=False)
        self.assert_index('b', self.docs)

    def test_matching_incremental_reads_no_unchanged_rows_and_refuses_a_different_model(self):
        self.provider.get_text_embedding_batch.reset_mock()
        indexing.update_llm_index(rebuild=False)
        self.provider.get_text_embedding_batch.assert_not_called()
        self.model_name.return_value = 'b'
        with self.assertRaisesRegex(ValueError, 'another embedding model'):
            indexing.llm_index_add_or_update_document(self.docs[0])
        self.provider.get_text_embedding_batch.assert_not_called()
        self.assert_index('a', self.docs)
        with indexing.read_store() as store, self.assertRaisesRegex(ValueError, 'another embedding model'):
            indexing.load_or_build_index(None, store)
        self.model_name.return_value = 'a'
        indexing.llm_index_remove_document(self.docs[1])
        self.assert_index('a', self.docs[:1])

    def test_reader_holds_old_index_and_a_later_attempt_cleans_an_interrupted_candidate(self):
        self.model_name.return_value = 'b'
        with indexing.read_store() as old, self.assertRaises(Timeout):
            self.assertEqual(old.stored_model_name(), 'a')
            indexing.update_llm_index(rebuild=False)
        self.assert_index('a', self.docs)
        candidate_dir = self.directory / 'usl-index-build'
        candidate_dir.mkdir()
        (candidate_dir / 'llmindex.db').write_bytes(b'synthetic interrupted candidate')
        indexing.update_llm_index(rebuild=False)
        self.assert_index('b', self.docs)
        self.assertFalse(candidate_dir.exists())

    def test_legacy_index_without_model_is_replaced_before_semantic_use(self):
        with indexing.write_store() as store:
            store.client.execute("DELETE FROM index_meta WHERE key = 'embed_model'")
        with (
            patch.object(semantic_api, 'get_configured_model_name', return_value='a', create=True),
            patch.object(semantic_api, 'get_embedding_model', return_value=self.provider),
            self.assertRaisesRegex(semantic_api.SemanticSearchUnavailable, 'Keyword search'),
        ):
            self.provider.get_query_embedding.return_value = [1., 0.]
            semantic_api.query_semantic_index('synthetic query', limit=2, document_ids=[self.docs[0].pk])
        indexing.update_llm_index(rebuild=False)
        self.assert_index('a', self.docs)

    def test_candidate_symlink_cannot_remove_the_active_index(self):
        (self.directory / 'usl-index-build').symlink_to(self.directory, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'real directory'):
            indexing.update_llm_index(rebuild=True)
        self.assert_index('a', self.docs)

    def test_checkpoint_disk_failure_leaves_last_complete_vectors_available(self):
        # A file-size limit in a separate Linux process simulates SQLite being
        # unable to grow only the candidate's main file. No disk is filled.
        from textwrap import dedent
        script = dedent("""
            import resource
            import signal
            import sqlite3
            import sys
            from contextlib import nullcontext
            from pathlib import Path
            from llama_index.core.schema import TextNode
            from paperless_ai.index_generation import replacement_index
            from paperless_ai.vector_store import DB_FILENAME, PaperlessSqliteVecVectorStore

            directory = Path(sys.argv[1])
            db = directory / 'usl-index-build' / DB_FILENAME
            limits = resource.getrlimit(resource.RLIMIT_FSIZE)
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            def node(number, text):
                return TextNode(id_=str(number), text=text, embedding=[0., 1.],
                                metadata={'document_id': number, 'modified': 'synthetic'})
            fault_injected = False
            try:
                with PaperlessSqliteVecVectorStore(str(directory), 'a') as live:
                    with replacement_index(live, 'b', directory, nullcontext) as candidate:
                        conn = candidate.client
                        conn.execute('PRAGMA wal_autocheckpoint=0')
                        candidate.add([node(1, 'short first document')])
                        assert tuple(conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()) == (0, 0, 0)
                        size = db.stat().st_size
                        candidate.add([node(2, 'x' * (size * 2))])
                        assert Path(str(db) + '-wal').stat().st_size > size
                        resource.setrlimit(resource.RLIMIT_FSIZE, (size, limits[1]))
                        fault_injected = True
            except (sqlite3.DatabaseError, OSError, RuntimeError):
                if not fault_injected:
                    raise
            else:
                raise AssertionError('a candidate with uncheckpointed frames was published')
            finally:
                resource.setrlimit(resource.RLIMIT_FSIZE, limits)
        """)
        result = subprocess.run([sys.executable, '-c', script, str(self.directory)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_index('a', self.docs)
        self.assertFalse((self.directory / 'usl-index-build').exists())
