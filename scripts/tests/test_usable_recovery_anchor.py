"""Calendar selection must not evict the last proven usable recovery point."""
import copy
import inspect
import json
import unittest
from datetime import UTC,datetime,timedelta
from unittest import mock
from operations import cohort


def anchor(**updates):
    value = {"schema":"usl-usable-recovery-anchor/v1","proof_id":"daily-"+"a"*16,
            "run_id":"proof-daily-"+"a"*16,"proof_receipt_sha256":"c"*64,
            "backup_receipt_sha256":"d"*64,"durable_snapshot_id":"a"*64,"cache_snapshot_id":"b"*64}
    return {**value, **updates}


def inventory():
    now=datetime(2026,12,4,12,tzinfo=UTC); durable=[];cache=[]
    for index in range(23):
        run=anchor()["run_id"] if index==0 else "proof-daily-"+f"{index:016x}"
        stamp=datetime(2026,11,12,8,tzinfo=UTC)+timedelta(days=index)
        for kind,items,identity in (("durable",durable,"a"*64 if not index else f"{index:064x}"),("cache",cache,"b"*64 if not index else f"{index+100:064x}")):
            tags=["usl-cohort",kind,"target-production","run-"+run,"release-"+"e"*64]
            if kind=="durable":tags.append("recovery-eligible")
            items.append({"id":identity,"time":stamp.isoformat(),"tags":tags})
    return now,durable,cache


def plan(now,durable,cache,protected):
    with mock.patch.object(cohort,"restic_environment",return_value={}),mock.patch.object(cohort,"_inventory",side_effect=[durable,cache]),mock.patch.dict(cohort.os.environ,{"USL_TARGET":"production"}):
        kwargs={"protected_anchor":protected} if "protected_anchor" in inspect.signature(cohort.plan_retention).parameters else {}
        return cohort.plan_retention(now,**kwargs)


class UsableRecoveryAnchorTests(unittest.TestCase):
    def test_failed_full_proofs_across_calendar_buckets_keep_last_passed_durable_and_cache(self):
        now,durable,cache=inventory();result=plan(now,durable,cache,anchor())
        self.assertIn("a"*64,result["retain_durable"])
        self.assertIn("b"*64,result["retain_cache"])
        self.assertNotIn("a"*64,result["delete_durable"])

    def test_missing_malformed_and_missing_remote_anchors_refuse_gc(self):
        now,durable,cache=inventory()
        for invalid in (None,{},dict(anchor(),cache_snapshot_id="f"*64),dict(anchor(),run_id="proof-daily-"+"f"*16)):
            with self.subTest(invalid=invalid),self.assertRaises(cohort.CohortError):
                plan(now,durable,cache,invalid)
        with self.assertRaises(cohort.CohortError):plan(now,durable[1:],cache,anchor())
        with self.assertRaises(cohort.CohortError):plan(now,durable,cache[1:],anchor())


