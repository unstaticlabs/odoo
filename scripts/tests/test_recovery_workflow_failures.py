"""An isolated restore preserves failures; it must not invent or replace them."""
import copy
import hashlib
import itertools
import unittest
from operations import control_manifest as controls
from scripts.tests.test_control_manifest import controls_v2


def fingerprint(seed):
    return {"schema": "usl-recovery-workflow-failures/v1", "roles": {
        role: {"count": int(role == "failed_documents"), "sha256": hashlib.sha256(
            (seed if role == "failed_documents" else "empty").encode()).hexdigest()}
        for role in controls.FAILED_QUEUE_KEYS
    }}


def compare(before, after, source, restored):
    method = getattr(controls, "validate_recovery_restore", None)
    if method is None:  # Exercise the original observable rejection before the fix.
        return controls.validate_restore(before, after, require_unchanged_release=True)
    return method(before, after, source_failures=source, restored_failures=restored)


class RecoveryWorkflowFailureTests(unittest.TestCase):
    def test_existing_failure_survives_a_usable_restore_without_clearing_business_work(self):
        before = controls_v2(); before["odoo"]["failed_documents"] = 1
        original = copy.deepcopy(before)
        evidence = compare(before, copy.deepcopy(before), fingerprint("invented-row-1:error"), fingerprint("invented-row-1:error"))
        self.assertEqual(before, original)
        self.assertEqual(evidence["workflow_failures"]["roles"]["failed_documents"]["count"], 1)
        with self.assertRaises(controls.ControlManifestError):
            controls.validate_restore(before, before, require_unchanged_release=True)

    def test_same_count_replacement_or_changed_error_is_not_a_faithful_restore(self):
        before = controls_v2(); before["odoo"]["failed_documents"] = 1
        for changed in ("invented-row-2:error", "invented-row-1:different-error"):
            with self.subTest(changed=changed), self.assertRaises(controls.ControlManifestError):
                compare(before, before, fingerprint("invented-row-1:error"), fingerprint(changed))

    def test_new_failures_and_business_changes_still_fail(self):
        before = controls_v2(); before["odoo"]["failed_documents"] = 1
        for key in ("failed_documents", "messages"):
            after = copy.deepcopy(before); after["odoo"][key] += 1
            with self.subTest(key=key), self.assertRaises(controls.ControlManifestError):
                compare(before, after, fingerprint("invented-row-1:error"), fingerprint("invented-row-1:error"))

    def test_each_failed_role_requires_typed_evidence_and_order_is_irrelevant(self):
        before = controls_v2()
        source = fingerprint("invented-row-1:error")
        before["odoo"]["failed_documents"] = 1
        roles = list(controls.FAILED_QUEUE_KEYS)
        for order in (roles, list(reversed(roles))):
            reordered = {"schema": source["schema"], "roles": {key: source["roles"][key] for key in order}}
            compare(before, before, source, reordered)
        for role, bad in itertools.product(roles, (None, {"count": True, "sha256": "a" * 64}, {"count": 0, "sha256": "invalid"})):
            broken = copy.deepcopy(source); broken["roles"][role] = bad
            with self.subTest(role=role, bad=bad), self.assertRaises(controls.ControlManifestError):
                compare(before, before, source, broken)


    def test_cron_quarantine_can_disable_activation_but_not_rewrite_failure_evidence(self):
        before = controls_v2(); before["odoo"]["cron_failures"] = 3
        after = copy.deepcopy(before); after["odoo"]["cron_failures"] = 0
        failures = fingerprint("unused"); failures["roles"]["failed_documents"]["count"] = 0
        failures["roles"]["cron_failures"]["count"] = 1
        compare(before, after, failures, failures)
        changed = copy.deepcopy(failures); changed["roles"]["cron_failures"]["sha256"] = "f" * 64
        with self.assertRaises(controls.ControlManifestError):
            compare(before, after, failures, changed)
        cron_sql = controls.workflow_failure_sql().split("FROM ir_cron WHERE ", 1)[1].split(")", 1)[0]
        self.assertEqual(cron_sql, "failure_count > 0")


    def test_failure_created_during_health_journey_cannot_receive_a_passed_proof(self):
        from unittest import mock
        from operations.stack import _validate_recovered_services
        before = controls_v2(); before["odoo"]["failed_documents"] = 1
        original = fingerprint("invented-row-1:error")
        # The initial restored control boundary passes before services run.
        compare(before, before, original, original)
        live = {"failures": original}
        def health(*args, **kwargs):
            # A later startup/health journey replaces the failure, same count.
            live["failures"] = fingerprint("invented-row-2:late-error")
            return {"status": "passed"}, {"status": "passed"}
        with mock.patch("operations.stack._recovery_proof_runtime_health", side_effect=health), \
                mock.patch("operations.stack._recovery_proof_durable_state", return_value=({}, {})), \
                mock.patch("operations.stack._recovery_proof_query", side_effect=lambda *a: live["failures"]):
            with self.assertRaisesRegex(RuntimeError, "service journeys changed"):
                _validate_recovered_services(None, None, {"containers": {"odoo_db": "invented-db"}},
                    {}, {}, "/invented-proof", {}, original, started=0, deadline_at="fixture")


