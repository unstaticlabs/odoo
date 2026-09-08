from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
ODOO_DEV = ROOT / "scripts" / "odoo-dev"
COMPOSE_SCOPE = ROOT / "scripts" / "lib" / "compose-scope.sh"
POCKET_ID_DEV = ROOT / "scripts" / "pocket-id-dev"
# The three spellings the Makefile and scripts/odoo-dev both accept.
PROJECT_VARIABLES = (
    "COMPOSE_PROJECT",
    "COMPOSE_PROJECT_NAME",
    "ODOO_SAAS_COMPOSE_PROJECT",
)


class DeveloperCommandUXTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.temporary = Path(self.temporary_directory.name)
        self.docker_log = self.temporary / "docker.log"
        self.docker_state = self.temporary / "docker.state"
        docker = self.temporary / "docker"
        docker.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$USL_FAKE_DOCKER_LOG"
case "${1:-}" in
  ps)
    if [[ -n "${USL_FAKE_DOCKER_STATE:-}" ]]; then
      cat "$USL_FAKE_DOCKER_STATE"
    else
      printf '%s' "${USL_FAKE_DOCKER_ROWS:-}"
    fi
    ;;
  volume)
    if [[ "${2:-}" == "ls" ]]; then
      printf '%s' "${USL_FAKE_DOCKER_VOLUMES:-}"
    else
      exit 91
    fi
    ;;
  rm)
    shift
    [[ "${1:-}" == "--force" ]] && shift
    printf '%s\\n' "$@"
    [[ -z "${USL_FAKE_DOCKER_STATE:-}" ]] || : > "$USL_FAKE_DOCKER_STATE"
    ;;
  exec)
    printf '%s' "${USL_FAKE_DATABASE_QUERY-1}"
    ;;
  *)
    exit 92
    ;;