class PassedProofAuthorityTests(unittest.TestCase):
    def fixture(self):
        import subprocess
        from scripts.tests.test_cohort import backup_receipt
        from scripts.tests.test_recovery_proof import completion_receipt,PROOF_ID,TARGETS
        from operations.runtime import load_target
        from operations.stack import _digested_document,_backup_run_receipt
        target=load_target("production",TARGETS)
        backup=backup_receipt(target="production",run_id="proof-"+PROOF_ID)
        backup.update(writers_quiesced=False,writer_interval_complete=True,quiescence=None,
                      writers_resumed_at="2026-09-04T00:00:01Z")
        backup["performance"].update(writer_freeze_seconds=1,writer_freeze_sla_passed=True)
        backup.pop("sha256");backup=_digested_document(backup)
        _backup_run_receipt(backup,target="production")
        proof=completion_receipt();proof.pop("sha256")
        proof["backup"].update(receipt_sha256=backup["sha256"],
            durable_snapshot_id=backup["qualification"]["durable_snapshot_id"],
            cache_snapshot_id=backup["qualification"]["cache_snapshot_id"])
        proof["release"]={"identity":backup["capture"]["release"]["identity"],
                           "manifest_sha256":backup["capture"]["release"]["manifest_sha256"]}
        proof=_digested_document(proof)
        root=target.value["state_directory"]
        files={root+"/recovery-anchor.json":proof,
               root+"/backup-runs/"+backup["run_id"]+"/receipt.json":backup}
        class Runner:
            def run(self,command,**kwargs):
                if command[:2]==["test","-L"]:return subprocess.CompletedProcess(command,1,"","")
                if command[:2]==["test","-e"]:return subprocess.CompletedProcess(command,0 if command[2] in files else 1,"","")
                if command[0]=="python3":return subprocess.CompletedProcess(command,0,json.dumps(files.get(command[-1])),"")
                if command[0]=="find":return subprocess.CompletedProcess(command,0,"\n".join(p for p in files if "/recovery-proofs/" in p),"")
                raise AssertionError(command)
        return target,Runner(),files,proof,backup

    def test_anchor_requires_full_pass_and_exact_backing_receipt_not_just_qualified_snapshot(self):
        from operations.stack import _usable_recovery_anchor,_digested_document
        target,runner,files,proof,backup=self.fixture()
        result=_usable_recovery_anchor(target,runner)
        self.assertEqual(result["durable_snapshot_id"],backup["qualification"]["durable_snapshot_id"])
        pointer=target.value["state_directory"]+"/recovery-anchor.json"
        for mutation in ("digest","failed","health","cache","missing_backup"):
            with self.subTest(mutation=mutation):
                saved=copy.deepcopy(files)
                try:
                    changed=copy.deepcopy(proof);changed.pop("sha256")
                    if mutation=="failed":changed["status"]="failed"
                    if mutation=="health":changed["health"]["checks"]["odoo_http"]=False
                    if mutation=="cache":changed["backup"]["cache_snapshot_id"]="f"*64
                    files[pointer]=_digested_document(changed)
                    if mutation=="digest":files[pointer]["sha256"]="0"*64
                    if mutation=="missing_backup":files.pop(target.value["state_directory"]+"/backup-runs/"+backup["run_id"]+"/receipt.json")
                    with self.assertRaises(RuntimeError):_usable_recovery_anchor(target,runner)
                finally:files.clear();files.update(saved)

    def test_legacy_v2_pass_is_adopted_read_only_and_failed_new_proof_does_not_advance_it(self):
        from operations.stack import _usable_recovery_anchor
        from pathlib import Path
        target,runner,files,proof,backup=self.fixture()
        files.pop(target.value["state_directory"]+"/recovery-anchor.json")
        root=str(Path(target.value["state_directory"]).parents[1])+"/recovery-proofs"
        files[root+"/"+proof["proof_id"]+"/receipt.json"]=proof
        # A failed later proof has no passed receipt; its state is not authority.
        result=_usable_recovery_anchor(target,runner)
        self.assertEqual(result["proof_id"],proof["proof_id"])
        files.pop(root+"/"+proof["proof_id"]+"/receipt.json")
        with self.assertRaisesRegex(RuntimeError,"no full usable-service"):_usable_recovery_anchor(target,runner)

    def test_local_capture_retention_keeps_anchor_beyond_two_newer_failed_proofs(self):
        from operations.stack import _select_proof_captures,_validated_cleanup_captures
        from scripts.tests.test_recovery_proof import TARGETS
        from operations.runtime import load_target
        target=load_target("production",TARGETS)
        runs=["proof-daily-"+digit*16 for digit in "abcd"]
        created={run:datetime(2026,11,12+i,tzinfo=UTC) for i,run in enumerate(runs)}
        with mock.patch("operations.stack._qualified_capture",side_effect=lambda t,r,n:{"created_at":created[n]}),mock.patch("operations.stack._usable_recovery_anchor",return_value={"run_id":runs[0]}):
            result=_select_proof_captures(target,mock.Mock(),runs)
            self.assertEqual(result["delete_captures"],[runs[1]])
            with self.assertRaisesRegex(RuntimeError,"last full usable"):
                _validated_cleanup_captures(target,mock.Mock(),{"delete_captures":[runs[0]]})
        with mock.patch("operations.stack._qualified_capture",side_effect=lambda t,r,n:{"created_at":created[n]}),mock.patch("operations.stack._usable_recovery_anchor",side_effect=RuntimeError("anchor missing")):
            with self.assertRaisesRegex(RuntimeError, "anchor missing"):
                _select_proof_captures(target,mock.Mock(),runs)


