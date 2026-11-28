import os
from unittest.mock import patch

from odoo import Command
from odoo.tests import new_test_user, tagged
from odoo.tests.common import ChromeBrowser

from odoo.addons.usl_docs.tests.journey import JourneyCase

from .. import controllers, models
from ..speech import SpeechUnavailable


@tagged("post_install", "-at_install", "usl_docs_journey")
class TestVoiceJourneys(JourneyCase):
    def test_voice_input_in_english(self):
        self._run_voice_journey("Dictation example", "This is a sample voice note.")

    def test_voice_input_in_french(self):
        self.journey_language = "fr"
        self._run_voice_journey("Exemple de dictée", "Ceci est un exemple de note vocale.")

    def _run_voice_journey(self, dictation, transcript):
        writer = new_test_user(
            self.env, login="voice-guide-writer", name="Voice Guide Writer",
            groups="base.group_user,project.group_project_user",
            context={"no_reset_password": True},
        )
        project = self.env["project.project"].create({"name": "Voice Guide"})
        task = self.env["project.task"].create({
            "name": " ", "project_id": project.id, "user_ids": [Command.set(writer.ids)],
        })
        screen = "/odoo/action-project.action_view_my_task"
        spawn = ChromeBrowser._spawn_chrome

        def spawn_with_fake_microphone(browser, cmd):
            return spawn(browser, [
                *cmd[:-1],
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
                cmd[-1],
            ])

        def dictate(_data, _mimetype, _lang, *, patience):
            if not attempts:
                attempts.append(True)
                raise SpeechUnavailable()
            return dictation

        attempts = []
        with (
            patch.object(ChromeBrowser, "_spawn_chrome", spawn_with_fake_microphone),
            patch.object(controllers, "transcribe", side_effect=dictate),
            patch.object(models, "transcribe", return_value=transcript) as speech,
            patch.dict(os.environ, {"QUEUE_JOB__NO_DELAY": "1"}),
        ):
            self.run_journey("usl_voice_input_journey", f"{screen}/{task.id}", login=writer.login, screen=screen)
        task.invalidate_recordset()
        self.assertEqual(task.name.strip(), dictation)
        voice = self.env["discuss.voice.metadata"].search([("usl_message_id", "in", task.message_ids.ids)])
        self.assertEqual(voice.usl_message_id.subtype_id, self.env.ref("mail.mt_note"))
        self.assertEqual(voice.usl_message_id.author_id, writer.partner_id)
        self.assertEqual(voice.usl_message_id.with_user(writer).usl_voice_notes, [{
            "attachment_id": voice.attachment_id.id, "state": "done", "text": transcript,
        }])
        self.assertGreater(voice.attachment_id.file_size, 0)
        speech.assert_called_once()
