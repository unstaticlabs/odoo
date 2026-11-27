from unittest.mock import MagicMock, patch

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..speech import SpeechUnavailable, transcribe


@tagged("post_install", "-at_install")
class TestVoice(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.writer = new_test_user(cls.env, login="voice_writer", groups="base.group_user")
        cls.portal = new_test_user(cls.env, login="voice_portal", groups="base.group_portal")

    def post_voice(self, subtype="mail.mt_note", recipient=None):
        target = self.writer.partner_id.with_user(self.writer)
        attachment = self.env["ir.attachment"].create({
            "name": "Voice.mp3", "raw": b"ID3synthetic-audio", "mimetype": "audio/mpeg",
            "res_model": target._name, "res_id": target.id,
        })
        attachment._set_voice_metadata()
        message = target.message_post(
            body="Original caption", attachment_ids=attachment.ids,
            subtype_xmlid=subtype, message_type="comment", partner_ids=(recipient.ids if recipient else []),
        )
        return message.with_user(self.writer), attachment.voice_ids

    def test_transcribed_once_on_original_internal_note(self):
        message, voice = self.post_voice()
        self.assertEqual(voice.usl_transcription_state, "pending")
        self.assertEqual(message.usl_voice_notes[0]["state"], "pending")
        caption = message.body
        text = "Bonjour à toute l’équipe."
        with patch("odoo.addons.usl_voice.models.transcribe", return_value=text) as server:
            message._usl_transcribe_voice(voice.id)
            message._usl_transcribe_voice(voice.id)
        server.assert_called_once_with(b"ID3synthetic-audio", "audio/mpeg", self.env.user.lang, patience=90)
        self.assertIsInstance(server.call_args.args[0], bytes)
        message.invalidate_recordset(["usl_voice_notes"])
        self.assertEqual(message.usl_voice_notes, [{
            "attachment_id": voice.attachment_id.id, "state": "done", "text": text,
        }])
        self.assertEqual(message.body, caption)
        with self.assertRaises(AccessError):
            message.with_user(self.portal).read(["usl_voice_notes"])
        with self.assertRaises(AccessError):
            message.with_user(self.portal).action_usl_retry_voice()

    def test_failure_retry_and_silence_keep_recording(self):
        message, voice = self.post_voice("mail.mt_comment")
        with patch("odoo.addons.usl_voice.models.transcribe", side_effect=SpeechUnavailable):
            message._usl_transcribe_voice(voice.id)
        self.assertEqual(voice.usl_transcription_state, "failed")
        self.assertIn(voice.attachment_id, message.attachment_ids.sudo())
        message.action_usl_retry_voice()
        with patch("odoo.addons.usl_voice.models.transcribe", return_value=""):
            message._usl_transcribe_voice(voice.id)
        self.assertEqual(voice.usl_transcription_state, "silent")
        self.assertFalse(voice.usl_transcription)

    def test_external_recipient_reads_only_accessible_voice(self):
        target = self.portal.partner_id
        attachment = self.env["ir.attachment"].create({
            "name": "Voice.mp3", "raw": b"ID3synthetic", "mimetype": "audio/mpeg",
            "res_model": target._name, "res_id": target.id,
        })
        attachment._set_voice_metadata()
        message = target.message_post(body="Shared recording", attachment_ids=attachment.ids,
                                      partner_ids=target.ids, subtype_xmlid="mail.mt_comment",
                                      message_type="comment")
        with patch("odoo.addons.usl_voice.models.transcribe", return_value="Shared words"):
            message._usl_transcribe_voice(attachment.voice_ids.id)
        reader = message.with_user(self.portal)
        self.assertEqual(reader.read(["usl_voice_notes"])[0]["usl_voice_notes"][0]["text"], "Shared words")

    def test_access_loss_before_job_preserves_audio_for_retry(self):
        message, voice = self.post_voice()
        with patch("odoo.addons.usl_voice.models.transcribe") as server:
            message.with_user(self.portal)._usl_transcribe_voice(voice.id)
        server.assert_not_called()
        self.assertEqual(voice.usl_transcription_state, "failed")
        message.action_usl_retry_voice()
        self.assertEqual(voice.usl_transcription_state, "pending")

    def test_deleted_note_cannot_be_populated_by_pending_job(self):
        message, voice = self.post_voice()
        voice_id = voice.id
        message.with_user(self.env.ref("base.user_root")).unlink()
        with patch("odoo.addons.usl_voice.models.transcribe") as server:
            message._usl_transcribe_voice(voice_id)
        server.assert_not_called()
        self.assertFalse(voice.exists())

    def test_backend_uses_private_gateway_and_discards_invalid_response(self):
        with patch("odoo.addons.usl_voice.speech.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"text": " Bonjour ", "language": "fr"}
            self.assertEqual(transcribe(b"audio", "audio/mpeg"), "Bonjour")
            self.assertTrue(post.call_args.args[0].endswith("/v1/audio/transcriptions"))
            self.assertEqual(post.call_args.kwargs["files"]["file"], ("voice.mp3", b"audio", "audio/mpeg"))
            post.return_value.status_code = 503
            with self.assertRaises(SpeechUnavailable):
                transcribe(b"audio", "audio/mpeg")
            with self.assertRaises(ValueError):
                transcribe(b"", "audio/mpeg")

    def test_a_busy_worker_is_asked_again_within_the_patience_given(self):
        def answers(*codes):
            replies = []
            for code in codes:
                reply = MagicMock(status_code=code, headers={"Retry-After": "2"} if code == 503 else {})
                reply.json.return_value = {"text": "Bonjour", "language": "fr"}
                replies.append(reply)
            return replies

        with patch("odoo.addons.usl_voice.speech.requests.post") as post, \
                patch("odoo.addons.usl_voice.speech.time.sleep") as sleep:
            # Without patience, as for a caption, a busy worker is a failure at once.
            post.side_effect = answers(503)
            with self.assertRaises(SpeechUnavailable):
                transcribe(b"audio", "audio/mpeg")
            sleep.assert_not_called()
            # With patience the recording is sent again after the pause the worker asks for.
            post.side_effect = answers(503, 503, 200)
            self.assertEqual(transcribe(b"audio", "audio/mpeg", patience=20), "Bonjour")
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 2])
            # Patience shorter than one pause does not wait at all.
            sleep.reset_mock()
            post.side_effect = answers(503)
            with self.assertRaises(SpeechUnavailable):
                transcribe(b"audio", "audio/mpeg", patience=1)
            sleep.assert_not_called()
            # A failed recognition, or a gateway with no worker awake, answers 503 without
            # saying when to come back: it is not asked again, however patient the caller.
            post.reset_mock()
            post.side_effect = [MagicMock(status_code=503, headers={})]
            with self.assertRaises(SpeechUnavailable):
                transcribe(b"audio", "audio/mpeg", patience=90)
            self.assertEqual(post.call_count, 1)
            sleep.assert_not_called()

    def test_words_heard_are_kept_when_the_second_hearing_fails(self):
        first = MagicMock(status_code=200, headers={})
        first.json.return_value = {"text": "Obrigado", "language": "pt"}
        with patch("odoo.addons.usl_voice.speech.requests.post",
                   side_effect=[first, MagicMock(status_code=503, headers={})]):
            self.assertEqual(transcribe(b"audio", "audio/mpeg"), "Obrigado")

    def test_a_voice_note_waits_for_a_busy_worker(self):
        message, voice = self.post_voice()
        busy = MagicMock(status_code=503, headers={"Retry-After": "2"})
        heard = MagicMock(status_code=200, headers={})
        heard.json.return_value = {"text": "Bonjour", "language": "fr"}
        with patch("odoo.addons.usl_voice.speech.requests.post", side_effect=[busy, heard]), \
                patch("odoo.addons.usl_voice.speech.time.sleep"):
            message._usl_transcribe_voice(voice.id)
        self.assertEqual(voice.usl_transcription_state, "done")
        self.assertEqual(voice.usl_transcription, "Bonjour")

    def test_speech_heard_as_a_third_language_is_heard_again_in_french_or_english(self):
        with patch("odoo.addons.usl_voice.speech.requests.post") as post:
            post.return_value.status_code = 200
            answers = iter([{"text": "Obrigado", "language": "portuguese"}, {"text": "Merci beaucoup", "language": "fr"}])
            post.return_value.json.side_effect = lambda: next(answers)
            self.assertEqual(transcribe(b"audio", "audio/mpeg", "fr_FR"), "Merci beaucoup")
            # The gateway answers 400 to any field it does not know: the first request carries none.
            self.assertEqual(post.call_args_list[0].kwargs["data"], {})
            self.assertEqual(post.call_args_list[1].kwargs["data"], {"language": "fr"})
            post.reset_mock()
            # English speech in a French interface is kept as heard, in one request.
            post.return_value.json.side_effect = None
            post.return_value.json.return_value = {"text": "Thank you", "language": "english"}
            self.assertEqual(transcribe(b"audio", "audio/mpeg", "fr_FR"), "Thank you")
            self.assertEqual(post.call_count, 1)
            post.reset_mock()
            # An English speaker is heard again in English; anyone else in French.
            answers = iter([{"text": "Danke", "language": "de"}, {"text": "Thanks", "language": "en"},
                            {"text": "Danke", "language": "de"}, {"text": "Merci", "language": "fr"}])
            post.return_value.json.side_effect = lambda: next(answers)
            self.assertEqual(transcribe(b"audio", "audio/mpeg", "en_US"), "Thanks")
            self.assertEqual(post.call_args.kwargs["data"]["language"], "en")
            self.assertEqual(transcribe(b"audio", "audio/mpeg", "de_DE"), "Merci")
            self.assertEqual(post.call_args.kwargs["data"]["language"], "fr")
            post.reset_mock()
            # A gateway that does not say what it heard is trusted as before.
            post.return_value.json.side_effect = None
            post.return_value.json.return_value = {"text": "Bonjour"}
            self.assertEqual(transcribe(b"audio", "audio/mpeg"), "Bonjour")
            self.assertEqual(post.call_count, 1)
