import contextlib
import io
import json
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "odoo" / "pocket_id_configure.py"


class _Cursor:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _Provider:
    def _usl_pocketid_apply_environment(self):
        return None


class _Users:
    def __init__(self):
        self.context = {}

    def with_context(self, **context):
        self.context.update(context)
        return self

    def _usl_pocketid_apply_user_configuration(self, *args, **kwargs):
        return {"users": 5}

    def _usl_pocketid_apply_login_policy(self):
        return {"enabled": True}


class _Environment:
    def __init__(self):
        self.cr = _Cursor()
        self.users = _Users()
        self.models = {
            "auth.oauth.provider": _Provider(),
            "res.users": self.users,
        }

    def __getitem__(self, model):
        return self.models[model]


class PocketIdConfigureTest(unittest.TestCase):
    def test_identity_reconciliation_defers_per_user_documents_sync(self):
        env = _Environment()
        environment = {
            "USL_POCKET_ID_APPLY": "1",
            "USL_POCKET_ID_BREAK_GLASS_PASSWORD": "a-secure-test-password-value",
            "USL_POCKET_ID_USERS_JSON": json.dumps([{"login": "valentin"}]),
        }

        with (
            patch.dict("os.environ", environment, clear=False),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            runpy.run_path(str(SCRIPT), init_globals={"env": env})

        self.assertEqual(
            env.users.context,
            {"usl_documents_user_access_no_sync": True},
        )
        self.assertEqual(env.cr.commits, 1)
        self.assertEqual(env.cr.rollbacks, 0)


if __name__ == "__main__":
    unittest.main()
