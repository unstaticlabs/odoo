"""Online backup capture: one instant for counters and dump, files proven complete."""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from operations import cohort
from operations import stack
from operations.runtime import load_target

try:
    from test_cohort import TARGETS, backup_receipt
except ImportError:  # run as ``python -m unittest scripts.tests.…``
    from scripts.tests.test_cohort import TARGETS, backup_receipt


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def seal(value: dict) -> dict:
    body = {key: item for key, item in value.items() if key != "sha256"}
    value["sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()
    return value


def online_receipt(target: str = "production", run_id: str = "proof-online-0001") -> dict:
    value = backup_receipt(target=target, run_id=run_id)
    value.update(
        capture_mode="online",
        writers_quiesced=False,
        writer_interval_complete=False,
        writers_stopped_at=None,
        writers_resumed_at=None,
        quiescence=None,
    )
    return seal(value)


class CompletenessTests(unittest.TestCase):
    """The check that replaces the guarantee the writer pause used to give."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.filestore = self.root / "filestore"
        self.originals = self.root / "originals"
        for directory in (self.filestore, self.originals):
            directory.mkdir()
        (self.filestore / "ab").mkdir()
        self.attachment = b"attachment bytes"
        (self.filestore / "ab" / "abcdef").write_bytes(self.attachment)
        self.document = b"%PDF synthetic"
        (self.originals / "0000001.pdf").write_bytes(self.document)
        self.odoo = [("ab/abcdef", sha1(self.attachment))]
        self.paperless = [(1, "0000001.pdf", md5(self.document))]

    def check(self, odoo=None, paperless=None):
        return cohort.verify_capture_completeness(
            odoo_files=self.odoo if odoo is None else odoo,
            filestore=self.filestore,
            paperless_documents=self.paperless if paperless is None else paperless,
            originals=self.originals,
        )

    def test_complete_copy_passes_and_counts_what_it_proved(self) -> None:
        self.assertEqual(self.check(), {"odoo_files": 1, "paperless_originals": 1})

    def test_extra_files_newer_than_the_snapshot_are_not_a_problem(self) -> None:
        (self.filestore / "ab" / "later").write_bytes(b"created after the export")
        (self.originals / "0000002.pdf").write_bytes(b"consumed after the export")
        self.assertEqual(self.check()["odoo_files"], 1)

    def test_missing_odoo_file_fails_loudly(self) -> None:
        (self.filestore / "ab" / "abcdef").unlink()
        with self.assertRaisesRegex(cohort.CohortError, "1 missing, 0 mismatched.*ab/abcdef is missing"):
            self.check()

    def test_corrupted_odoo_file_fails_loudly(self) -> None:
        (self.filestore / "ab" / "abcdef").write_bytes(b"truncat")
        with self.assertRaisesRegex(cohort.CohortError, "0 missing, 1 mismatched.*different checksum"):
            self.check()

    def test_missing_and_corrupted_paperless_originals_fail_loudly(self) -> None:
        (self.originals / "0000001.pdf").write_bytes(b"%PDF torn")
        with self.assertRaisesRegex(cohort.CohortError, "document 1 original has a different checksum"):
            self.check()
        (self.originals / "0000001.pdf").unlink()
        with self.assertRaisesRegex(cohort.CohortError, "document 1 original is missing"):
            self.check()

    def test_document_without_a_stored_filename_fails_instead_of_being_guessed(self) -> None:
        with self.assertRaisesRegex(cohort.CohortError, "document 7 has no stored filename"):
            self.check(paperless=[(7, None, "0" * 32)])

    def test_a_database_value_cannot_point_outside_the_copy(self) -> None:
        (self.root / "outside").write_bytes(b"x")
        with self.assertRaisesRegex(cohort.CohortError, "escapes its root"):
            self.check(odoo=[("../outside", None)])

    def test_a_row_without_a_checksum_still_needs_its_file(self) -> None:
        self.assertEqual(self.check(odoo=[("ab/abcdef", None)])["odoo_files"], 1)
        with self.assertRaisesRegex(cohort.CohortError, "is missing"):
            self.check(odoo=[("ab/zzzzzz", None)])

    def test_every_problem_is_counted_and_only_a_few_are_shown(self) -> None:
        rows = [(f"ab/missing{index}", None) for index in range(12)]
        with self.assertRaisesRegex(cohort.CohortError, "12 missing") as raised:
            self.check(odoo=rows)
        self.assertNotIn("missing11", str(raised.exception))


class FakePostgres:
    """Records the order of everything that touches the database."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.snapshot_counter = 0

    def connect(self, **values):
        self.events.append(f"connect:{values['dbname']}")
        fake = self

        class Cursor:
            def __init__(self) -> None:
                self.query = ""

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def execute(self, query):
                self.query = query
                if "pg_export_snapshot" in query:
                    fake.snapshot_counter += 1
                    fake.events.append(f"export:{values['dbname']}")
                elif query == cohort.ODOO_FILE_SQL:
                    fake.events.append("files:odoo")
                elif query == cohort.PAPERLESS_FILE_SQL:
                    fake.events.append("files:paperless")
                else:
                    fake.events.append(f"controls:{values['dbname']}")

            def fetchone(self):
                if "pg_export_snapshot" in self.query:
                    return (f"00000003-0000000{fake.snapshot_counter}-1",)
                return ({"ledger_delta": 0, "documents": 3},)

            def fetchall(self):
                if self.query == cohort.ODOO_FILE_SQL:
                    return [("ab/abcdef", "a" * 40)]
                return [(1, "0000001.pdf", "b" * 32)]

        class Connection:
            def set_session(self, **options):
                fake.events.append(f"session:{options['isolation_level']}:{options['readonly']}")

            def cursor(self):
                return Cursor()

            def rollback(self):
                fake.events.append(f"rollback:{values['dbname']}")

            def close(self):
                fake.events.append(f"close:{values['dbname']}")

        return Connection()


ENVIRONMENT = {
    "ODOO_DB_HOST": "odoo-db", "ODOO_DB_PORT": "5432", "ODOO_DB_NAME": "odoo_prod",
    "ODOO_DB_USER": "odoo", "ODOO_DB_PASSWORD": "synthetic",
    "PAPERLESS_DB_HOST": "paperless-db", "PAPERLESS_DB_PORT": "5432",
    "PAPERLESS_DB_NAME": "paperless_prod", "PAPERLESS_DB_USER": "paperless",
    "PAPERLESS_DB_PASSWORD": "synthetic",
    "USL_TARGET": "staging", "USL_RELEASE_COMMIT": "b" * 40, "USL_RELEASE_IDENTITY": "a" * 64,
    "USL_RELEASE_MANIFEST_SHA256": "d" * 64, "USL_OLLAMA_MODEL": "model",
    "USL_OLLAMA_MANIFEST_SHA256": "e" * 64, "USL_OLLAMA_DIMENSION": "1024",
}


class OnlineCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.postgres = FakePostgres()
        self.fake_driver = types.SimpleNamespace(connect=self.postgres.connect)
        environment = mock.patch.dict(os.environ, ENVIRONMENT)
        environment.start()
        self.addCleanup(environment.stop)
        driver = mock.patch.dict(sys.modules, {"psycopg2": self.fake_driver})
        driver.start()
        self.addCleanup(driver.stop)

    def arguments(self, phase: str) -> argparse.Namespace:
        return argparse.Namespace(
            root=str(self.root), run_id="online-run-0001", mode="online", phase=phase,
        )

    def dump(self, prefix, destination, *, snapshot=None, low_priority=False):
        self.postgres.events.append(f"dump:{prefix}:{snapshot}:{low_priority}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"dump")
        return {"name": prefix.lower(), "bytes": 4, "sha256": "c" * 64}

    def begin(self, **overrides):
        def copy_durable(durable, odoo_database, *, online=False):
            self.postgres.events.append(f"copy-durable:online={online}")
            (durable / "odoo-filestore" / odoo_database).mkdir(parents=True)
            return {"mcp_secrets": None, "renderer_secrets": None, "paperless_personal_ai_keys": None}

        def copy_cache(cache, *, online=False):
            self.postgres.events.append(f"copy-cache:online={online}")
            cache.mkdir(parents=True, exist_ok=True)

        def complete(**_arguments):
            self.postgres.events.append("completeness")
            return {"odoo_files": 1, "paperless_originals": 1}

        patches = {
            "dump_database": self.dump,
            "_capture_release_manifest": lambda: "{}",
            "_capture_runtime_images": lambda _durable: None,
            "_copy_durable_material": copy_durable,
            "_copy_cache_material": copy_cache,
            "verify_capture_completeness": complete,
        }
        patches.update(overrides)
        with contextlib.ExitStack() as scope:
            for name, replacement in patches.items():
                scope.enter_context(mock.patch.object(cohort, name, replacement))
            return cohort.capture(self.arguments("begin"))

    def test_counters_files_and_dump_come_from_the_same_exported_snapshot(self) -> None:
        self.begin()
        events = self.postgres.events
        # Odoo: open, export, read the counters and file list inside that
        # transaction, dump it while the transaction is still open, then close.
        odoo = events[: events.index("close:paperless_prod")]
        self.assertEqual(odoo[:2], ["connect:odoo_prod", "session:REPEATABLE READ:True"])
        self.assertEqual(odoo[2], "export:odoo_prod")
        self.assertLess(odoo.index("controls:odoo_prod"), odoo.index("dump:ODOO:00000003-00000001-1:True"))
        self.assertLess(odoo.index("files:odoo"), odoo.index("dump:ODOO:00000003-00000001-1:True"))
        self.assertLess(odoo.index("dump:ODOO:00000003-00000001-1:True"), odoo.index("rollback:odoo_prod"))
        # The dump joins the snapshot that was exported, and each database has its own.
        self.assertIn("dump:PAPERLESS:00000003-00000002-1:True", events)

    def test_paperless_is_exported_after_odoo_and_files_are_copied_after_both(self) -> None:
        # Odoo stores Paperless document ids; Paperless stores no Odoo id. The
        # later export is therefore the superset of what the earlier one cites.
        self.begin()
        events = self.postgres.events
        self.assertLess(events.index("close:odoo_prod"), events.index("connect:paperless_prod"))
        self.assertLess(events.index("close:paperless_prod"), events.index("copy-durable:online=True"))
        self.assertLess(events.index("copy-durable:online=True"), events.index("completeness"))
        self.assertLess(events.index("copy-cache:online=True"), events.index("completeness"))

    def test_controls_are_the_ones_read_in_the_snapshot_and_survive_to_the_manifest(self) -> None:
        self.begin()
        state = json.loads((self.root / ".online-run-0001.partial" / cohort.ONLINE_STATE).read_text())
        self.assertEqual(state["controls"]["odoo"], {"ledger_delta": 0, "documents": 3})
        self.assertEqual(state["controls"]["paperless"], {"ledger_delta": 0, "documents": 3})
        self.assertEqual(state["phase"], "begun")

    def test_an_incomplete_copy_fails_the_capture_and_leaves_nothing_behind(self) -> None:
        def incomplete(**_arguments):
            raise cohort.CohortError("online capture is incomplete: 1 missing, 0 mismatched")

        with self.assertRaisesRegex(cohort.CohortError, "incomplete"):
            self.begin(verify_capture_completeness=incomplete)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_a_failed_dump_does_not_leave_a_workspace(self) -> None:
        def broken(*_args, **_kwargs):
            raise cohort.CohortError("pg_dump failed")

        with self.assertRaisesRegex(cohort.CohortError, "pg_dump failed"):
            self.begin(dump_database=broken)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertIn("rollback:odoo_prod", self.postgres.events)

    def test_the_phases_run_in_order_and_seal_the_same_manifest_a_quiesced_capture_does(self) -> None:
        self.begin()
        with self.assertRaisesRegex(cohort.CohortError, "not ready"):
            cohort.capture(self.arguments("finish"))
        sealed = {}

        def sign(durable):
            (durable / "sign-secrets").mkdir(parents=True)
            return {"files": 0, "bytes": 0, "sha256": "f" * 64}

        def seal_capture(**values):
            sealed.update(values)
            return {"status": "sealed"}

        with (
            mock.patch.object(cohort, "_copy_sign_material", sign),
            mock.patch.object(cohort, "_seal_capture", seal_capture),
        ):
            self.assertEqual(cohort.capture(self.arguments("sign"))["status"], "signed")
            with self.assertRaisesRegex(cohort.CohortError, "not ready"):
                cohort.capture(self.arguments("sign"))
            self.assertEqual(cohort.capture(self.arguments("finish")), {"status": "sealed"})
        self.assertEqual(sealed["sign_secrets_identity"]["sha256"], "f" * 64)
        self.assertEqual(sealed["controls"]["odoo"]["documents"], 3)
        self.assertEqual(sealed["databases"]["odoo"]["sha256"], "c" * 64)
        self.assertFalse((self.root / ".online-run-0001.partial" / cohort.ONLINE_STATE).exists())

    def test_a_failed_sign_phase_discards_the_workspace_and_abort_is_idempotent(self) -> None:
        self.begin()
        with (
            mock.patch.object(cohort, "_copy_sign_material", side_effect=cohort.CohortError("sign torn")),
            self.assertRaisesRegex(cohort.CohortError, "sign torn"),
        ):
            cohort.capture(self.arguments("sign"))
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(cohort.capture(self.arguments("abort"))["status"], "discarded")

    def test_abort_discards_a_workspace_that_a_later_phase_never_reached(self) -> None:
        self.begin()
        self.assertEqual(cohort.capture(self.arguments("abort"))["status"], "discarded")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_online_capture_requires_a_phase(self) -> None:
        arguments = self.arguments("begin")
        arguments.phase = None
        with self.assertRaisesRegex(cohort.CohortError, "requires --phase"):
            cohort.capture(arguments)

    def test_a_real_sealed_manifest_is_the_unchanged_v2_schema(self) -> None:
        # Old and new captures must be indistinguishable to verify and materialize.
        partial = self.root / ".sealed.partial"
        durable, cache = partial / "durable", partial / "cache"
        for path in (
            durable / "odoo-filestore/odoo_prod", durable / "paperless-media/documents/originals",
            durable / "paperless-trash", durable / "paperless-consume", durable / "mcp-oauth",
            durable / "sign-secrets", durable / "sign-evidence",
            cache / "paperless-media/documents/archive", cache / "paperless-media/documents/thumbnails",
            cache / "paperless-data/index", cache / "paperless-data/llm_index",
        ):
            path.mkdir(parents=True)
        (durable / "release.json").write_text("{}")
        sign = cohort.tree_identity(durable / "sign-secrets")
        with mock.patch.dict(os.environ, {"USL_TARGET": "staging"}):
            manifest = cohort._seal_capture(
                partial=partial, root=self.root / "sealed", run_id="online-run-0001",
                odoo_database="odoo_prod",
                databases={
                    "odoo": {"name": "o", "bytes": 1, "sha256": "a" * 64},
                    "paperless": {"name": "p", "bytes": 1, "sha256": "b" * 64},
                },
                controls={"odoo": {"ledger_delta": 0}, "paperless": {}},
                identities={"mcp_secrets": None, "renderer_secrets": None, "paperless_personal_ai_keys": None},
                sign_secrets_identity=sign,
            )
        self.assertEqual(manifest["schema"], cohort.SCHEMA)
        self.assertEqual(cohort.validate_manifest(manifest), manifest)
        self.assertTrue((self.root / "sealed" / "manifest.json").is_file())


class PgDumpCommandTests(unittest.TestCase):
    def test_the_quiesced_dump_command_is_unchanged(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root,
            mock.patch.dict(os.environ, ENVIRONMENT),
            mock.patch.object(cohort, "run") as run,
            mock.patch.object(cohort, "sha256_file", return_value="a" * 64),
        ):
            destination = Path(root) / "databases/odoo.dump"
            destination.parent.mkdir()
            destination.write_bytes(b"dump")
            cohort.dump_database("ODOO", destination)
        self.assertEqual(run.call_args_list[0].args[0][0], "pg_dump")
        self.assertNotIn("--snapshot", " ".join(run.call_args_list[0].args[0]))
        self.assertNotIn("nice", run.call_args_list[0].args[0])

    def test_the_online_dump_joins_the_snapshot_at_low_priority(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root,
            mock.patch.dict(os.environ, ENVIRONMENT),
            mock.patch.object(cohort, "run") as run,
            mock.patch.object(cohort, "sha256_file", return_value="a" * 64),
            mock.patch.object(cohort.shutil, "which", return_value="/usr/bin/tool"),
        ):
            destination = Path(root) / "databases/odoo.dump"
            destination.parent.mkdir()
            destination.write_bytes(b"dump")
            cohort.dump_database("ODOO", destination, snapshot="00000003-00000001-1", low_priority=True)
        command = run.call_args_list[0].args[0]
        self.assertEqual(command[:6], ["nice", "-n", "10", "ionice", "-c", "3"])
        self.assertIn("--snapshot=00000003-00000001-1", command)


class LiveCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        # ``cp --reflink`` is a GNU option; the backup image is Debian.
        def copy_tree(source, destination, *, required=True):
            if source.is_dir():
                shutil.copytree(source, destination, symlinks=True)
                destination.chmod(0o755)  # a read-only source mount is not a read-only copy

        copy = mock.patch.object(cohort, "copy_tree", copy_tree)
        copy.start()
        self.addCleanup(copy.stop)

    def test_a_sqlite_database_with_an_open_writer_is_copied_consistently(self) -> None:
        source = self.root / "oauth"
        source.mkdir()
        (source / "notes.txt").write_text("plain file")
        database = source / "auth.db"
        writer = sqlite3.connect(database)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE credential (id INTEGER PRIMARY KEY, value TEXT)")
        writer.executemany("INSERT INTO credential (value) VALUES (?)", [("v",)] * 50)
        writer.commit()
        # Committed rows that only the write-ahead log holds: a plain file copy
        # taken without the log would lose them or tear the database.
        self.assertTrue((source / "auth.db-wal").stat().st_size > 0)
        cohort.copy_tree_online(source, self.root / "copy")
        writer.close()
        copy = self.root / "copy"
        self.assertEqual((copy / "notes.txt").read_text(), "plain file")
        restored = sqlite3.connect(copy / "auth.db")
        self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(restored.execute("SELECT count(*) FROM credential").fetchone()[0], 50)
        restored.close()
        self.assertFalse((copy / "auth.db-wal").exists())
        self.assertFalse((copy / "auth.db-shm").exists())

    def test_the_source_is_opened_read_only(self) -> None:
        source = self.root / "oauth"
        source.mkdir()
        database = sqlite3.connect(source / "auth.db")
        database.execute("CREATE TABLE credential (id INTEGER)")
        database.commit()
        database.close()
        before = (source / "auth.db").read_bytes()
        cohort.copy_tree_online(source, self.root / "copy")
        self.assertEqual((source / "auth.db").read_bytes(), before)

    def test_an_idle_wal_database_on_a_read_only_mount_is_still_copied(self) -> None:
        # No process has it open, so there is no -wal or -shm; a read-only mount
        # cannot create them either, which plain read-only access would refuse.
        source = self.root / "oauth"
        source.mkdir()
        database = sqlite3.connect(source / "auth.db")
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("CREATE TABLE credential (id INTEGER PRIMARY KEY)")
        database.executemany("INSERT INTO credential DEFAULT VALUES", [()] * 7)
        database.commit()
        database.close()
        self.assertEqual(sorted(path.name for path in source.iterdir()), ["auth.db"])
        source.chmod(0o555)
        self.addCleanup(source.chmod, 0o755)
        if os.access(source, os.W_OK):
            self.skipTest("the directory stays writable (running as root)")
        cohort.copy_tree_online(source, self.root / "copy")
        restored = sqlite3.connect(self.root / "copy" / "auth.db")
        self.assertEqual(restored.execute("SELECT count(*) FROM credential").fetchone()[0], 7)
        restored.close()

    def test_an_absent_optional_tree_is_skipped(self) -> None:
        cohort.copy_tree_online(self.root / "absent", self.root / "copy", required=False)
        self.assertFalse((self.root / "copy").exists())


class SearchIndexTests(unittest.TestCase):
    def index(self, segments: list[str], present: list[str], meta: str | None = None) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        (root / "meta.json").write_text(meta if meta is not None else json.dumps(
            {"segments": [{"segment_id": value} for value in segments]},
        ))
        for name in present:
            (root / name).write_bytes(b"x")
        return root

    def test_a_consistent_copy_is_left_alone(self) -> None:
        index = self.index(
            ["0123abcd-0000-0000-0000-000000000001"],
            ["0123abcd000000000000000000000001.idx", "0123abcd000000000000000000000001.term"],
        )
        self.assertFalse(cohort.mark_unreadable_index_for_rebuild(index))
        self.assertFalse((index / ".rebuilding").exists())

    def test_a_segment_deleted_by_a_merge_during_the_copy_marks_the_index_for_rebuild(self) -> None:
        index = self.index(["0123abcd-0000-0000-0000-000000000001"], [])
        self.assertTrue(cohort.mark_unreadable_index_for_rebuild(index))
        self.assertTrue((index / ".rebuilding").exists())

    def test_unreadable_metadata_marks_the_index_for_rebuild(self) -> None:
        for meta in ("not json", "{}"):
            with self.subTest(meta=meta):
                index = self.index([], [], meta)
                self.assertTrue(cohort.mark_unreadable_index_for_rebuild(index))

    def test_a_missing_index_marks_nothing_it_cannot_create(self) -> None:
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, True)
        with self.assertRaises(OSError):
            cohort.mark_unreadable_index_for_rebuild(directory / "absent")


class ReceiptTests(unittest.TestCase):
    """Every input class the receipt gate judges, with the consumers that use it."""

    def validate(self, value, **options):
        return stack._backup_run_receipt(
            value, target=value["capture"]["target"], run_id=value["run_id"], **options,
        )

    def test_online_receipt_is_accepted(self) -> None:
        value = online_receipt()
        self.assertEqual(self.validate(value)["capture_mode"], "online")

    def test_quiesced_receipt_with_an_explicit_mode_is_accepted_for_the_release_path(self) -> None:
        value = backup_receipt()
        value["capture_mode"] = "quiesced"
        seal(value)
        services = [
            load_target("staging", TARGETS).value["services"][role]
            for role in stack.BACKUP_WRITER_SERVICE_ROLES
        ]
        self.assertEqual(
            self.validate(value, require_quiesced=True, expected_writer_services=services)["capture_mode"],
            "quiesced",
        )

    def test_receipts_written_before_the_online_capture_still_validate(self) -> None:
        legacy = backup_receipt()
        self.assertNotIn("capture_mode", legacy)
        services = [
            load_target("staging", TARGETS).value["services"][role]
            for role in stack.BACKUP_WRITER_SERVICE_ROLES
        ]
        self.validate(legacy, require_quiesced=True, expected_writer_services=services)
        complete_interval = copy.deepcopy(legacy)
        complete_interval.update(
            writers_quiesced=False, writer_interval_complete=True, quiescence=None,
            writers_stopped_at="2026-09-04T00:00:00Z", writers_resumed_at="2026-09-04T00:03:00Z",
        )
        complete_interval["performance"].update(writer_freeze_seconds=185.0, writer_freeze_sla_passed=False)
        self.validate(seal(complete_interval))
        resumed = copy.deepcopy(legacy)
        resumed.update(
            writers_quiesced=False, writer_interval_complete=False, quiescence=None,
            writers_stopped_at=None,
        )
        self.validate(seal(resumed))

    def test_unknown_mode_and_stray_fields_are_refused(self) -> None:
        value = online_receipt()
        value["capture_mode"] = "live"
        with self.assertRaisesRegex(RuntimeError, "capture mode is invalid"):
            self.validate(seal(value))
        value = online_receipt()
        value["unexpected"] = 1
        with self.assertRaisesRegex(RuntimeError, "fields differ"):
            self.validate(seal(value))

    def test_an_online_receipt_cannot_claim_a_writer_interval(self) -> None:
        claims = {
            "writers_quiesced": True,
            "writer_interval_complete": True,
            "writers_stopped_at": "2026-09-04T00:00:00Z",
            "writers_resumed_at": "2026-09-04T00:00:05Z",
            "quiescence": backup_receipt()["quiescence"],
        }
        for field, claim in claims.items():
            with self.subTest(field=field):
                value = online_receipt()
                value[field] = claim
                with self.assertRaisesRegex(RuntimeError, "claims a writer interval"):
                    self.validate(seal(value))
        for field in ("writer_freeze_seconds", "writer_freeze_sla_passed"):
            with self.subTest(field=field):
                value = online_receipt()
                value["performance"][field] = 1 if field.endswith("seconds") else True
                with self.assertRaisesRegex(RuntimeError, "claims a writer interval"):
                    self.validate(seal(value))

    def test_an_online_receipt_can_never_authorise_a_release_that_needs_stopped_writers(self) -> None:
        value = online_receipt(target="staging", run_id="attempt-20260904-staging")
        services = [
            load_target("staging", TARGETS).value["services"][role]
            for role in stack.BACKUP_WRITER_SERVICE_ROLES
        ]
        with self.assertRaisesRegex(RuntimeError, "did not leave writers quiesced"):
            self.validate(value, require_quiesced=True, expected_writer_services=services)

    def test_a_tampered_online_receipt_fails_its_digest(self) -> None:
        value = online_receipt()
        value["capture_mode"] = "quiesced"
        with self.assertRaisesRegex(RuntimeError, "digest differs|claims|quiesc"):
            self.validate(value)


class ProofRecoveryTests(unittest.TestCase):
    def test_an_interrupted_online_capture_restarts_only_sign(self) -> None:
        target = load_target("production", TARGETS)
        online = [target.value["services"][role] for role in stack.BACKUP_ONLINE_PAUSE_SERVICE_ROLES]
        receipt = stack._backup_quiescence_receipt(
            target="production", run_id="proof-online-0001", baseline="a" * 64,
            services=online, status="quiesced",
            prepared_at="2026-09-04T00:00:00Z", stopped_at="2026-09-04T00:01:00Z",
        )
        runner = mock.Mock()
        commands: list[list[str]] = []

        def run(command, *, check=True, input_text=None):
            commands.append(command)
            if command[0] == "cat" and command[1].endswith("/quiesced.json"):
                return subprocess.CompletedProcess(command, 0, json.dumps(receipt), "")
            return subprocess.CompletedProcess(command, 1 if command[0] == "cat" else 0, "", "")

        runner.run.side_effect = run
        identity = {
            "project": "p", "working_directory": "/w", "environment_file": "/e",
            "compose_files": ["/c.yaml"],
        }
        with (
            mock.patch.object(stack, "_recover_interrupted_backup_lock", return_value=True) as lock,
            mock.patch.object(stack, "compose_identity", return_value=identity),
            mock.patch.object(stack, "inspect_runtime", return_value={}),
            mock.patch.object(stack, "_runtime_cas_sha256", return_value="a" * 64),
        ):
            resumable = stack._recover_recovery_proof_backup(
                target, runner, "proof-online-0001", "a" * 64,
            )
        # No cohort was captured, so the next attempt starts over rather than resuming.
        self.assertFalse(resumable)
        lock.assert_called_once()
        ups = [command for command in commands if "up" in command]
        self.assertEqual(len(ups), 1)
        self.assertEqual(ups[0][-len(online):], online)
        self.assertFalse(any(service in ups[0] for service in (
            target.value["services"]["odoo"], target.value["services"]["paperless"],
        )))

    def test_resumed_runs_keep_the_mode_they_were_captured_in(self) -> None:
        target = load_target("production", TARGETS)
        online = [target.value["services"][role] for role in stack.BACKUP_ONLINE_PAUSE_SERVICE_ROLES]
        everything = [target.value["services"][role] for role in stack.BACKUP_WRITER_SERVICE_ROLES]
        for services, expected in ((online, "online"), (everything, "quiesced")):
            runner = mock.Mock()
            runner.run.return_value = subprocess.CompletedProcess(
                [], 0, json.dumps({"writer_services": services}), "",
            )
            self.assertEqual(stack._resumed_capture_mode(target, runner, "proof-x-0001"), expected)
        runner = mock.Mock()
        runner.run.return_value = subprocess.CompletedProcess([], 1, "", "")
        self.assertEqual(stack._resumed_capture_mode(target, runner, "proof-x-0001"), "quiesced")


if __name__ == "__main__":
    unittest.main()
