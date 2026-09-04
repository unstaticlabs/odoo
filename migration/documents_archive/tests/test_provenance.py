import unittest
from datetime import datetime

from migration.documents_archive.provenance import (
    SourceAttachment,
    SourceTimestamps,
    add_timestamps,
    select_source_attachment,
)


class DocumentsProvenanceTest(unittest.TestCase):
    def test_duplicate_content_keeps_earliest_creation_and_latest_modification(self):
        timestamps = {}
        add_timestamps(
            timestamps,
            "checksum",
            datetime(2025, 6, 1, 9, 0),
            datetime(2025, 6, 2, 10, 0),
        )
        result = add_timestamps(
            timestamps,
            "checksum",
            datetime(2025, 5, 1, 8, 0),
            datetime(2025, 7, 3, 11, 0),
        )

        self.assertEqual(result.created_at, datetime(2025, 5, 1, 8, 0))
        self.assertEqual(result.modified_at, datetime(2025, 7, 3, 11, 0))

    def test_missing_modification_falls_back_to_creation(self):
        result = add_timestamps(
            {},
            "checksum",
            datetime(2025, 5, 1, 8, 0),
        )

        self.assertEqual(result.created_at, result.modified_at)

    def test_source_attachment_resolution_uses_business_record_and_occurrence(self):
        first = SourceAttachment(
            source_id=40,
            name="receipt.pdf",
            res_model="hr.expense",
            file_size=100,
            mimetype="application/pdf",
            record_key=("hr.expense", 1, "Hotel", "2026-01-02", 42.0),
            timestamps=SourceTimestamps(
                datetime(2026, 1, 3, 9, 0),
                datetime(2026, 1, 3, 9, 5),
            ),
        )
        second = SourceAttachment(
            source_id=41,
            name=first.name,
            res_model=first.res_model,
            file_size=first.file_size,
            mimetype=first.mimetype,
            record_key=first.record_key,
            timestamps=SourceTimestamps(
                datetime(2026, 1, 3, 10, 0),
                datetime(2026, 1, 3, 10, 5),
            ),
        )

        result = select_source_attachment(
            [second, first],
            name=first.name,
            res_model=first.res_model,
            file_size=first.file_size,
            mimetype=first.mimetype,
            record_key=first.record_key,
            occurrence=1,
        )

        self.assertEqual(result, second)

    def test_source_attachment_resolution_refuses_ambiguous_unkeyed_content(self):
        timestamps = SourceTimestamps(
            datetime(2026, 1, 3, 9, 0),
            datetime(2026, 1, 3, 9, 5),
        )
        candidates = [
            SourceAttachment(
                source_id=source_id,
                name="duplicate.pdf",
                res_model="unknown.model",
                file_size=100,
                mimetype="application/pdf",
                record_key=None,
                timestamps=timestamps,
            )
            for source_id in (40, 41)
        ]

        result = select_source_attachment(
            candidates,
            name="duplicate.pdf",
            res_model="unknown.model",
            file_size=100,
            mimetype="application/pdf",
            record_key=None,
        )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
