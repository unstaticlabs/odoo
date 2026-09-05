import hashlib
import importlib.util
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "portable_filestore",
    ROOT / "migration/portable_filestore.py",
)
portable = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(portable)


class PortableFilestoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "filestore"
        self.root.mkdir()
        self.content = b"qualified attachment"
        self.checksum = hashlib.sha1(  # noqa: S324 - Odoo filestore identity
            self.content,
            usedforsecurity=False,
        ).hexdigest()
        self.name = f"{self.checksum[:2]}/{self.checksum}"
        source = self.root / self.name
        source.parent.mkdir()
        source.write_bytes(self.content)
        self.inventory = Path(self.temporary.name) / "inventory.tsv"
        self.inventory.write_text(
            f"{self.name}\t{self.checksum}\t{len(self.content)}\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_archive_contains_only_database_referenced_files(self):
        orphan = self.root / "ff" / ("f" * 40)
        orphan.parent.mkdir()
        orphan.write_bytes(b"orphan")
        archive = Path(self.temporary.name) / "filestore.tgz"

        result = portable.build_archive(
            self.root,
            portable.read_inventory(self.inventory),
            archive,
        )

        self.assertEqual(result["distinct_store_file_count"], 1)
        with tarfile.open(archive, "r:gz") as stream:
            self.assertEqual(stream.getnames(), [self.name])

    def test_verify_rejects_missing_tampered_and_unreferenced_files(self):
        inventory = portable.read_inventory(self.inventory)
        self.assertEqual(
            portable.verify_source(self.root, inventory)[
                "distinct_store_file_count"
            ],
            1,
        )

        (self.root / self.name).write_bytes(b"tampered attachment")
        with self.assertRaisesRegex(portable.FilestoreError, "size differs"):
            portable.verify_source(self.root, inventory)

        (self.root / self.name).write_bytes(self.content)
        extra = self.root / "ee" / ("e" * 40)
        extra.parent.mkdir()
        extra.write_bytes(b"extra")
        with self.assertRaisesRegex(portable.FilestoreError, "unreferenced"):
            portable.verify_source(self.root, inventory)

    def test_inventory_rejects_path_traversal_and_conflicts(self):
        self.inventory.write_text(
            f"../../escape\t{self.checksum}\t{len(self.content)}\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(portable.FilestoreError, "unsafe"):
            portable.read_inventory(self.inventory)

        self.inventory.write_text(
            f"{self.name}\t{self.checksum}\t{len(self.content)}\n"
            f"{self.name}\t{'0' * 40}\t{len(self.content)}\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(portable.FilestoreError, "conflicting"):
            portable.read_inventory(self.inventory)

    def test_deterministic_archive_has_no_links(self):
        first = Path(self.temporary.name) / "first.tgz"
        second = Path(self.temporary.name) / "second.tgz"
        inventory = portable.read_inventory(self.inventory)
        portable.build_archive(self.root, inventory, first)
        portable.build_archive(self.root, inventory, second)

        self.assertEqual(first.read_bytes(), second.read_bytes())
        with tarfile.open(fileobj=io.BytesIO(first.read_bytes()), mode="r:gz") as stream:
            self.assertTrue(all(member.isfile() for member in stream.getmembers()))
            self.assertTrue(
                all((member.uid, member.gid, member.mode) == (1000, 1000, 0o600)
                    for member in stream.getmembers()),
            )


if __name__ == "__main__":
    unittest.main()
