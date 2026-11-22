import os

import requests


MAX_AUDIO_BYTES = 16 * 1024 * 1024
FORMATS = {
    "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/webm": "webm",
    "audio/mp4": "m4a", "audio/ogg": "ogg", "audio/wav": "wav",
}


class SpeechUnavailable(Exception):
    pass


def transcribe(data, mimetype):
    mime = (mimetype or "").partition(";")[0].strip().lower()
    if mime not in FORMATS or not data or len(data) > MAX_AUDIO_BYTES:
        raise ValueError("Unsupported or empty audio")
    gateway = os.environ.get("USL_VOICE_GATEWAY", "http://ollama:11434").rstrip("/")
    try:
        response = requests.post(
            f"{gateway}/v1/audio/transcriptions",
            files={"file": (f"voice.{FORMATS[mime]}", data, mime)},
            timeout=(3, 120), allow_redirects=False,
        )
        if response.status_code != 200:
            raise SpeechUnavailable()
        text = response.json()["text"]
        if not isinstance(text, str):
            raise SpeechUnavailable()
        return text.strip()
    except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
        raise SpeechUnavailable() from exc
