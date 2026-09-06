from __future__ import annotations

import contextlib
import io
import os
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "odoo" / "worktree_release_identity.py"
COMMIT = "a" * 40


class _Cursor:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


class _Parameters:
    def __init__(self):
        self.values = {}

    def sudo(self):
        return self

    def set_str(self, key, value):
        self.values[key] = value


class _Environment:
    def __init__(self):
        self.cr = _Cursor()
        self.parameters = _Parameters()

    def __getitem__(self, model):
        if model != "ir.config_parameter":
            raise KeyError(model)
        return self.parameters


class WorktreeReleaseIdentityTest(unittest.TestCase):
    def run_script(self, **environment):
        env = _Environment()
        values = {
            "USL_DEPLOYMENT_ENV": "qa",
            "USL_EINVOICE_LIVE_ENABLED": "0",
            "USL_EREPORTING_LIVE_ENABLED": "0",
            "USL_WORKTREE_RELEASE_COMMIT": COMMIT,
            **environment,
        }
        with (
            patch.dict(os.environ, values, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            runpy.run_path(str(SCRIPT), init_globals={"env": env})
        return env

    def test_records_an_exact_clean_worktree_commit(self):
        env = self.run_script()

        self.assertEqual(COMMIT, env.parameters.values["usl.release.commit"])
        self.assertEqual(1, env.cr.commits)

    def test_empty_commit_clears_stale_identity(self):
        env = self.run_script(USL_WORKTREE_RELEASE_COMMIT="")

        self.assertIsNone(env.parameters.values["usl.release.commit"])

    def test_rejects_production_and_live_regulatory_modes(self):
        with self.assertRaisesRegex(RuntimeError, "development and QA"):
            self.run_script(USL_DEPLOYMENT_ENV="production")
        with self.assertRaisesRegex(RuntimeError, "regulatory live flags"):
            self.run_script(USL_EINVOICE_LIVE_ENABLED="1")

    def test_rejects_an_unverified_commit_label(self):
        with self.assertRaisesRegex(RuntimeError, "exact lowercase commit SHA"):
            self.run_script(USL_WORKTREE_RELEASE_COMMIT="unverified")


if __name__ == "__main__":
    unittest.main()
