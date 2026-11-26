import os
import time

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


def transcribe(data, mimetype, lang=None, patience=0):
    """Return the spoken text. ``lang`` is the speaker's own language, used when unsure.

    A worker hears one recording at a time and answers 503 with Retry-After to the rest. ``patience`` is how
    many seconds to keep asking again before giving up.
    """
    mime = (mimetype or "").partition(";")[0].strip().lower()
    if mime not in FORMATS or not data or len(data) > MAX_AUDIO_BYTES:
        raise ValueError("Unsupported or empty audio")
    gateway = os.environ.get("USL_VOICE_GATEWAY", "http://ollama:11434").rstrip("/")
    deadline = time.monotonic() + patience

    def ask(language=None):
        # The gateway answers with the text and the language heard, and refuses any
        # field other than ``language``.
        fields = {"language": language} if language else {}
        try:
            while True:
                response = requests.post(
                    f"{gateway}/v1/audio/transcriptions",
                    files={"file": (f"voice.{FORMATS[mime]}", data, mime)}, data=fields,
                    timeout=(3, 120), allow_redirects=False,
                )
                # Only a busy worker says when to come back. A failed recognition or a
                # gateway with no worker awake would fail again: give up at once.
                pause = response.headers.get("Retry-After")
                if response.status_code != 503 or not isinstance(pause, str) or not pause.isdecimal():
                    break
                pause = min(max(int(pause), 1), 5)
                if time.monotonic() + pause > deadline:
                    break
                time.sleep(pause)
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
        try:
            text, heard = ask(LANGUAGES.get((lang or "")[:2].lower(), "fr"))
        except SpeechUnavailable:
            # Keep the words already heard rather than lose the recording.
            pass
    return text
