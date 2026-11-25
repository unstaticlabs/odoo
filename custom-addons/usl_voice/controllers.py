from werkzeug.exceptions import BadRequest, Forbidden, RequestEntityTooLarge, ServiceUnavailable

from odoo import http
from odoo.http import request

from .speech import MAX_AUDIO_BYTES, SpeechUnavailable, transcribe


class VoiceController(http.Controller):
    @http.route("/usl/voice/dictate", methods=["POST"], auth="user", type="http",
                max_content_length=MAX_AUDIO_BYTES + 32 * 1024)
    def dictate(self, file=None, **kwargs):
        if not request.env.user._is_internal():
            raise Forbidden()
        if file is None:
            raise BadRequest()
        data = file.read(MAX_AUDIO_BYTES + 1)
        if len(data) > MAX_AUDIO_BYTES:
            raise RequestEntityTooLarge()
        try:
            text = transcribe(data, file.mimetype, request.env.user.lang)
        except ValueError as exc:
            raise BadRequest() from exc
        except SpeechUnavailable as exc:
            raise ServiceUnavailable() from exc
        return request.make_json_response({"text": text}, headers=[("Cache-Control", "no-store")])
