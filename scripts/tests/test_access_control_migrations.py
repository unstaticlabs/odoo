from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT
    / "custom-addons/usl_access_control/migrations/saas~19.3.1.5.0"
    / "pre-agent-access-mode.py"
)
SPEC = importlib.util.spec_from_file_location("pre_agent_access_mode", MIGRATION)
assert SPEC and SPEC.loader
pre_agent_access_mode = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pre_agent_access_mode)


class FakeCursor:
    def __init__(self, *, table_exists: bool) -> None:
        self.table_exists = table_exists
        self.queries: list[str] = []

    def execute(self, query: str) -> None:
        self.queries.append(" ".join(query.split()))

    def fetchone(self) -> tuple[str | None]:
        return ("usl_agent" if self.table_exists else None,)


class AgentAccessMigrationTests(unittest.TestCase):
    def test_first_upgrade_skips_a_table_odoo_has_not_created_yet(self) -> None:
        cursor = FakeCursor(table_exists=False)

        pre_agent_access_mode.migrate(cursor, "saas~19.3.1.3.1")

        self.assertEqual(cursor.queries, ["SELECT to_regclass('usl_agent')"])

    def test_existing_agent_rows_keep_their_read_write_authority(self) -> None:
        cursor = FakeCursor(table_exists=True)

        pre_agent_access_mode.migrate(cursor, "saas~19.3.1.4.0")

        self.assertEqual(len(cursor.queries), 3)
        self.assertIn("ALTER TABLE usl_agent", cursor.queries[1])
        self.assertIn("SET access_mode = 'read_write'", cursor.queries[2])


if __name__ == "__main__":
    unittest.main()
