import ast
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "deploy" / "production.cron-policy.json"
SCRIPT = ROOT / "scripts" / "odoo" / "production_admission_policy.py"

RECEPTION_CRONS = {
    "account_peppol.ir_cron_peppol_get_message_status",
    "account_peppol.ir_cron_peppol_get_new_documents",
    "account_peppol.ir_cron_peppol_get_participant_status",
    "account_peppol.ir_cron_peppol_webhook_keepalive",
    "l10n_fr_pdp.ir_cron_pdp_get_regulatory_documents",
}
EREPORTING_CRONS = {
    "account_peppol_response.ir_cron_peppol_auto_register_services",
    "l10n_fr_pdp.ir_cron_l10n_fr_pdp_generate_flows",
    "l10n_fr_pdp.ir_cron_pdp_send_lifecycles",
}


def load_expected_regulatory_gates():
    module = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "expected_regulatory_gates"
    )
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), SCRIPT, "exec"), namespace)
    return namespace["expected_regulatory_gates"]


class ProductionCronPolicyTest(unittest.TestCase):
    def test_reception_and_ereporting_have_independent_cron_gates(self):
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        rules = policy["crons"]

        self.assertNotIn("pdp", policy["gates"])
        self.assertEqual(
            {xmlid for xmlid, rule in rules.items() if rule["gate"] == "pdp_reception"},
            RECEPTION_CRONS,
        )
        self.assertEqual(
            {xmlid for xmlid, rule in rules.items() if rule["gate"] == "pdp_ereporting"},
            EREPORTING_CRONS,
        )

    def test_invoice_reception_can_precede_ereporting(self):
        resolve = load_expected_regulatory_gates()

        self.assertEqual(
            resolve({
                "USL_EINVOICE_LIVE_ENABLED": "1",
                "USL_EREPORTING_LIVE_ENABLED": "0",
            }),
            {"pdp_reception": True, "pdp_ereporting": False},
        )

    def test_ereporting_cannot_precede_invoice_exchange(self):
        resolve = load_expected_regulatory_gates()

        with self.assertRaisesRegex(RuntimeError, "before invoice exchange"):
            resolve({
                "USL_EINVOICE_LIVE_ENABLED": "0",
                "USL_EREPORTING_LIVE_ENABLED": "1",
            })


if __name__ == "__main__":
    unittest.main()
