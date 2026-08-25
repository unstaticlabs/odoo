import base64
import binascii
import json
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from paperless_personal_ai.models import PersonalAIProfile

MASTER_KEYS_FILE_ENV = "USL_PERSONAL_AI_MASTER_KEYS_PATH"
SECRET_FORMAT = "usl-paperless-personal-ai-keys-v1"
NONCE_BYTES = 12
DEK_BYTES = 32
KEY_SERVICE_UNAVAILABLE = "The personal AI key service is unavailable."
KEY_REQUIRED = "A personal Gemini API key is required."
KEY_OPEN_FAILED = "The personal Gemini credential could not be opened."
KEY_ROTATION_FAILED = "The personal Gemini credential could not be rotated."


class PersonalAIKeyServiceError(RuntimeError):
    """A deliberately non-secret failure from the key service."""


@dataclass(frozen=True)
class MasterKey:
    key_id: str
    version: int
    key: bytes


@dataclass(frozen=True)
class MasterKeyRing:
    active: MasterKey
    keys: dict[tuple[str, int], MasterKey]


@dataclass(frozen=True)
class EncryptedAPIKey:
    ciphertext: str
    nonce: str
    wrapped_dek: str
    wrapped_dek_nonce: str
    master_key_id: str
    master_key_version: int


def _decode(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise PersonalAIKeyServiceError(KEY_SERVICE_UNAVAILABLE) from exc


def _encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _parse_master_key_ring(raw) -> MasterKeyRing:
    if raw.get("format") != SECRET_FORMAT:
        raise ValueError
    active_id = str(raw["active_key_id"])
    active_version = int(raw["active_key_version"])
    items = raw["keys"]
    if not isinstance(items, list) or not items:
        raise ValueError
    keys = {}
    for item in items:
        key = MasterKey(
            key_id=str(item["id"]),
            version=int(item["version"]),
            key=_decode(str(item["key"])),
        )
        if (
            not key.key_id
            or key.version < 1
            or len(key.key) != DEK_BYTES
            or (key.key_id, key.version) in keys
        ):
            raise ValueError
        keys[key.key_id, key.version] = key
    active = keys[active_id, active_version]
    return MasterKeyRing(active=active, keys=keys)


def load_master_key_ring() -> MasterKeyRing:
    configured_path = os.environ.get(MASTER_KEYS_FILE_ENV, "").strip()
    if not configured_path:
        raise PersonalAIKeyServiceError(KEY_SERVICE_UNAVAILABLE)
    try:
        raw = json.loads(Path(configured_path).read_text(encoding="utf-8"))
        return _parse_master_key_ring(raw)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PersonalAIKeyServiceError(KEY_SERVICE_UNAVAILABLE) from exc


def _credential_aad(user_id: int, revision: int) -> bytes:
    return (
        f"usl:paperless:personal-ai:api-key:v1:user:{user_id}:revision:{revision}"
    ).encode()


def _dek_aad(user_id: int, revision: int, master_key: MasterKey) -> bytes:
    return (
        "usl:paperless:personal-ai:dek:v1:"
        f"user:{user_id}:revision:{revision}:"
        f"master:{master_key.key_id}:{master_key.version}"
    ).encode()


def encrypt_api_key(
    *,
    user_id: int,
    revision: int,
    api_key: str,
    key_ring: MasterKeyRing | None = None,
) -> EncryptedAPIKey:
    ring = key_ring or load_master_key_ring()
    dek = AESGCM.generate_key(bit_length=DEK_BYTES * 8)
    nonce = os.urandom(NONCE_BYTES)
    wrapped_dek_nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(dek).encrypt(
        nonce,
        api_key.encode("utf-8"),
        _credential_aad(user_id, revision),
    )
    wrapped_dek = AESGCM(ring.active.key).encrypt(
        wrapped_dek_nonce,
        dek,
        _dek_aad(user_id, revision, ring.active),
    )
    return EncryptedAPIKey(
        ciphertext=_encode(ciphertext),
        nonce=_encode(nonce),
        wrapped_dek=_encode(wrapped_dek),
        wrapped_dek_nonce=_encode(wrapped_dek_nonce),
        master_key_id=ring.active.key_id,
        master_key_version=ring.active.version,
    )


def decrypt_api_key(
    profile: PersonalAIProfile,
    *,
    key_ring: MasterKeyRing | None = None,
) -> str:
    if not profile.has_api_key:
        raise PersonalAIKeyServiceError(KEY_REQUIRED)
    ring = key_ring or load_master_key_ring()
    try:
        master_key = ring.keys[
            profile.master_key_id, profile.master_key_version,
        ]
        dek = AESGCM(master_key.key).decrypt(
            _decode(profile.wrapped_dek_nonce),
            _decode(profile.wrapped_dek),
            _dek_aad(profile.user_id, profile.credential_revision, master_key),
        )
        plaintext = AESGCM(dek).decrypt(
            _decode(profile.api_key_nonce),
            _decode(profile.api_key_ciphertext),
            _credential_aad(profile.user_id, profile.credential_revision),
        )
        api_key = plaintext.decode("utf-8")
    except (KeyError, UnicodeDecodeError, InvalidTag, ValueError) as exc:
        raise PersonalAIKeyServiceError(KEY_OPEN_FAILED) from exc
    return api_key


def rewrap_dek_if_needed(
    profile: PersonalAIProfile,
    *,
    key_ring: MasterKeyRing | None = None,
) -> None:
    ring = key_ring or load_master_key_ring()
    if not profile.has_api_key or (
        profile.master_key_id,
        profile.master_key_version,
    ) == (ring.active.key_id, ring.active.version):
        return
    try:
        old_key = ring.keys[profile.master_key_id, profile.master_key_version]
        dek = AESGCM(old_key.key).decrypt(
            _decode(profile.wrapped_dek_nonce),
            _decode(profile.wrapped_dek),
            _dek_aad(profile.user_id, profile.credential_revision, old_key),
        )
        nonce = os.urandom(NONCE_BYTES)
        wrapped = AESGCM(ring.active.key).encrypt(
            nonce,
            dek,
            _dek_aad(profile.user_id, profile.credential_revision, ring.active),
        )
    except (KeyError, InvalidTag, ValueError) as exc:
        raise PersonalAIKeyServiceError(KEY_ROTATION_FAILED) from exc
    updated = PersonalAIProfile.objects.filter(
        pk=profile.pk,
        master_key_id=profile.master_key_id,
        master_key_version=profile.master_key_version,
        credential_revision=profile.credential_revision,
    ).update(
        wrapped_dek=_encode(wrapped),
        wrapped_dek_nonce=_encode(nonce),
        master_key_id=ring.active.key_id,
        master_key_version=ring.active.version,
    )
    if updated:
        profile.wrapped_dek = _encode(wrapped)
        profile.wrapped_dek_nonce = _encode(nonce)
        profile.master_key_id = ring.active.key_id
        profile.master_key_version = ring.active.version
