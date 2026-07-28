from __future__ import annotations

import argparse
import ast
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from accounting_compat import cli


class ManagerAccountingIdentityTest(unittest.TestCase):
    source = {
        "candidate_count": 1,
        "employee_source_id": 1,
        "partner_source_id": 3,
        "user_partner_source_id": 3,
        "payable_source_account_id": 344,
        "payable_code": "455100",
        "payable_reconcile": True,
        "cca_line_count": 200,
        "open_debit_count": 4,
    }

    def test_accepts_one_canonical_source_faithful_identity(self):
        target = {
            **{
                key: value
                for key, value in self.source.items()
                if key != "candidate_count"
            },
            "canonical_partner": True,
            "configured_cca_account": True,
            "configured_cca_employee": True,
        }

        self.assertTrue(
            cli.manager_accounting_identity_matches(self.source, target),
        )

    def test_rejects_split_user_and_employee_contacts(self):
        target = {
            **{
                key: value
                for key, value in self.source.items()
                if key != "candidate_count"
            },
            "canonical_partner": False,
            "configured_cca_account": True,
            "configured_cca_employee": True,
        }

        self.assertFalse(
            cli.manager_accounting_identity_matches(self.source, target),
        )

    def test_rejects_wrong_payable_or_missing_outstanding_debit(self):
        wrong_payable = {
            **{
                key: value
                for key, value in self.source.items()
                if key != "candidate_count"
            },
            "payable_source_account_id": 999,
            "payable_code": "401100",
            "canonical_partner": True,
            "configured_cca_account": True,
            "configured_cca_employee": True,
        }
        no_outstanding_debit = {
            **{
                key: value
                for key, value in self.source.items()
                if key != "candidate_count"
            },
            "open_debit_count": 0,
            "canonical_partner": True,
            "configured_cca_account": True,
            "configured_cca_employee": True,
        }

        self.assertFalse(
            cli.manager_accounting_identity_matches(
                self.source,
                wrong_payable,
            ),
        )
        self.assertFalse(
            cli.manager_accounting_identity_matches(
                {**self.source, "open_debit_count": 0},
                no_outstanding_debit,
            ),
        )

    def test_rejects_missing_source_or_target_identity(self):
        self.assertFalse(
            cli.manager_accounting_identity_matches(None, self.source),
        )
        self.assertFalse(
            cli.manager_accounting_identity_matches(self.source, None),
        )

    def test_rejects_ambiguous_source_or_unconfigured_projection(self):
        target = {
            **{
                key: value
                for key, value in self.source.items()
                if key != "candidate_count"
            },
            "canonical_partner": True,
            "configured_cca_account": True,
            "configured_cca_employee": False,
        }

        self.assertFalse(
            cli.manager_accounting_identity_matches(
                {**self.source, "candidate_count": 2},
                target,
            ),
        )
        self.assertFalse(
            cli.manager_accounting_identity_matches(self.source, target),
        )

    def test_generated_import_script_reuses_the_source_partner(self):
        source_profile = {
            "date_to": "2026-06-30",
            "source_move_count": 1,
            "source_move_line_count": 2,
            "source_non_posted_move_count": 0,
            "source_native_context_line_count": 0,
            "source_expense_count": 1,
            "source_asset_count": 0,
            "source_posted_asset_move_count": 0,
        }
        failed_shell = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="expected unit stop",
        )

        with tempfile.TemporaryDirectory() as tmp:
            private_artifacts = Path(tmp)
            with (
                patch.object(cli, "PRIVATE_ARTIFACTS", private_artifacts),
                patch.object(
                    cli,
                    "validate_source",
                    return_value={"dump": {"sha256": "a" * 64}},
                ),
                patch.object(cli, "table_exists", return_value=True),
                patch.object(cli, "query_json", return_value=source_profile),
                patch.object(
                    cli,
                    "source_manager_accounting_identity",
                    return_value=self.source,
                ),
                patch.object(cli, "run", return_value=failed_shell),
                self.assertRaises(cli.HarnessError),
            ):
                cli.dev_import(argparse.Namespace())

            generated = (
                private_artifacts / "dev-import-source-snapshot.py"
            ).read_text(encoding="utf-8")

        ast.parse(generated)
        self.assertIn("values['partner_id'] = partner.id", generated)
        self.assertIn("partner=manager_partner", generated)
        self.assertIn("'canonical_partner': (", generated)
        self.assertIn(
            "'rebuild_source_id', '=', 1",
            generated,
        )
        self.assertIn(
            "'rebuild_overview_cca_account_id': manager_payable.id",
            generated,
        )
        self.assertIn(
            "'rebuild_overview_cca_employee_id': manager_employee.id",
            generated,
        )
        self.assertNotIn("('name', 'ilike', 'Valentin')", generated)
        self.assertNotIn(
            "manager_employee = env['hr.employee'].create",
            generated,
        )


if __name__ == "__main__":
    unittest.main()
