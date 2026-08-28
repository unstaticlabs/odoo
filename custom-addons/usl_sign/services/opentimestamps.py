import hashlib
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from urllib.parse import urlparse

import requests
from bitcoin.core import CBlockHeader, b2lx
from opentimestamps.calendar import CommitmentNotFoundError, RemoteCalendar
from opentimestamps.core.notary import (
    BitcoinBlockHeaderAttestation,
    PendingAttestation,
    VerificationError,
)
from opentimestamps.core.op import OpAppend, OpSHA256
from opentimestamps.core.serialize import (
    DeserializationError,
    StreamDeserializationContext,
    StreamSerializationContext,
)
from opentimestamps.core.timestamp import DetachedTimestampFile

DEFAULT_CALENDAR_POOLS = (
    "https://a.pool.opentimestamps.org",
    "https://b.pool.opentimestamps.org",
    "https://a.pool.eternitywall.com",
    "https://ots.btc.catallaxy.com",
)
DEFAULT_PENDING_CALENDAR_SUFFIXES = (
    ".calendar.opentimestamps.org",
    ".calendar.eternitywall.com",
    ".calendar.catallaxy.com",
)
DEFAULT_PENDING_CALENDAR_HOSTS = ("ots.btc.catallaxy.com",)
DEFAULT_EXPLORERS = (
    ("Blockstream", "https://blockstream.info/api"),
    ("mempool.space", "https://mempool.space/api"),
)
MAX_RECEIPT_BYTES = 1024 * 1024
MAX_EXPLORER_RESPONSE_BYTES = 4096


class OpenTimestampsError(RuntimeError):
    code = "opentimestamps_error"
    transient = False

    def __init__(self, message, *, code=None, transient=None):
        super().__init__(message)
        if code:
            self.code = code
        if transient is not None:
            self.transient = transient


class OpenTimestampsUnavailableError(OpenTimestampsError):
    code = "opentimestamps_unavailable"
    transient = True


class OpenTimestampsRejectedError(OpenTimestampsError):
    code = "opentimestamps_rejected"