esac
""",
            encoding="utf-8",
        )
        docker.chmod(0o755)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def environment(self, rows="", **values):
        self.docker_state.write_text(rows, encoding="utf-8")
        return {
            **os.environ,
            "PATH": f"{self.temporary}:{os.environ['PATH']}",
            "USL_FAKE_DOCKER_LOG": str(self.docker_log),
            "USL_FAKE_DOCKER_ROWS": rows,
            "USL_FAKE_DOCKER_STATE": str(self.docker_state),
            "USL_FAKE_DOCKER_VOLUMES": "test-project_db\n",
            **values,
        }

    @staticmethod
    def row(identifier, name, state, owner, service, status="", oneoff=""):
        return (
            f"{identifier}|{name}|{state}|{owner}|{service}|"
            f"{status or state}|{oneoff}\n"
        )

    def run_dev(self, command, rows="", **environment):
        return subprocess.run(
            [str(ODOO_DEV), command],
            cwd=ROOT,
            env=self.environment(
                rows,
                COMPOSE_PROJECT_NAME="test-project",
                ODOO_SAAS_COMPOSE_PROJECT="test-project",
                ODOO_DEV_DB="odoo_test_ux",
                **environment,
            ),
            check=False,
            capture_output=True,
            text=True,
        )

    def run_scope(self, command, rows="", *arguments):
        return subprocess.run(
            ["bash", "-c", f'source "$1"; {command}', "test", str(COMPOSE_SCOPE), *arguments],
            cwd=ROOT,
            env=self.environment(rows),
            check=False,
            capture_output=True,
            text=True,
        )

    def test_doctor_classifies_unused_owned_foreign_and_mixed_projects(self):
        foreign = self.temporary / "foreign"
        foreign.mkdir()
        cases = {
            "unused": "",
            "owned": self.row("one", "odoo", "running", str(ROOT), "odoo"),
            "foreign": self.row(
                "one",
                "odoo",
                "running",
                str(foreign),
                "odoo",
            ),
            "mixed": (
                self.row("one", "odoo", "running", str(ROOT), "odoo")
                + self.row("two", "db", "running", str(foreign), "db")
            ),
        }
        for expected, rows in cases.items():
            with self.subTest(expected=expected):
                completed = self.run_dev("doctor", rows)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn(f"Ownership: {expected}", completed.stdout)

    def test_doctor_is_read_only_and_reports_branch_owners(self):
        rows = self.row("one", "db", "running", str(ROOT), "db")

        completed = self.run_dev("doctor", rows)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Environment doctor", completed.stdout)
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertIn(branch, completed.stdout)
        docker_calls = self.docker_log.read_text(encoding="utf-8")
        self.assertIn("ps -a", docker_calls)
        self.assertIn("volume ls", docker_calls)
        self.assertIn("exec one psql", docker_calls)
        self.assertNotIn("rm --force", docker_calls)
        self.assertNotIn("compose up", docker_calls)
        self.assertNotIn("compose down", docker_calls)

    def test_cli_port_defaults_match_compose_env_without_overriding_shell(self):
        repository = self.temporary / "checkout"
        repository.mkdir()
        (repository / ".env").write_text(
            "ODOO_HTTP_PORT=18069\nODOO_GEVENT_PORT=18072\n",
            encoding="utf-8",
        )
        (repository / ".pocket-id.env").write_text(
            "POCKET_ID_HTTP_PORT=11411\nPAPERLESS_HTTP_PORT=18010\n",
            encoding="utf-8",
        )
        command = (
            'source "$1"; '
            'usl_cli_load_local_port_defaults "$2"; '
            "printf '%s|%s|%s|%s' \"$ODOO_HTTP_PORT\" \"$ODOO_GEVENT_PORT\" "
            '"$POCKET_ID_HTTP_PORT" "$PAPERLESS_HTTP_PORT"'
        )

        completed = subprocess.run(
            [
                "bash",
                "-c",
                command,
                "test",
                str(ROOT / "scripts/lib/cli-ui.sh"),
                str(repository),
            ],
            cwd=ROOT,
            env={**os.environ, "ODOO_HTTP_PORT": "28069"},
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "28069|18072|11411|18010")

    def test_cli_port_defaults_accept_project_bound_environment(self):
        repository = self.temporary / "checkout"
        repository.mkdir()
        project_environment = self.temporary / ".pocket-id-project.env"
        project_environment.write_text(
            "ODOO_HTTP_PORT=28069\n"
            "ODOO_GEVENT_PORT=28072\n"
            "POCKET_ID_HTTP_PORT=21411\n"
            "PAPERLESS_HTTP_PORT=28010\n",
            encoding="utf-8",
        )
        command = (
            'source "$1"; '
            'usl_cli_load_local_port_defaults "$2" "$3"; '
            "printf '%s|%s|%s|%s' \"$ODOO_HTTP_PORT\" \"$ODOO_GEVENT_PORT\" "
            '"$POCKET_ID_HTTP_PORT" "$PAPERLESS_HTTP_PORT"'
        )

        completed = subprocess.run(
            [
                "bash",
                "-c",
                command,
                "test",
                str(ROOT / "scripts/lib/cli-ui.sh"),
                str(repository),
                str(project_environment),
            ],
            cwd=ROOT,
            env=os.environ,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "28069|28072|21411|28010")

    def test_doctor_reports_a_missing_target_before_recommending_deploy(self):
        rows = self.row("one", "db", "running", str(ROOT), "db")

        completed = self.run_dev(
            "doctor",
            rows,
            USL_FAKE_DATABASE_QUERY="",
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Target:   missing", completed.stdout)
        self.assertIn("deploy cannot recreate source data", completed.stdout)
        self.assertIn("migration/manage qa refresh", completed.stdout)
        self.assertNotIn("Update mounted add-ons with", completed.stdout)

    def test_mixed_project_blocks_deploy_with_actionable_next_steps(self):
        foreign = self.temporary / "foreign"
        foreign.mkdir()
        rows = self.row("one", "odoo", "running", str(ROOT), "odoo") + self.row(
            "two",
            "db",
            "running",
            str(foreign),
            "db",
        )

        completed = self.run_dev("deploy", rows)

        self.assertEqual(completed.returncode, 2)
        self.assertIn("Blocked", completed.stderr)
        self.assertIn("Why", completed.stderr)
        self.assertIn("No changes were made", completed.stderr)
        self.assertIn("make doctor", completed.stderr)
        self.assertEqual(
            self.docker_log.read_text(encoding="utf-8").count("ps -a"),
            1,
        )

    def test_confirmed_container_removal_never_calls_volume_delete(self):
        rows = self.row("one", "odoo", "exited", str(ROOT), "odoo") + self.row(
            "two",
            "db",
            "exited",
            str(self.temporary / "foreign"),
            "db",
        )
        command = (
            'usl_compose_scope_scan test-project "$2"; '
            "usl_remove_compose_containers"
        )

        completed = self.run_scope(command, rows, str(ROOT))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        docker_calls = self.docker_log.read_text(encoding="utf-8")
        self.assertIn("rm --force one", docker_calls)
        self.assertIn("rm --force two", docker_calls)
        self.assertNotIn("volume rm", docker_calls)
        self.assertNotIn("down", docker_calls)

    def test_plain_make_is_help_and_common_variables_are_forwarded(self):
        default = subprocess.run(
            ["make", "-n"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        deploy = subprocess.run(
            ["make", "-n", "deploy", "MODULE=usl_accounting"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertIn("USL Odoo Distribution", default.stdout)
        self.assertNotIn("scripts/odoo-dev start", default.stdout)
        self.assertEqual(deploy.returncode, 0, deploy.stderr)
        self.assertIn('deploy "usl_accounting"', deploy.stdout)

        qa = subprocess.run(
            ["make", "-n", "qa"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(qa.returncode, 0)
        self.assertIn("No rule to make target", qa.stderr)
        self.assertTrue((ROOT / "migration/manage").is_file())

    def test_target_database_preflight_precedes_identity_and_document_services(self):
        helper = POCKET_ID_DEV.read_text(encoding="utf-8")
        configure = helper.split("configure_odoo() {", 1)[1].split(
            "\n}\n\nsync_paperless_users()",
            1,
        )[0]

        self.assertLess(
            configure.index("require_target_database"),
            configure.index("provision"),
        )
        self.assertLess(
            configure.index("require_target_database"),
            configure.index("start_paperless_runtime"),
        )
        self.assertIn("Deploy updates an existing reconstructed target", helper)

    def make_project(self, *, dotenv="", **environment):
        """Report the Compose project a `make` invocation actually resolves.

        The answer must come from this call, not from the checkout it runs in.
        `make worktree-env >> .env` is a documented step, and the Makefile
        deliberately reads that file as its last fallback, so a developer who
        followed the instruction would otherwise fail the default assertion
        below on a checkout that is working perfectly. Pin the fallback on the
        command line, where it overrides the Makefile, and drop the three
        documented variables from the inherited environment for the same
        reason. Pass `dotenv` to exercise the fallback itself.
        """
        environment = {
            **{
                name: value
                for name, value in os.environ.items()
                if name not in PROJECT_VARIABLES
            },
            **environment,
        }
        completed = subprocess.run(
            ["make", f"DOTENV_COMPOSE_PROJECT={dotenv}", "doctor"],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        for line in completed.stdout.splitlines():
            if line.strip().startswith("Project:"):
                return line.split(":", 1)[1].strip()
        return None

    def test_make_accepts_every_documented_compose_project_variable(self):
        """COMPOSE_PROJECT_NAME used to be silently replaced by the default."""
        for variable in PROJECT_VARIABLES:
            with self.subTest(variable=variable):
                self.assertEqual(
                    self.make_project(**{variable: "usl-probe"}),
                    "usl-probe",
                )

        self.assertEqual(self.make_project(), "usl-odoo-saas-19-3")

    def test_dotenv_supplies_the_project_but_never_outranks_a_variable(self):
        """Compose reads .env by itself; make must agree, and yield to a variable."""
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("test -f .env", makefile)

        self.assertEqual(self.make_project(dotenv="usl-dotenv"), "usl-dotenv")
        for variable in PROJECT_VARIABLES:
            with self.subTest(variable=variable):
                self.assertEqual(
                    self.make_project(
                        dotenv="usl-dotenv", **{variable: "usl-probe"},
                    ),
                    "usl-probe",
                )

    def test_compose_project_precedence_is_the_same_everywhere(self):
        """`make`, odoo-dev and accounting_compat must not disagree."""
        self.assertEqual(
            self.make_project(
                COMPOSE_PROJECT_NAME="usl-standard",
                ODOO_SAAS_COMPOSE_PROJECT="usl-legacy",
            ),
            "usl-standard",
        )

        helper = ODOO_DEV.read_text(encoding="utf-8")
        self.assertIn(
            'COMPOSE_PROJECT="${COMPOSE_PROJECT_NAME:-'
            '${ODOO_SAAS_COMPOSE_PROJECT:-usl-odoo-saas-19-3}}"',
            helper,
        )

    def test_worktree_env_derives_isolated_project_and_ports(self):
        completed = subprocess.run(
            ["make", "worktree-env"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        settings = dict(
            line.split("=", 1)
            for line in completed.stdout.splitlines()
            if "=" in line
        )
        # COMPOSE_PROJECT_NAME, not COMPOSE_PROJECT: a bare `docker compose`
        # reads only this spelling, so one assignment has to serve both.
        self.assertEqual(
            sorted(settings),
            [
                "COMPOSE_PROJECT_NAME",
                "ODOO_GEVENT_PORT",
                "ODOO_HTTP_PORT",
                "PAPERLESS_HTTP_PORT",
                "POCKET_ID_HTTP_PORT",
            ],
        )
        self.assertNotEqual(settings["COMPOSE_PROJECT_NAME"], "usl-odoo-saas-19-3")

        ports = [int(settings[name]) for name in settings if name.endswith("PORT")]
        self.assertEqual(len(set(ports)), len(ports), "ports must not collide")
        for port in ports:
            self.assertGreater(port, 1024)
            self.assertLess(port, 65536)
        # The canonical published ports must never be handed to a worktree.
        self.assertFalse({8069, 8072, 1411, 8010}.intersection(ports))

    def test_worktree_env_persisted_to_dotenv_reaches_make(self):
        """`make worktree-env >> .env` has to apply to make, not only Compose."""
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("DOTENV_COMPOSE_PROJECT", makefile)
        self.assertIn("$(DOTENV_COMPOSE_PROJECT)", makefile)
        # An explicit variable must still outrank the file.
        resolution = makefile.split("COMPOSE_PROJECT ?=", 1)[1].splitlines()[0]
        self.assertLess(
            resolution.index("$(COMPOSE_PROJECT_NAME)"),
            resolution.index("$(DOTENV_COMPOSE_PROJECT)"),
        )

    def test_worktree_env_is_stable_for_the_same_checkout(self):
        first = subprocess.run(
            ["make", "worktree-env"], cwd=ROOT, check=False,
            capture_output=True, text=True,
        )
        second = subprocess.run(
            ["make", "worktree-env"], cwd=ROOT, check=False,
            capture_output=True, text=True,
        )

        self.assertEqual(first.stdout, second.stdout)

    def test_blocked_worktree_message_offers_settings_that_work(self):
        """Naming the variables was never enough; the values are the hard part."""
        scope = COMPOSE_SCOPE.read_text(encoding="utf-8")

        self.assertIn("usl_worktree_env_prefix", scope)
        self.assertIn("make worktree-env", scope)
        self.assertNotIn(
            "Use a dedicated COMPOSE_PROJECT and non-conflicting ports",
            scope,
        )

    def test_dev_reclaim_target_exists_and_demands_confirmation(self):
        """compose-scope.sh has always pointed at this target; it must resolve."""
        scope = COMPOSE_SCOPE.read_text(encoding="utf-8")
        self.assertIn("make dev-reclaim CONFIRM=", scope)

        completed = subprocess.run(
            ["make", "dev-reclaim"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertNotIn("No rule to make target", completed.stderr)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("Blocked", completed.stderr)
        self.assertIn("CONFIRM=", completed.stderr)

    def test_dev_reclaim_refuses_a_confirmation_naming_another_project(self):
        completed = subprocess.run(
            ["make", "dev-reclaim", "CONFIRM=some-other-project"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("does not name the project", completed.stderr)

    def test_missing_database_offers_a_way_to_build_one(self):
        """The advice used to dead-end at a source-data reconstruction."""
        helper = ODOO_DEV.read_text(encoding="utf-8")
        assessment = helper.split("The %s database is missing", 1)[1][:600]

        self.assertIn("make init-db", assessment)
        self.assertIn("make action-risk-db", assessment)
        # The migration path stays, but no longer as the only option.
        self.assertIn("migration/manage qa refresh", assessment)

    def test_database_targets_are_reachable_from_make(self):
        for target in ("init-db", "action-risk-db"):
            with self.subTest(target=target):
                completed = subprocess.run(
                    ["make", "-n", target],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn(f"odoo-dev {target}", completed.stdout)

    def test_action_risk_database_installs_the_tracked_roots(self):
        """The database must not drift from what the inventory compares to."""
        surface = json.loads(
            (
                ROOT
                / "custom-addons/usl_access_control/policy/action_surface.json"
            ).read_text(encoding="utf-8"),
        )
        helper = ODOO_DEV.read_text(encoding="utf-8")

        # Roots are read from the tracked file, never restated in the script.
        self.assertIn("action_surface.json", helper)
        self.assertIn('ODOO_INIT_MODULES="$(action_risk_root_modules)"', helper)
        for root in surface["root_modules"]:
            self.assertNotIn(f'"{root}"', helper.split("action_risk_root_modules")[0])

    def test_action_risk_database_reuses_the_canonical_scope_enforcement(self):
        """Do not add a second way to remove the optional auto-installs."""
        helper = ODOO_DEV.read_text(encoding="utf-8")
        block = helper.split("action-risk-db)", 1)[1].split(";;", 1)[0]

        self.assertIn("scripts/odoo/enforce_product_module_scope.py", block)
        self.assertTrue(
            (ROOT / "scripts/odoo/enforce_product_module_scope.py").is_file(),
        )
        # ci-product-database and migration/internal/finalize rely on it too.
        pipeline = (ROOT / "scripts/ci-product-database").read_text(encoding="utf-8")
        self.assertIn("enforce_product_module_scope.py", pipeline)

    def test_action_risk_database_matches_the_canonical_build_sequence(self):
        """A database built differently reports drift in untouched modules."""
        helper = ODOO_DEV.read_text(encoding="utf-8")
        block = helper.split("action-risk-db)", 1)[1].split("\n    ;;", 1)[0]
        pipeline = (ROOT / "scripts/ci-product-database").read_text(encoding="utf-8")

        # ci-product-database is the reference: init, enforce scope, update twice.
        self.assertEqual(pipeline.count('odoo_run --update="$modules"'), 2)
        self.assertEqual(block.count("action_risk_update_closure"), 2)
        self.assertLess(
            block.index("enforce_product_module_scope.py"),
            block.index("action_risk_update_closure"),
        )

    def test_closure_verification_refuses_a_mismatched_database(self):
        """A database wide of the tracked set makes any discovery diff a lie."""
        script = (
            ROOT / "scripts/odoo/verify_tracked_module_closure.py"
        ).read_text(encoding="utf-8")

        self.assertIn("action_surface.json", script)
        self.assertIn("Module set does not match the tracked closure", script)
        # Both directions must fail, not just extras.
        self.assertIn("tracked - installed", script)
        self.assertIn("installed - tracked", script)
        # It only reports; removal stays with the canonical script.
        self.assertNotIn("button_immediate_uninstall", script)

    def test_repair_hint_never_hands_a_worktree_the_canonical_project(self):
        """It echoed current values, i.e. the one project a worktree may not use."""
        helper = ODOO_DEV.read_text(encoding="utf-8")
        hint = helper.split("Pocket ID repair", 1)[1][:900]

        self.assertIn("usl_worktree_is_linked", hint)
        self.assertIn("usl_worktree_env_prefix", hint)
        self.assertIn("CANONICAL_COMPOSE_PROJECT", hint)

    def test_preproduction_boundary_rejects_partial_qa_profiles(self):
        boundary = (ROOT / "scripts/odoo/product_database_boundary.py").read_text(
            encoding="utf-8",
        )
        runner = (ROOT / "scripts/check-product-database-boundary").read_text(
            encoding="utf-8",
        )

        self.assertIn("usl.qa.data_profile", boundary)
        self.assertIn("Pre-production cannot use partial QA data profile", boundary)
        self.assertIn("USL_PRODUCT_BOUNDARY_PREPROD", runner)


if __name__ == "__main__":
    unittest.main()
