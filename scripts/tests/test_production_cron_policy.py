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
INBOUND_MAIL_CRONS = {"mail.ir_cron_mail_gateway_action"}


def load_script_function(name):
    module = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name == name
    )
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), SCRIPT, "exec"), namespace)
    return namespace[name]


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
        resolve = load_script_function("expected_regulatory_gates")

        self.assertEqual(
            resolve({
                "USL_EINVOICE_LIVE_ENABLED": "1",
                "USL_EREPORTING_LIVE_ENABLED": "0",
            }),
            {"pdp_reception": True, "pdp_ereporting": False},
        )

    def test_inbound_mail_has_an_independent_production_gate(self):
        policy = json.loads(POLICY.read_text(encoding="utf-8"))

        self.assertIn("inbound_mail", policy["gates"])
        self.assertEqual(
            {
                xmlid
                for xmlid, rule in policy["crons"].items()
                if rule["gate"] == "inbound_mail"
            },
            INBOUND_MAIL_CRONS,
        )

    def test_admitted_inbound_server_contract_is_explicit(self):
        expected = load_script_function("expected_inbound_server")()

        self.assertEqual(
            expected,
            {
                "server_type": "imap",
                "state": "done",
                "server": "imap.gmail.com",
                "port": 993,
                "is_ssl": True,
                "object_id": False,
            },
        )

    def test_ereporting_cannot_precede_invoice_exchange(self):
        resolve = load_script_function("expected_regulatory_gates")

        with self.assertRaisesRegex(RuntimeError, "before invoice exchange"):
            resolve({
                "USL_EINVOICE_LIVE_ENABLED": "0",
                "USL_EREPORTING_LIVE_ENABLED": "1",
            })

    def test_production_mail_alias_domain_is_explicit(self):
        expected = load_script_function("expected_mail_alias_domain")()

        self.assertEqual(
            expected,
            {
                "name": "unstaticlabs.com",
                "bounce_alias": "bounce",
                "catchall_alias": "catchall",
                "default_from": "odoo",
            },
        )


if __name__ == "__main__":
    unittest.main()