class OpenTimestampsClient:
    """Create portable OTS receipts and verify Bitcoin attestations.

    Calendar servers receive only nonce-protected commitment digests. Public
    explorers are used only to obtain the same Bitcoin header from two
    independent endpoints; all digest, header and attestation checks happen
    locally.
    """

    def __init__(
        self,
        *,
        calendar_urls=None,
        explorers=None,
        timeout=None,
        session=None,
        calendar_factory=None,
    ):
        configured_calendars = calendar_urls or self._env_urls(
            "USL_SIGN_OTS_CALENDARS",
            DEFAULT_CALENDAR_POOLS,
        )
        self.calendar_urls = tuple(
            self._validated_https_url(url, allow_path=False)
            for url in configured_calendars
        )
        if not set(self.calendar_urls).issubset(DEFAULT_CALENDAR_POOLS):
            msg = "Only the reviewed official OpenTimestamps calendar pools are allowed."
            raise OpenTimestampsRejectedError(
                msg,
                code="calendar_endpoint_unapproved",
            )
        if len(set(self.calendar_urls)) < 2:
            msg = "At least two distinct OpenTimestamps calendars are required."
            raise OpenTimestampsRejectedError(
                msg,
                code="calendar_quorum_invalid",
            )
        configured_explorers = explorers or self._env_explorers()
        if len(configured_explorers) != 2:
            msg = "Exactly two Bitcoin explorer endpoints are required."
            raise OpenTimestampsRejectedError(
                msg,
                code="explorer_quorum_invalid",
            )
        self.explorers = tuple(
            (str(name), self._validated_https_url(url, allow_path=True))
            for name, url in configured_explorers
        )
        if len({url for _name, url in self.explorers}) != 2:
            msg = "The Bitcoin explorer endpoints must be distinct."
            raise OpenTimestampsRejectedError(
                msg,
                code="explorer_quorum_invalid",
            )
        self.timeout = float(timeout or os.getenv("USL_SIGN_OTS_TIMEOUT", "5"))
        self.session = session or requests.Session()
        self.calendar_factory = calendar_factory or (
            lambda url: RemoteCalendar(url, user_agent="USL-Sign-OpenTimestamps/1")
        )

    @staticmethod
    def _env_urls(name, defaults):
        value = os.getenv(name, "")
        return tuple(item.strip() for item in value.split(",") if item.strip()) or defaults

    @classmethod
    def _env_explorers(cls):
        urls = cls._env_urls(
            "USL_SIGN_OTS_EXPLORERS",
            tuple(url for _name, url in DEFAULT_EXPLORERS),
        )
        default_names = [name for name, _url in DEFAULT_EXPLORERS]
        return tuple(
            (
                default_names[index] if index < len(default_names) else f"Explorer {index + 1}",
                url,
            )
            for index, url in enumerate(urls)
        )

    @staticmethod
    def _validated_https_url(value, *, allow_path):
        parsed = urlparse(str(value).strip())
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.port not in (None, 443)
            or (not allow_path and parsed.path not in ("", "/"))
        ):
            msg = "OpenTimestamps endpoints must be fixed HTTPS URLs."
            raise OpenTimestampsRejectedError(
                msg,
                code="endpoint_invalid",
            )
        return value.rstrip("/")

    @staticmethod
    def _serialize(detached):
        output = io.BytesIO()
        detached.serialize(StreamSerializationContext(output))
        data = output.getvalue()
        if not data or len(data) > MAX_RECEIPT_BYTES:
            msg = "The OpenTimestamps receipt exceeds the accepted size."
            raise OpenTimestampsRejectedError(
                msg,
                code="receipt_oversized",
            )
        return data

    @staticmethod
    def _deserialize(receipt, document):
        if not receipt or len(receipt) > MAX_RECEIPT_BYTES:
            msg = "The OpenTimestamps receipt is empty or oversized."
            raise OpenTimestampsRejectedError(
                msg,
                code="receipt_oversized",
            )
        try:
            detached = DetachedTimestampFile.deserialize(
                StreamDeserializationContext(io.BytesIO(receipt)),
            )
        except (DeserializationError, ValueError, TypeError) as error:
            msg = "The OpenTimestamps receipt is malformed."
            raise OpenTimestampsRejectedError(
                msg,
                code="receipt_malformed",
            ) from error
        if not isinstance(detached.file_hash_op, OpSHA256):
            msg = "The OpenTimestamps receipt does not use SHA-256."
            raise OpenTimestampsRejectedError(
                msg,
                code="receipt_algorithm_mismatch",
            )
        expected = hashlib.sha256(document).digest()
        if detached.file_digest != expected:
            msg = "The OpenTimestamps receipt does not cover this signed manifest."
            raise OpenTimestampsRejectedError(
                msg,
                code="receipt_digest_mismatch",
            )
        return detached

    @staticmethod
    def _walk(timestamp):
        yield timestamp
        for child in timestamp.ops.values():
            yield from OpenTimestampsClient._walk(child)

    @staticmethod
    def _pending_uri_allowed(uri):
        parsed = urlparse(uri)
        host = (parsed.hostname or "").lower()
        return bool(
            parsed.scheme == "https"
            and parsed.port in (None, 443)
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and parsed.path in ("", "/")
            and (
                host in DEFAULT_PENDING_CALENDAR_HOSTS
                or any(host.endswith(suffix) for suffix in DEFAULT_PENDING_CALENDAR_SUFFIXES)
            ),
        )

    @classmethod
    def _check_pending_calendars(cls, detached):
        for _message, attestation in detached.timestamp.all_attestations():
            if isinstance(attestation, PendingAttestation) and not cls._pending_uri_allowed(
                attestation.uri,
            ):
                msg = "The receipt refers to an unapproved calendar."
                raise OpenTimestampsRejectedError(
                    msg,
                    code="calendar_substitution",
                )

    def submit(self, document, *, nonce=None):
        detached = DetachedTimestampFile.from_fd(OpSHA256(), io.BytesIO(document))
        nonce = nonce or os.urandom(16)
        if len(nonce) != 16:
            msg = "The OpenTimestamps privacy nonce must contain 16 bytes."
            raise OpenTimestampsRejectedError(
                msg,
                code="nonce_invalid",
            )
        merkle_tip = detached.timestamp.ops.add(OpAppend(nonce)).ops.add(OpSHA256())
        successes = []

        def submit_one(url):
            timestamp = self.calendar_factory(url).submit(
                merkle_tip.msg,
                timeout=self.timeout,
            )
            if timestamp.msg != merkle_tip.msg:
                msg = "A calendar returned a receipt for another commitment."
                raise OpenTimestampsRejectedError(
                    msg,
                    code="calendar_commitment_mismatch",
                )
            return url, timestamp

        with ThreadPoolExecutor(max_workers=min(4, len(self.calendar_urls))) as executor:
            futures = {
                executor.submit(submit_one, url): url for url in self.calendar_urls
            }
            for future in as_completed(futures):
                try:
                    successes.append(future.result())
                except OpenTimestampsRejectedError:
                    raise
                except Exception:  # noqa: BLE001 -- multiple transports
                    continue
        if len(successes) < 2:
            msg = "Fewer than two OpenTimestamps calendars accepted the commitment."
            raise OpenTimestampsUnavailableError(
                msg,
                code="calendar_quorum_unavailable",
            )
        for _url, timestamp in successes:
            merkle_tip.merge(timestamp)
        self._check_pending_calendars(detached)
        return {
            "receipt": self._serialize(detached),
            "calendar_count": len(successes),
            "calendars": sorted(url for url, _timestamp in successes),
        }

    def upgrade(self, receipt, document):
        detached = self._deserialize(receipt, document)
        self._check_pending_calendars(detached)
        changed = False
        for timestamp in tuple(self._walk(detached.timestamp)):
            pending = tuple(
                attestation
                for attestation in timestamp.attestations
                if isinstance(attestation, PendingAttestation)
            )
            for attestation in pending:
                try:
                    upgraded = self.calendar_factory(attestation.uri).get_timestamp(
                        timestamp.msg,
                        timeout=self.timeout,
                    )
                except CommitmentNotFoundError:
                    continue
                except Exception:  # noqa: BLE001 -- outage leaves proof pending
                    continue
                if upgraded.msg != timestamp.msg:
                    msg = "A calendar upgrade covers another commitment."
                    raise OpenTimestampsRejectedError(
                        msg,
                        code="calendar_commitment_mismatch",
                    )
                before = len(tuple(timestamp.all_attestations()))
                timestamp.merge(upgraded)
                changed = changed or len(tuple(timestamp.all_attestations())) > before
        self._check_pending_calendars(detached)
        attestations = sorted(
            {
                (attestation.height, message)
                for message, attestation in detached.timestamp.all_attestations()
                if isinstance(attestation, BitcoinBlockHeaderAttestation)
            },
            key=lambda item: item[0],
        )
        return {
            "receipt": self._serialize(detached),
            "changed": changed,
            "bitcoin_attestations": [
                {"height": height, "merkle_root": message.hex()}
                for height, message in attestations
            ],
        }

    def _explorer_content(self, base_url, path, accept):
        response = None
        try:
            response = self.session.get(
                f"{base_url}/{path.lstrip('/')}",
                timeout=self.timeout,
                allow_redirects=False,
                stream=True,
                headers={"Accept": accept, "User-Agent": "USL-Sign/1"},
            )
            response.raise_for_status()
        except requests.RequestException as error:
            msg = "A Bitcoin explorer is unavailable."
            raise OpenTimestampsUnavailableError(
                msg,
                code="explorer_unavailable",
            ) from error
        chunks = []
        size = 0
        try:
            for chunk in response.iter_content(chunk_size=1024):
                size += len(chunk)
                if size > MAX_EXPLORER_RESPONSE_BYTES:
                    msg = "A Bitcoin explorer returned an invalid response size."
                    raise OpenTimestampsRejectedError(
                        msg,
                        code="explorer_response_invalid",
                    )
                chunks.append(chunk)
        except requests.RequestException as error:
            msg = "A Bitcoin explorer is unavailable."
            raise OpenTimestampsUnavailableError(
                msg,
                code="explorer_unavailable",
            ) from error
        finally:
            if response is not None:
                response.close()
        content = b"".join(chunks)
        if not content:
            msg = "A Bitcoin explorer returned an invalid response size."
            raise OpenTimestampsRejectedError(
                msg,
                code="explorer_response_invalid",
            )
        return content

    def _explorer_text(self, base_url, path):
        content = self._explorer_content(base_url, path, "text/plain")
        try:
            return content.decode("ascii").strip()
        except UnicodeDecodeError as error:
            msg = "A Bitcoin explorer returned non-ASCII proof data."
            raise OpenTimestampsRejectedError(
                msg,
                code="explorer_response_invalid",
            ) from error

    def _explorer_json(self, base_url, path):
        try:
            value = json.loads(
                self._explorer_content(base_url, path, "application/json"),
            )
        except (UnicodeDecodeError, ValueError, TypeError) as error:
            msg = "A Bitcoin explorer returned malformed proof data."
            raise OpenTimestampsRejectedError(
                msg,
                code="explorer_response_invalid",
            ) from error
        if not isinstance(value, dict):
            msg = "A Bitcoin explorer returned malformed proof data."
            raise OpenTimestampsRejectedError(msg, code="explorer_response_invalid")
        return value

    @staticmethod
    def _block_hash(value):
        normalized = str(value).strip().lower()
        if len(normalized) != 64:
            msg = "A Bitcoin explorer returned an invalid block hash."
            raise OpenTimestampsRejectedError(msg, code="bitcoin_header_invalid")
        try:
            bytes.fromhex(normalized)
        except ValueError as error:
            msg = "A Bitcoin explorer returned an invalid block hash."
            raise OpenTimestampsRejectedError(
                msg,
                code="bitcoin_header_invalid",
            ) from error
        return normalized

    @staticmethod
    def _block_header(value):
        normalized = str(value).strip().lower()
        try:
            header = bytes.fromhex(normalized)
        except ValueError as error:
            msg = "The Bitcoin block header is malformed."
            raise OpenTimestampsRejectedError(
                msg,
                code="bitcoin_header_invalid",
            ) from error
        if len(header) != 80:
            msg = "The Bitcoin block header is malformed."
            raise OpenTimestampsRejectedError(msg, code="bitcoin_header_invalid")
        return normalized, header

    def verify(self, receipt, document, *, minimum_confirmations=6):
        detached = self._deserialize(receipt, document)
        candidates = sorted(
            (
                (attestation.height, message, attestation)
                for message, attestation in detached.timestamp.all_attestations()
                if isinstance(attestation, BitcoinBlockHeaderAttestation)
            ),
            key=lambda item: item[0],
        )
        if not candidates:
            return {"status": "pending", "confirmations": 0}
        last_error = None
        for height, commitment, attestation in candidates:
            observations = []
            try:
                for name, base_url in self.explorers:
                    block_hash = self._block_hash(
                        self._explorer_text(base_url, f"block-height/{height}"),
                    )
                    header_hex, _header_bytes = self._block_header(
                        self._explorer_text(base_url, f"block/{block_hash}/header"),
                    )
                    status = self._explorer_json(base_url, f"block/{block_hash}/status")
                    tip_height = int(self._explorer_text(base_url, "blocks/tip/height"))
                    if status.get("in_best_chain") is not True:
                        msg = "The attested Bitcoin block is not in the best chain."
                        raise OpenTimestampsRejectedError(
                            msg,
                            code="bitcoin_reorg",
                        )
                    observations.append(
                        {
                            "name": name,
                            "url": base_url,
                            "block_hash": block_hash.lower(),
                            "header_hex": header_hex.lower(),
                            "tip_height": tip_height,
                        },
                    )
                if (
                    observations[0]["block_hash"] != observations[1]["block_hash"]
                    or observations[0]["header_hex"] != observations[1]["header_hex"]
                ):
                    msg = "The Bitcoin explorers disagree on the attested block."
                    raise OpenTimestampsRejectedError(
                        msg,
                        code="explorer_disagreement",
                    )
                _header_hex, header_bytes = self._block_header(
                    observations[0]["header_hex"],
                )
                header = CBlockHeader.deserialize(header_bytes)
                calculated_hash = b2lx(header.GetHash())
                if calculated_hash != observations[0]["block_hash"]:
                    msg = "The Bitcoin block header hash is invalid."
                    raise OpenTimestampsRejectedError(
                        msg,
                        code="bitcoin_header_invalid",
                    )
                attested_time = attestation.verify_against_blockheader(
                    commitment,
                    header,
                )
            except OpenTimestampsError as error:
                last_error = error
                continue
            except (ValueError, TypeError, DeserializationError, VerificationError):
                last_error = OpenTimestampsRejectedError(
                    "The Bitcoin block header is malformed.",
                    code="bitcoin_header_invalid",
                )
                continue
            confirmations = min(item["tip_height"] for item in observations) - height + 1
            if confirmations < 1:
                msg = "A Bitcoin explorer reported an impossible chain height."
                raise OpenTimestampsRejectedError(
                    msg,
                    code="bitcoin_height_invalid",
                )
            report = {
                "format": "usl-sign-opentimestamps-verification-v1",
                "verification_mode": "two-public-explorers",
                "proof_sha256": hashlib.sha256(receipt).hexdigest(),
                "document_sha256": hashlib.sha256(document).hexdigest(),
                "bitcoin_block_height": height,
                "bitcoin_block_hash": calculated_hash,
                "bitcoin_block_header": observations[0]["header_hex"],
                "bitcoin_block_time": datetime.fromtimestamp(
                    attested_time,
                    tz=UTC,
                ).isoformat(),
                "confirmations": confirmations,
                "minimum_confirmations": minimum_confirmations,
                "explorers": [
                    {
                        "name": item["name"],
                        "url": item["url"],
                        "block_hash": item["block_hash"],
                        "header_sha256": hashlib.sha256(
                            bytes.fromhex(item["header_hex"]),
                        ).hexdigest(),
                        "tip_height": item["tip_height"],
                    }
                    for item in observations
                ],
            }
            report["status"] = (
                "confirmed" if confirmations >= minimum_confirmations else "pending"
            )
            return report
        if last_error:
            raise last_error
        return {"status": "pending", "confirmations": 0}
