from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "worktree-env"


def assignments(path):
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; usl_worktree_env_assignments "$2"', "worktree-env", str(SCRIPT), path],
        check=True, capture_output=True, text=True,
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


class WorktreeEnvironmentTest(unittest.TestCase):
    def test_managed_checkouts_with_the_same_basename_have_distinct_projects(self):
        first = assignments("/workspace/worktrees/feedback/odoo")
        second = assignments("/workspace/worktrees/agents/odoo")

        self.assertNotEqual(first["COMPOSE_PROJECT_NAME"], second["COMPOSE_PROJECT_NAME"])

    def test_project_identity_is_stable_and_compose_compatible(self):
        for path in (
            "/workspace/worktrees/feedback/odoo",
            "/workspace/worktrees/agents/odoo",
            "/workspace/Feature With Spaces",
            "/workspace/" + "A" * 100,
            "/workspace/équipe",
        ):
            with self.subTest(path=path):
                values = assignments(path)
                self.assertEqual(values, assignments(path))
                project = values["COMPOSE_PROJECT_NAME"]
                self.assertRegex(project, re.compile(r"^[a-z0-9][a-z0-9_-]*$"))
                self.assertLessEqual(len(project), 63)
                self.assertEqual(int(values["ODOO_HTTP_PORT"]) + 3, int(values["ODOO_GEVENT_PORT"]))

    def test_port_bucket_collision_does_not_share_project_ownership(self):
        first = assignments("/workspace/worktrees/agent-5/odoo")
        second = assignments("/workspace/worktrees/agent-19/odoo")

        self.assertEqual(first["ODOO_HTTP_PORT"], second["ODOO_HTTP_PORT"])
        self.assertNotEqual(first["COMPOSE_PROJECT_NAME"], second["COMPOSE_PROJECT_NAME"])


if __name__ == "__main__":
    unittest.main()
