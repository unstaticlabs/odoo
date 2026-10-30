"""Production plans bind its own candidate without depending on staging."""
import copy
import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

from operations.module_release import ModuleReleaseError
from operations.stack import (
    _validated_release_upgrade_plan,
    _run_candidate_upgrade,
    release_command,
    build_parser,
)
from scripts.tests.test_plan_evidence import plan
from scripts.tests.test_module_release import inventory


class ProductionUpgradePlanTests(unittest.TestCase):
    def test_production_accepts_its_own_plan_without_staging_keys(self):
        target = SimpleNamespace(value={"environment": "production"})
        value = plan()
        self.assertEqual(
            _validated_release_upgrade_plan(target, value, {"identity": value["candidate_release"]}),
            value,
        )

    def test_wrong_candidate_and_modified_plan_are_rejected(self):
        target = SimpleNamespace(value={"environment": "production"})
        value = plan()
        with self.assertRaisesRegex(ModuleReleaseError, "another candidate"):
            _validated_release_upgrade_plan(target, value, {"identity": "d" * 64})
        changed = copy.deepcopy(value)
        changed["upgrade_modules"] = []
        with self.assertRaisesRegex(ModuleReleaseError, "digest differs"):
            _validated_release_upgrade_plan(target, changed, {"identity": value["candidate_release"]})


class ReleasePlanCommandTests(unittest.TestCase):
    def test_release_plan_has_no_promotion_flags(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["release", "plan", "--promote"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["release", "plan", "--staging-release", "release.json"])
        arguments = parser.parse_args(["release", "plan", "--attest"])
        self.assertTrue(arguments.attest)
        self.assertFalse(hasattr(arguments, "promote"))


class InstalledFoundationPlanTests(unittest.TestCase):
    def _release(self, identity, *, core="a", new_module=False):
        modules = [
            ("usl_product", "1.0", ["base"], "a", "b"),
            ("usl_optional", "1.0", ["iap"], "c", "d"),
        ]
        if new_module:
            modules.append(("usl_new", "1.0", ["iap"], "e", "f"))
        return {
            "schema": "usl-release/v3", "identity": identity * 64,
            "modules": inventory(modules),
            "foundation": {
                "digest": core * 64, "odoo_core_sha256": core * 64,
                "oca_sha256": "b" * 64,
            },
            "components": {"distribution": {"digest_reference": "odoo@sha256:" + "c" * 64}},
        }

    def _generate_plan(self, *, legacy=False, managed=None, new_module=False):
        active = self._release("a")
        if legacy:
            active["schema"] = "usl-release/v2"
        candidate = self._release("b", core="e", new_module=new_module)
        managed = managed if managed is not None else {"usl_product"}
        actual = managed | {"base", "iap", "account_reconcile_oca"}
        target = mock.Mock(value={"environment": "staging", "state_directory": "/private/qa"})
        arguments = SimpleNamespace(
            target="staging", targets=Path("/private/qa/targets"), action="plan",
            attest=False, active_release=Path("/private/qa/active.json"),
            candidate_release=Path("/private/qa/candidate.json"),
            output=Path("/private/qa/plan.json"), json=True,
        )
        def query(_target, _runner, _compose, database, sql):
            self.assertEqual(database, "odoo")
            self.assertIn("state = 'installed'", sql)
            return json.dumps(sorted(managed if "name LIKE 'usl_%'" in sql else actual))

        output = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            mock.patch("operations.stack.load_target", return_value=target),
            mock.patch("operations.stack._release", return_value=(active, "unused", json.dumps(active))),
            mock.patch("operations.stack._read_path", return_value=json.dumps(candidate)),
            mock.patch("operations.stack.validate_release", return_value=candidate),
            mock.patch("operations.stack.inspect_runtime", return_value={"compose": {"project": "qa"}}),
            mock.patch("operations.stack._psql", side_effect=query) as queries,
            mock.patch("operations.stack._write_remote") as write,
        ):
            self.assertEqual(release_command(arguments), 0)
        value = json.loads(output.getvalue())
        written = json.loads(write.call_args.args[3])
        self.assertEqual({k: v for k, v in value.items() if k != "path"}, written)
        self.assertEqual(queries.call_count, 2)
        return written, candidate

    def test_controller_plans_actual_native_and_oca_modules_for_core_change(self):
        value, _candidate = self._generate_plan()
        expected = ["account_reconcile_oca", "base", "iap", "usl_product"]
        self.assertEqual(value["installed_modules"], expected)
        self.assertEqual(value["upgrade_modules"], expected)
        self.assertNotIn("usl_optional", value["upgrade_modules"])

    def test_legacy_controller_also_carries_actual_installed_native_modules(self):
        value, _candidate = self._generate_plan(legacy=True)
        expected = ["account_reconcile_oca", "base", "iap", "usl_product"]
        self.assertEqual(value["installed_modules"], expected)
        self.assertEqual(value["upgrade_modules"], expected)

    def test_candidate_command_retains_exact_native_update_and_only_new_init(self):
        value, candidate = self._generate_plan(new_module=True)
        target = SimpleNamespace(value={
            "environment": "production",
            "databases": {"odoo": {"name": "qa_restore", "service": "db", "user": "odoo"}},
            "secrets": {"env_file": "/private/qa/env"},
        })
        runner = mock.Mock()
        _run_candidate_upgrade(
            target, runner, candidate, "qa-internal",
            {"odoo_filestore": "qa-filestore"}, value,
        )
        command = runner.run.call_args.args[0]
        self.assertIn("--update=account_reconcile_oca,base,iap,usl_new,usl_product", command)
        self.assertIn("--init=usl_new", command)
        self.assertNotIn("--update=all", command)
        self.assertNotIn("usl_optional", ",".join(command))

    def test_controller_still_rejects_unknown_managed_module(self):
        with self.assertRaisesRegex(RuntimeError, "ambiguous: usl_foreign"):
            self._generate_plan(managed={"usl_foreign"})
