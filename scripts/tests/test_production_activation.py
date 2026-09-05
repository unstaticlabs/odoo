"""Exercise activation of a previously admitted database and retry semantics."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

SCRIPT = Path(__file__).resolve().parents[1] / 'odoo' / 'production_activate.py'

class ProductionActivationTests(unittest.TestCase):
    def run_activation(self, values):
        params = MagicMock()
        params.get_str.side_effect = lambda key: values.get(key, '')
        params.get_bool.side_effect = lambda key: bool(values.get(key, False))
        params.set_str.side_effect = lambda key, value: values.__setitem__(key, value)
        params.set_bool.side_effect = lambda key, value: values.__setitem__(key, value)
        models = {}
        for name in ['ir.config_parameter', 'mail.mail', 'ir.mail_server', 'ir.ui.view']:
            models[name] = MagicMock()
        models['ir.config_parameter'].sudo.return_value = params
        models['mail.mail'].sudo.return_value.search_count.return_value = 0
        for name in ['ir.mail_server', 'ir.ui.view']:
            models[name].sudo.return_value.search.return_value = []
        env = MagicMock()
        env.__getitem__.side_effect = models.__getitem__
        env.registry = MagicMock()
        env.registry.__contains__.return_value = False
        with patch.dict(os.environ, {
            'USL_PRODUCTION_ACTIVATION_CONFIRM': 'a' * 64,
            'USL_EINVOICE_LIVE_ENABLED': '0',
            'USL_EREPORTING_LIVE_ENABLED': '0',
        }):
            exec(compile(SCRIPT.read_text(), str(SCRIPT), 'exec'), {'env': env})
        return values

    def test_new_release_can_replace_old_admission_and_retry(self):
        values = {
            'database.is_neutralized': True,
            'usl.production.quarantined_candidate_fingerprint': 'a' * 64,
            'usl.production.activation_candidate_fingerprint': 'b' * 64,
            'usl.production.admitted_candidate_fingerprint': 'b' * 64,
        }
        self.run_activation(values)
        self.assertFalse(values['database.is_neutralized'])
        self.assertEqual(values['usl.production.activation_candidate_fingerprint'], 'a' * 64)
        self.assertEqual(values['usl.production.admitted_candidate_fingerprint'], '')
        self.run_activation(values)

    def test_active_different_release_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'another|admission'):
            self.run_activation({
                'database.is_neutralized': False,
                'usl.production.quarantined_candidate_fingerprint': 'a' * 64,
                'usl.production.activation_candidate_fingerprint': 'b' * 64,
                'usl.production.admitted_candidate_fingerprint': 'b' * 64,
            })
