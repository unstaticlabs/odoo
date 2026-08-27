import logging
from time import perf_counter

from odoo import Command
from odoo.tests import TransactionCase, tagged

from odoo.addons.mail.tests.common import mail_new_test_user

_logger = logging.getLogger(__name__)


@tagged("post_install", "-at_install", "usl_documents_performance")
class TestDocumentsPerformance(TransactionCase):
    """Guard the document fields used by common list and form views.

    The fixture is deliberately larger than Odoo's default list page so this
    test catches query counts which grow once per displayed business record.
    """

    RECORD_COUNT = 40

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = mail_new_test_user(
            cls.env,
            login="documents-performance-user",
            name="Documents Performance User",
            company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.company.ids)],
            groups="usl_documents.group_documents_user",
        )
        cls.partners = cls.env["res.partner"].create(
            [
                {
                    "name": f"Performance partner {index:02d}",
                    "company_id": cls.env.company.id,
                }
                for index in range(cls.RECORD_COUNT)
            ],
        )
        cls.documents = cls.env["usl.document"].create(
            [
                {
                    "name": f"Performance document {index:02d}",
                    "paperless_id": 900_000 + index,
                    "company_id": cls.env.company.id,
                    "confidentiality": "internal",
                    "review_state": "classified",
                    "permission_sync_state": "synchronized",
                }
                for index in range(cls.RECORD_COUNT)
            ],
        )
        cls.links = cls.env["usl.document.link"].create(
            [
                {
                    "document_id": document.id,
                    "res_model": "res.partner",
                    "res_id": partner.id,
                    "record_name": partner.display_name,
                    "company_id": cls.env.company.id,
                    "linked_by_id": cls.env.user.id,
                }
                for document, partner in zip(cls.documents, cls.partners)
            ],
        )
        cache_models = {
            "tag": "usl.paperless.tag",
            "correspondent": "usl.paperless.correspondent",
            "document_type": "usl.paperless.document.type",
        }
        cls.metadata = {}
        for offset, (key, model_name) in enumerate(cache_models.items()):
            cls.metadata[key] = (
                cls.env[model_name]
                .sudo()
                .with_context(usl_documents_cache_write=True)
                .create(
                    {
                        "name": f"Performance {key}",
                        "paperless_id": 910_000 + offset,
                    },
                )
            )

    def _measure_read(self, records, fields):
        self.env.cr.flush()
        self.env.invalidate_all()
        records = records.with_user(self.user)
        query_start = self.env.cr.sql_log_count
        time_start = perf_counter()
        values = records.read(fields)
        duration = perf_counter() - time_start
        queries = self.env.cr.sql_log_count - query_start
        return values, queries, duration

    def test_business_record_document_badges_have_bounded_query_count(self):
        values, queries, duration = self._measure_read(
            self.partners,
            ["archived_document_count"],
        )

        self.assertEqual(
            [value["archived_document_count"] for value in values],
            [1] * self.RECORD_COUNT,
        )
        self.assertLessEqual(queries, 15)
        _logger.info(
            "PERF archived document badges: records=%d queries=%d duration=%.6fs",
            self.RECORD_COUNT,
            queries,
            duration,
        )

    def test_document_link_helpers_have_bounded_query_count(self):
        values, queries, duration = self._measure_read(
            self.documents,
            ["link_count", "has_linked_record"],
        )

        self.assertEqual(
            [(value["link_count"], value["has_linked_record"]) for value in values],
            [(1, True)] * self.RECORD_COUNT,
        )
        self.assertLessEqual(queries, 25)
        _logger.info(
            "PERF document link helpers: records=%d queries=%d duration=%.6fs",
            self.RECORD_COUNT,
            queries,
            duration,
        )

    def test_sync_metadata_resolution_has_bounded_query_count(self):
        payloads = [
            {
                "id": 920_000 + index,
                "title": f"Synchronized document {index:02d}",
                "tags": [self.metadata["tag"].paperless_id],
                "correspondent": self.metadata["correspondent"].paperless_id,
                "document_type": self.metadata["document_type"].paperless_id,
            }
            for index in range(self.RECORD_COUNT)
        ]
        self.env.cr.flush()
        self.env.invalidate_all()
        query_start = self.env.cr.sql_log_count
        time_start = perf_counter()
        metadata_records = self.env[
            "usl.document"
        ]._paperless_metadata_records()
        values = [
            self.env["usl.document"]._paperless_values(
                payload,
                metadata_records=metadata_records,
            )
            for payload in payloads
        ]
        duration = perf_counter() - time_start
        queries = self.env.cr.sql_log_count - query_start

        self.assertEqual(len(values), self.RECORD_COUNT)
        self.assertTrue(all(value["tag_ids"] for value in values))
        self.assertLessEqual(queries, 10)
        _logger.info(
            "PERF sync metadata resolution: records=%d queries=%d duration=%.6fs",
            self.RECORD_COUNT,
            queries,
            duration,
        )

    def test_workspace_page_and_facets_have_bounded_query_count(self):
        self.env.cr.flush()
        self.env.invalidate_all()
        documents = self.env["usl.document"].with_user(self.user)
        query_start = self.env.cr.sql_log_count
        time_start = perf_counter()
        result = documents.workspace_data(
            workspace="all",
            page_size=self.RECORD_COUNT,
            include_workspace_metadata=True,
        )
        duration = perf_counter() - time_start
        queries = self.env.cr.sql_log_count - query_start

        self.assertEqual(len(result["documents"]), self.RECORD_COUNT)
        self.assertEqual(len(result["link_facets"]), self.RECORD_COUNT)
        self.assertLessEqual(queries, 85)
        _logger.info(
            "PERF Documents workspace: records=%d queries=%d duration=%.6fs",
            self.RECORD_COUNT,
            queries,
            duration,
        )


