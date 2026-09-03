from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
ODOO_DEV = ROOT / "scripts" / "odoo-dev"
COMPOSE_SCOPE = ROOT / "scripts" / "lib" / "compose-scope.sh"
POCKET_ID_DEV = ROOT / "scripts" / "pocket-id-dev"


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
