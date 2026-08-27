import os
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from paperless.models import ApplicationConfiguration

from paperless_personal_ai.tests.base import PersonalAIKeyFileMixin


class TestPersonalAIReleaseBoundary(PersonalAIKeyFileMixin, TestCase):
    def test_release_check_accepts_only_the_mounted_key_path(self):
        stdout = StringIO()

        call_command("check_personal_ai_release", stdout=stdout)

        self.assertIn("USL_PERSONAL_AI_RELEASE_READY=test-key:1", stdout.getvalue())

    def test_release_check_rejects_inline_key_environment_without_echoing_it(self):
        secret = "inline-master-key-material"
        with patch.dict(
            os.environ,
            {"PAPERLESS_USL_PERSONAL_AI_MASTER_KEYS": secret},
            clear=False,
        ):
            with self.assertRaises(CommandError) as raised:
                call_command("check_personal_ai_release")

        self.assertNotIn(secret, str(raised.exception))

    def test_release_check_rejects_native_global_generative_environment(self):
        secret = "legacy-global-api-key"
        with patch.dict(
            os.environ,
            {"PAPERLESS_AI_LLM_API_KEY": secret},
            clear=False,
        ):
            with self.assertRaises(CommandError) as raised:
                call_command("check_personal_ai_release")

        self.assertIn("PAPERLESS_AI_LLM_API_KEY", str(raised.exception))
        self.assertNotIn(secret, str(raised.exception))

    def test_release_check_rejects_native_global_generative_database_value(self):
        secret = "legacy-database-api-key"
        configuration = (
            ApplicationConfiguration.objects.first()
            or ApplicationConfiguration.objects.create()
        )
        configuration.llm_api_key = secret
        configuration.save(update_fields=["llm_api_key"])

        with self.assertRaises(CommandError) as raised:
            call_command("check_personal_ai_release")

        self.assertIn("llm_api_key", str(raised.exception))
        self.assertNotIn(secret, str(raised.exception))