class AnchorInputBoundaryTests(unittest.TestCase):
    def test_missing_authority_never_contacts_or_mutates_a_repository(self):
        with mock.patch.object(cohort,"restic_environment") as environment, mock.patch.dict(cohort.os.environ,{"USL_TARGET":"production"}):
            with self.assertRaises(cohort.CohortError):cohort.apply_retention()
        environment.assert_not_called()

    def test_anchor_metadata_reader_refuses_symlinks_corruption_and_oversized_files(self):
        import tempfile
        from pathlib import Path
        from operations.runtime import CommandRunner
        from operations.stack import _read_usable_anchor_document
        runner=CommandRunner(filesystem_scope="namespace")
        with tempfile.TemporaryDirectory() as temporary:
            p=Path(temporary)/"receipt.json"
            self.assertIsNone(_read_usable_anchor_document(runner,str(p)))
            p.write_text('{"invented":"metadata"}')
            self.assertEqual(_read_usable_anchor_document(runner,str(p)),{"invented":"metadata"})
            link=Path(temporary)/"alias.json";link.symlink_to(p)
            with self.assertRaises(RuntimeError):_read_usable_anchor_document(runner,str(link))
            for content in ("invalid", "[1]", "x"*(1024*1024+1)):
                p.write_text(content)
                with self.subTest(length=len(content)),self.assertRaises(RuntimeError):
                    _read_usable_anchor_document(runner,str(p))


@unittest.skipUnless(__import__('shutil').which('restic'), 'actual Restic executable required')
class ResticAnchorRoundtripTests(unittest.TestCase):
    def test_restored_anchor_survives_native_retention_and_restores_again(self):
        import hashlib
        import os
        import subprocess
        import tempfile
        from pathlib import Path
        password='invented-offline-2374-password'
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);payload=root/'payload';payload.mkdir()
            durable_env={**os.environ,'RESTIC_REPOSITORY':str(root/'durable-repo'),'RESTIC_PASSWORD':password,'TZ':'UTC'}
            cache_env={**durable_env,'RESTIC_REPOSITORY':str(root/'cache-repo')}
            for env in (durable_env,cache_env):
                subprocess.run(['restic','init'],env=env,capture_output=True,text=True,check=True)
            identities=[]
            for index in range(3):
                run='proof-daily-'+f'{index+1:016x}'
                (payload/'invented.txt').write_text('invented recovery content '+str(index))
                pair={}
                for kind,env in (('durable',durable_env),('cache',cache_env)):
                    args=['restic','backup',str(payload),'--json','--host','synthetic-2374','--time',f'2026-09-01 0{index+1}:00:00']
                    tags=['usl-cohort',kind,'target-production','run-'+run,'release-'+str(index+1)*64]
                    if kind=='durable':tags.append('recovery-eligible')
                    for tag in tags:args.extend(['--tag',tag])
                    p=subprocess.run(args,env=env,capture_output=True,text=True,check=True)
                    summary=[json.loads(line) for line in p.stdout.splitlines() if json.loads(line).get('message_type')=='summary'][-1]
                    pair[kind]=summary['snapshot_id']
                identities.append(pair)
            protected=anchor(proof_id='daily-'+f'{1:016x}',run_id='proof-daily-'+f'{1:016x}',
                             durable_snapshot_id=identities[0]['durable'],cache_snapshot_id=identities[0]['cache'])
            def restore_pair(label):
                recovered={}
                for kind,env in (('durable',durable_env),('cache',cache_env)):
                    dest=root/(label+'-'+kind)
                    subprocess.run(['restic','restore',identities[0][kind],'--target',str(dest)],env=env,capture_output=True,text=True,check=True)
                    files=list(dest.rglob('invented.txt'));self.assertEqual(len(files),1)
                    recovered[kind]=hashlib.sha256(files[0].read_bytes()).hexdigest()
                    self.assertEqual(files[0].read_text(),'invented recovery content 0')
                return recovered
            before=restore_pair('before')
            with mock.patch.dict(os.environ,{'RESTIC_REPOSITORY':durable_env['RESTIC_REPOSITORY'],
                    'RESTIC_PASSWORD':password,'USL_BACKUP_CACHE_REPOSITORY':cache_env['RESTIC_REPOSITORY'],
                    'USL_BACKUP_CACHE_PASSWORD':password,'USL_TARGET':'production'}):
                result=cohort.apply_retention(datetime(2026,12,4,tzinfo=UTC),protected_anchor=protected)
            self.assertIn(identities[0]['durable'],result['retain_durable'])
            self.assertIn(identities[0]['cache'],result['retain_cache'])
            self.assertIn(identities[1]['durable'],result['delete_durable'])
            self.assertEqual(restore_pair('after'),before)