@unittest.skipUnless(__import__('shutil').which('initdb') and __import__('os').geteuid() != 0,
                     'PostgreSQL server tools and a non-root disposable test user required')
class PostgreSQLWorkflowFailureTests(unittest.TestCase):
    def test_real_sql_preserves_cron_activation_and_detects_replacement_error_and_new_failure(self):
        import json
        import subprocess
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(prefix='workflow-2374-') as temporary:
            root=Path(temporary);data=root/'data';socket=root/'socket';socket.mkdir()
            subprocess.run(['initdb','-D',str(data),'--auth=trust','--username=recovery_fixture_user',
                            '--no-instructions','--no-locale','--encoding=UTF8'],capture_output=True,text=True,check=True)
            subprocess.run(['pg_ctl','-D',str(data),'-l',str(root/'server.log'),'-o',
                f"-c listen_addresses='' -c unix_socket_directories='{socket}' -c shared_buffers=16MB -c max_connections=10",
                '--wait','--timeout=30','start'],capture_output=True,text=True,check=True)
            def sql(statement):
                p=subprocess.run(['psql','-h',str(socket),'-U','recovery_fixture_user','-d','postgres',
                    '-Atq','-v','ON_ERROR_STOP=1'],input=statement,capture_output=True,text=True,check=True)
                return p.stdout
            try:
                sql("""CREATE TABLE mail_mail(id bigint,state text,failure_type text,failure_reason text);
CREATE TABLE usl_document_operation(id bigint,state text,error_message text);
CREATE TABLE account_bank_ingestion(id bigint,state text,last_error text);
CREATE TABLE payment_transaction(id bigint,state text,state_message text);
CREATE TABLE sign_oca_request(id bigint,archive_status text,archive_last_error text);
CREATE TABLE ir_cron(id bigint,active boolean,failure_count int,first_failure_date timestamp);
INSERT INTO usl_document_operation VALUES(1,'failed','Invented invalid file');
INSERT INTO ir_cron VALUES(1,true,3,'2026-01-01');""")
                def observe():return controls.validate_workflow_failures(json.loads(sql(controls.workflow_failure_sql())))
                original=observe()
                sql('UPDATE ir_cron SET active=false;');self.assertEqual(observe(),original)
                sql("UPDATE usl_document_operation SET error_message='Invented changed error';")
                self.assertNotEqual(observe()['roles']['failed_documents']['sha256'],original['roles']['failed_documents']['sha256'])
                sql("UPDATE usl_document_operation SET error_message='Invented invalid file',id=2;")
                replacement=observe();self.assertEqual(replacement['roles']['failed_documents']['count'],1)
                self.assertNotEqual(replacement['roles']['failed_documents']['sha256'],original['roles']['failed_documents']['sha256'])
                sql("INSERT INTO usl_document_operation VALUES(3,'failed',NULL);")
                self.assertEqual(observe()['roles']['failed_documents']['count'],2)
            finally:
                subprocess.run(['pg_ctl','-D',str(data),'--wait','--timeout=15','-m','fast','stop'],capture_output=True,text=True,check=True)
                self.assertFalse((data/'postmaster.pid').exists())
