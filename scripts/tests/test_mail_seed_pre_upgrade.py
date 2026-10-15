"""Real PostgreSQL contract tests; opt in with a disposable test DSN.

Run in the Odoo runtime with USL_MAIL_SEED_TEST_DSN naming a database whose
name starts with odoo_test_mail_seed. Every case owns an isolated schema.
The packaged/native upgrade smoke test additionally exercises real Mail data.
"""
from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import threading
import time
import unittest
import uuid

try:
    import psycopg2
except ImportError:
    psycopg2 = None

ROOT = Path(__file__).resolve().parents[2]
DSN = os.environ.get("USL_MAIL_SEED_TEST_DSN")
GENERAL = "channel_member_general_channel_for_admin"
ADMIN = "channel_member_channel_admin_partner_admin"


class Cursor:
    """Expose the native cursor's savepoint API over a real PostgreSQL cursor."""

    def __init__(self, connection, before_execute=None):
        self.connection = connection
        self.cursor = connection.cursor()
        self.before_execute = before_execute

    def execute(self, sql, params=None):
        if self.before_execute:
            self.before_execute(sql, params)
        return self.cursor.execute(sql, params)

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    @contextmanager
    def savepoint(self, flush=True):
        assert flush is False, "Pre-registry repair must not construct or flush an ORM environment"
        self.cursor.execute("SAVEPOINT qa_mail_seed")
        try:
            yield
        except BaseException:
            self.cursor.execute("ROLLBACK TO SAVEPOINT qa_mail_seed")
            self.cursor.execute("RELEASE SAVEPOINT qa_mail_seed")
            raise
        else:
            self.cursor.execute("RELEASE SAVEPOINT qa_mail_seed")


