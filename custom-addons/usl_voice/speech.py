import os

import requests


MAX_AUDIO_BYTES = 16 * 1024 * 1024
FORMATS = {
    "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/webm": "webm",
    "audio/mp4": "m4a", "audio/ogg": "ogg", "audio/wav": "wav",
}


# People here speak French or English. Left alone, the recognizer sometimes hears a
# short or noisy recording as another language and writes nonsense in it.
LANGUAGES = {"fr": "fr", "french": "fr", "en": "en", "english": "en"}


class SpeechUnavailable(Exception):
    pass


def transcribe(data, mimetype, lang=None):
    """Return the spoken text. ``lang`` is the speaker's own language, used when unsure."""
    mime = (mimetype or "").partition(";")[0].strip().lower()
    if mime not in FORMATS or not data or len(data) > MAX_AUDIO_BYTES:
        raise ValueError("Unsupported or empty audio")
    gateway = os.environ.get("USL_VOICE_GATEWAY", "http://ollama:11434").rstrip("/")

    def ask(language=None):
        # The gateway answers with the text and the language heard, and refuses any
        # field other than ``language``.
        fields = {"language": language} if language else {}
        try:
            response = requests.post(
                f"{gateway}/v1/audio/transcriptions",
                files={"file": (f"voice.{FORMATS[mime]}", data, mime)}, data=fields,
                timeout=(3, 120), allow_redirects=False,
            )
            if response.status_code != 200:
                raise SpeechUnavailable()
            payload = response.json()
            text = payload["text"]
            if not isinstance(text, str):
                raise SpeechUnavailable()
            return text.strip(), payload.get("language")
        except (requests.RequestException, KeyError, TypeError, ValueError, AttributeError) as exc:
            raise SpeechUnavailable() from exc

    text, heard = ask()
    if text and isinstance(heard, str) and heard.strip().lower() not in LANGUAGES:
        # Heard as a third language: listen again in the speaker's own, French by default.
        text, heard = ask(LANGUAGES.get((lang or "")[:2].lower(), "fr"))
    return text