@tagged("post_install", "-at_install", "usl_documents_performance")
class TestDocumentArchiveStatusPerformance(TransactionCase):
    """Keep operation-heavy status profiling isolated from document fixtures."""

    RECORD_COUNT = 40

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = mail_new_test_user(
            cls.env,
            login="documents-operation-performance-user",
            name="Documents Operation Performance User",
            company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.company.ids)],
            groups="usl_documents.group_documents_user",
        )
        cls.partners = cls.env["res.partner"].create(
            [
                {
                    "name": f"Operation performance partner {index:02d}",
                    "company_id": cls.env.company.id,
                }
                for index in range(cls.RECORD_COUNT)
            ],
        )
        cls.env["usl.document.operation"].create(
            [
                {
                    "name": f"Pending operation {index:02d}",
                    "state": "processing",
                    "checksum": f"pending-{index:02d}",
                    "company_id": cls.env.company.id,
                    "res_model": "res.partner",
                    "res_id": partner.id,
                }
                for index, partner in enumerate(cls.partners)
            ]
            + [
                {
                    "name": f"Failed operation {index:02d}",
                    "state": "failed",
                    "checksum": f"failed-{index:02d}",
                    "company_id": cls.env.company.id,
                    "res_model": "res.partner",
                    "res_id": partner.id,
                }
                for index, partner in enumerate(cls.partners)
            ],
        )

    def test_business_record_archive_status_has_bounded_query_count(self):
        self.env.cr.flush()
        self.env.invalidate_all()
        partners = self.partners.with_user(self.user)
        query_start = self.env.cr.sql_log_count
        time_start = perf_counter()
        values = partners.read(
            ["document_archive_pending_count", "document_archive_failure_count"],
        )
        duration = perf_counter() - time_start
        queries = self.env.cr.sql_log_count - query_start

        self.assertEqual(
            [
                (
                    value["document_archive_pending_count"],
                    value["document_archive_failure_count"],
                )
                for value in values
            ],
            [(1, 1)] * self.RECORD_COUNT,
        )
        self.assertLessEqual(queries, 12)
        _logger.info(
            "PERF archive status badges: records=%d queries=%d duration=%.6fs",
            self.RECORD_COUNT,
            queries,
            duration,
        )