@unittest.skipUnless(DSN, "Real PostgreSQL Mail seed tests require USL_MAIL_SEED_TEST_DSN")
class MailSeedPreUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if psycopg2 is None:
            raise RuntimeError("Configured PostgreSQL tests require the runtime psycopg2 dependency")
        options = psycopg2.extensions.parse_dsn(DSN)
        if not options.get("dbname", "").startswith("odoo_test_mail_seed"):
            raise RuntimeError("Use a disposable odoo_test_mail_seed* database")
        spec = importlib.util.spec_from_file_location("mail_seed_pre_upgrade", ROOT / "docker/pre_upgrade.py")
        cls.hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.hook)

    def setUp(self):
        self.schema = "qa_mail_seed_" + uuid.uuid4().hex
        self.connection = psycopg2.connect(DSN)
        self.connection.autocommit = True
        with self.connection.cursor() as cr:
            cr.execute('CREATE SCHEMA "' + self.schema + '"')
            cr.execute('SET search_path TO "' + self.schema + '"')
            cr.execute("""
                CREATE TABLE ir_model_data (
                    id serial PRIMARY KEY, module text NOT NULL, name text NOT NULL,
                    model text NOT NULL, res_id integer, noupdate boolean NOT NULL DEFAULT false,
                    UNIQUE(module, name));
                CREATE TABLE res_partner (id integer PRIMARY KEY);
                CREATE TABLE discuss_channel (id integer PRIMARY KEY);
                CREATE TABLE discuss_channel_member (
                    id integer PRIMARY KEY, partner_id integer NOT NULL,
                    channel_id integer NOT NULL, channel_role text,
                    seen_message_id integer, unread_counter integer,
                    custom_state text, UNIQUE(partner_id, channel_id));
                INSERT INTO res_partner VALUES (1), (2);
                INSERT INTO discuss_channel VALUES (10), (20), (30);
                INSERT INTO ir_model_data(module,name,model,res_id,noupdate) VALUES
                    ('base','partner_admin','res.partner',1,true),
                    ('mail','channel_all_employees','discuss.channel',10,true),
                    ('mail','channel_admin','discuss.channel',20,true);
                INSERT INTO discuss_channel_member VALUES
                    (101,1,10,'admin',900,7,'retained general state'),
                    (102,1,20,'owner',901,3,'retained admin state'),
                    (103,2,30,NULL,902,1,'unrelated membership');
            """)
        self.connection.autocommit = False
        self.connection.set_session(isolation_level="REPEATABLE READ")
        self.cr = Cursor(self.connection)
        self.cr.execute("SHOW transaction_isolation")
        self.assertEqual(self.cr.fetchone()[0], "repeatable read")
        self.members_before = self.members()

    def tearDown(self):
        self.connection.rollback()
        self.connection.autocommit = True
        with self.connection.cursor() as cr:
            cr.execute('DROP SCHEMA "' + self.schema + '" CASCADE')
        self.connection.close()

    def migrate(self, cr=None):
        self.hook.migrate(cr or self.cr, "saas~19.3.1.0")

    def members(self):
        self.cr.execute("SELECT * FROM discuss_channel_member ORDER BY id")
        return self.cr.fetchall()

    def bindings(self):
        self.cr.execute("SELECT name,model,res_id,noupdate FROM ir_model_data WHERE module='mail' AND name IN (%s,%s) ORDER BY name", (GENERAL, ADMIN))
        return self.cr.fetchall()

    def bind(self, name, target, model="discuss.channel.member", noupdate=True):
        self.cr.execute("INSERT INTO ir_model_data(module,name,model,res_id,noupdate) VALUES ('mail',%s,%s,%s,%s)", (name, model, target, noupdate))

    def assert_preserved(self):
        self.assertEqual(self.members(), self.members_before)

    def test_missing_bindings_reuse_both_members_without_replaying_roles(self):
        self.migrate()
        self.assertEqual(self.bindings(), [(ADMIN, "discuss.channel.member", 102, True), (GENERAL, "discuss.channel.member", 101, True)])
        self.assert_preserved()

    def test_repeated_run_preserves_metadata_and_all_members(self):
        self.migrate()
        self.cr.execute("SELECT * FROM ir_model_data ORDER BY id")
        before = self.cr.fetchall()
        self.migrate()
        self.cr.execute("SELECT * FROM ir_model_data ORDER BY id")
        self.assertEqual(self.cr.fetchall(), before)
        self.assert_preserved()

    def test_healthy_bindings_are_noops_even_with_existing_noupdate_false(self):
        self.bind(GENERAL, 101, noupdate=False)
        self.bind(ADMIN, 102)
        self.cr.execute("SELECT * FROM ir_model_data ORDER BY id")
        before = self.cr.fetchall()
        self.migrate()
        self.cr.execute("SELECT * FROM ir_model_data ORDER BY id")
        self.assertEqual(self.cr.fetchall(), before)
        self.assert_preserved()

    def test_dangling_correct_model_rebinds_only_to_unique_member(self):
        self.bind(GENERAL, 99999, noupdate=False)
        self.migrate()
        self.assertIn((GENERAL, "discuss.channel.member", 101, True), self.bindings())
        self.assert_preserved()

    def test_absent_binding_and_member_leave_native_creation_untouched(self):
        self.cr.execute("DELETE FROM discuss_channel_member WHERE id IN (101,102)")
        before = self.members()
        self.migrate()
        self.assertEqual(self.bindings(), [])
        self.assertEqual(self.members(), before)

    def test_dangling_binding_without_candidate_refuses(self):
        self.bind(GENERAL, 99999)
        self.cr.execute("DELETE FROM discuss_channel_member WHERE id=101")
        with self.assertRaisesRegex(RuntimeError, "Mail seed"):
            self.migrate()
        self.assertEqual(self.bindings(), [(GENERAL, "discuss.channel.member", 99999, True)])

    def test_wrong_model_refuses_without_adopting_the_other_seed(self):
        self.bind(ADMIN, 102, model="res.partner")
        with self.assertRaisesRegex(RuntimeError, "Mail seed"):
            self.migrate()
        self.assertEqual(self.bindings(), [(ADMIN, "res.partner", 102, True)])
        self.assert_preserved()

    def test_live_wrong_target_refuses_without_repointing(self):
        self.bind(ADMIN, 103)
        with self.assertRaisesRegex(RuntimeError, "Mail seed"):
            self.migrate()
        self.assertEqual(self.bindings(), [(ADMIN, "discuss.channel.member", 103, True)])
        self.assert_preserved()

    def test_malformed_anchors_refuse_before_any_metadata_write(self):
        for mutation in [
            "DELETE FROM ir_model_data WHERE module='base' AND name='partner_admin'",
            "UPDATE ir_model_data SET model='res.users' WHERE module='base'",
            "UPDATE ir_model_data SET res_id=99999 WHERE module='base'",
            "UPDATE ir_model_data SET res_id=NULL WHERE module='base'",
            "DELETE FROM ir_model_data WHERE name='channel_admin'",
            "UPDATE ir_model_data SET model='res.partner' WHERE name='channel_admin'",
        ]:
            with self.subTest(mutation=mutation), self.cr.savepoint(flush=False):
                self.cr.execute(mutation)
                with self.assertRaisesRegex(RuntimeError, "Mail seed"):
                    self.migrate()
                self.assertEqual(self.bindings(), [])
                self.assert_preserved()
                # Restore the fixture for the next generated state.
                self.cr.execute("ROLLBACK TO SAVEPOINT qa_mail_seed")

    def test_ambiguous_expected_members_refuse(self):
        self.cr.execute("ALTER TABLE discuss_channel_member DROP CONSTRAINT discuss_channel_member_partner_id_channel_id_key")
        self.cr.execute("INSERT INTO discuss_channel_member VALUES (104,1,10,'owner',903,0,'ambiguous fixture')")
        before = self.members()
        with self.assertRaisesRegex(RuntimeError, "Mail seed"):
            self.migrate()
        self.assertEqual(self.bindings(), [])
        self.assertEqual(self.members(), before)

    def test_invalid_second_mapping_is_validated_before_first_write(self):
        self.bind(ADMIN, 103)
        writes = []
        cr = Cursor(self.connection, lambda sql, params: writes.append(sql) if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) else None)
        with self.assertRaisesRegex(RuntimeError, "Mail seed"):
            self.migrate(cr)
        self.assertEqual(writes, [])
        self.assert_preserved()

    def test_either_seed_dangling_binding_is_recovered_without_member_writes(self):
        for name, target in [(GENERAL, 101), (ADMIN, 102)]:
            with self.subTest(seed=name), self.cr.savepoint(flush=False):
                self.bind(name, 99999, noupdate=False)
                self.migrate()
                self.assertIn((name, "discuss.channel.member", target, True), self.bindings())
                self.assert_preserved()
                self.cr.execute("ROLLBACK TO SAVEPOINT qa_mail_seed")

    def test_either_seed_wrong_model_or_live_target_refuses(self):
        for name in [GENERAL, ADMIN]:
            for model in ["res.partner", "discuss.channel.member"]:
                with self.subTest(seed=name, model=model), self.cr.savepoint(flush=False):
                    self.bind(name, 103, model=model)
                    with self.assertRaisesRegex(RuntimeError, "Mail seed"):
                        self.migrate()
                    self.assertEqual(self.bindings(), [(name, model, 103, True)])
                    self.assert_preserved()
                    self.cr.execute("ROLLBACK TO SAVEPOINT qa_mail_seed")

    def test_ambiguous_native_anchor_refuses_without_adoption(self):
        self.cr.execute("ALTER TABLE ir_model_data DROP CONSTRAINT ir_model_data_module_name_key")
        self.cr.execute("INSERT INTO ir_model_data(module,name,model,res_id,noupdate) VALUES ('base','partner_admin','res.partner',2,true)")
        with self.assertRaisesRegex(RuntimeError, "Mail seed"):
            self.migrate()
        self.assertEqual(self.bindings(), [])
        self.assert_preserved()

    def test_fresh_database_without_mail_tables_leaves_native_install_untouched(self):
        self.cr.execute("DROP TABLE discuss_channel_member")
        self.migrate()
        self.assertEqual(self.bindings(), [])

    def other_connection(self):
        connection = psycopg2.connect(DSN)
        connection.set_session(isolation_level="REPEATABLE READ")
        with connection.cursor() as cr:
            cr.execute("SHOW transaction_isolation")
            self.assertEqual(cr.fetchone()[0], "repeatable read")
            cr.execute('SET search_path TO "' + self.schema + '"')
            cr.execute("SET statement_timeout='5s'")
        connection.commit()
        return connection

    def test_native_transaction_isolation_is_repeatable_read(self):
        self.cr.execute("SHOW transaction_isolation")
        self.assertEqual(self.cr.fetchone()[0], "repeatable read")

    def test_two_transactions_serialize_then_retry_the_whole_transaction(self):
        self.connection.commit()
        self.migrate()
        other = self.other_connection()
        done = threading.Event()
        errors = []

        def second():
            try:
                self.migrate(Cursor(other))
                other.commit()
            except BaseException as error:
                errors.append(error)
                other.rollback()
            finally:
                done.set()

        thread = threading.Thread(target=second)
        thread.start()
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                with self.connection.cursor() as cr:
                    cr.execute("SELECT cardinality(pg_blocking_pids(%s))", (other.get_backend_pid(),))
                    if cr.fetchone()[0]:
                        break
                time.sleep(.01)
            else:
                self.fail("Second hook did not wait for the first transaction")
            self.assertFalse(done.is_set())
            self.connection.commit()
            self.assertTrue(done.wait(5))
            self.assertTrue(all(isinstance(error, psycopg2.errors.SerializationFailure) for error in errors), errors)
            # A blocked Repeatable Read transaction can retain an old snapshot.
            # Only a serialization refusal is safe here; retry the whole unit.
            other.rollback()
            self.migrate(Cursor(other))
            other.commit()
            self.connection.rollback()
            self.assertEqual(self.bindings(), [(ADMIN, "discuss.channel.member", 102, True), (GENERAL, "discuss.channel.member", 101, True)])
            self.assert_preserved()
        finally:
            self.connection.rollback()
            thread.join(6)
            other.close()

    def test_concurrent_conflicting_second_insert_rolls_back_first_adoption(self):
        self.connection.commit()
        other = self.other_connection()
        fired = False
        first_adoption_seen = False

        def conflict(sql, params):
            nonlocal fired, first_adoption_seen
            if fired or not sql.lstrip().upper().startswith("INSERT INTO IR_MODEL_DATA"):
                return
            if ADMIN not in params:
                return
            self.cr.execute("SELECT res_id FROM ir_model_data WHERE module='mail' AND name=%s", (GENERAL,))
            self.assertEqual(self.cr.fetchone(), (101,))
            first_adoption_seen = True
            fired = True
            with other.cursor() as cursor:
                cursor.execute("INSERT INTO ir_model_data(module,name,model,res_id,noupdate) VALUES ('mail',%s,'res.partner',1,true)", (ADMIN,))
            other.commit()

        try:
            with self.assertRaises((RuntimeError, psycopg2.errors.SerializationFailure)) as refused:
                self.migrate(Cursor(self.connection, conflict))
            if isinstance(refused.exception, psycopg2.errors.SerializationFailure):
                self.assertEqual(refused.exception.pgcode, "40001")
            else:
                self.assertIn("Mail seed", str(refused.exception))
            self.assertTrue(fired and first_adoption_seen)
            # Even if the caller catches the error, the savepoint removed the
            # earlier adoption. The external commit is outside this snapshot.
            self.assertEqual(self.bindings(), [])
            self.connection.rollback()
            self.assertEqual(self.bindings(), [(ADMIN, "res.partner", 1, True)])
            self.assert_preserved()
            # A whole-transaction retry now sees the live wrong-model conflict:
            # it must refuse, preserve that actor's row and adopt nothing else.
            with self.assertRaisesRegex(RuntimeError, "Mail seed"):
                self.migrate()
            self.assertEqual(self.bindings(), [(ADMIN, "res.partner", 1, True)])
            self.assert_preserved()
        finally:
            other.close()


    def test_concurrent_correct_insert_recovers_only_after_whole_transaction_retry(self):
        self.connection.commit()
        other = self.other_connection()
        first_adoption_seen = False

        def insert_second(sql, params):
            nonlocal first_adoption_seen
            if not sql.lstrip().upper().startswith("INSERT INTO IR_MODEL_DATA") or ADMIN not in params:
                return
            self.cr.execute("SELECT res_id FROM ir_model_data WHERE module='mail' AND name=%s", (GENERAL,))
            self.assertEqual(self.cr.fetchone(), (101,))
            first_adoption_seen = True
            with other.cursor() as cursor:
                cursor.execute("INSERT INTO ir_model_data(module,name,model,res_id,noupdate) VALUES ('mail',%s,'discuss.channel.member',102,true)", (ADMIN,))
            other.commit()

        try:
            with self.assertRaises(psycopg2.errors.SerializationFailure) as refused:
                self.migrate(Cursor(self.connection, insert_second))
            self.assertEqual(refused.exception.pgcode, "40001")
            self.assertTrue(first_adoption_seen)
            self.assertEqual(self.bindings(), [])
            self.connection.rollback()
            self.assertEqual(self.bindings(), [(ADMIN, "discuss.channel.member", 102, True)])
            self.migrate()
            self.connection.commit()
            self.assertEqual(self.bindings(), [(ADMIN, "discuss.channel.member", 102, True), (GENERAL, "discuss.channel.member", 101, True)])
            self.assert_preserved()
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()
