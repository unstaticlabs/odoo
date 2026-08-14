import io
import json

import requests
from bitcoin.core import CBlockHeader, b2lx
from opentimestamps.calendar import CommitmentNotFoundError
from opentimestamps.core.notary import (
    BitcoinBlockHeaderAttestation,
    PendingAttestation,
)
from opentimestamps.core.op import OpSHA256
from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp

from odoo.tests import TransactionCase, tagged

from ..services import (
    OpenTimestampsClient,
    OpenTimestampsRejectedError,
    OpenTimestampsUnavailableError,
)

CALENDARS = (
    "https://a.pool.opentimestamps.org",
    "https://b.pool.opentimestamps.org",
    "https://a.pool.eternitywall.com",
)
PENDING_URIS = {
    CALENDARS[0]: "https://alice.btc.calendar.opentimestamps.org",
    CALENDARS[1]: "https://bob.btc.calendar.opentimestamps.org",
    CALENDARS[2]: "https://finney.calendar.eternitywall.com",
}
EXPLORERS = (
    ("Blockstream", "https://blockstream.example/api"),
    ("mempool.space", "https://mempool.example/api"),
)


class FakeCalendar:
    def __init__(self, factory, url):
        self.factory = factory
        self.url = url

    def submit(self, commitment, timeout=None):
        del timeout
        if self.url in self.factory.offline:
            raise TimeoutError(self.url)
        self.factory.submissions.append((self.url, commitment))
        timestamp = Timestamp(commitment)
        timestamp.attestations.add(PendingAttestation(PENDING_URIS[self.url]))
        return timestamp

    def get_timestamp(self, commitment, timeout=None):
        del timeout
        if self.factory.upgrade_height is None:
            msg = "Still pending"
            raise CommitmentNotFoundError(msg)
        timestamp = Timestamp(commitment)
        timestamp.attestations.add(
            BitcoinBlockHeaderAttestation(self.factory.upgrade_height),
        )
        return timestamp


class FakeCalendarFactory:
    def __init__(self, *, offline=(), upgrade_height=None):
        self.offline = set(offline)
        self.upgrade_height = upgrade_height
        self.submissions = []

    def __call__(self, url):
        return FakeCalendar(self, url)


class FakeResponse:
    def __init__(self, content):
        self.content = content if isinstance(content, bytes) else str(content).encode()

    @staticmethod
    def raise_for_status():
        return None

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset : offset + chunk_size]

    @staticmethod
    def close():
        return None


class FakeExplorerSession:
    def __init__(self, responses, *, unavailable=()):
        self.responses = responses
        self.unavailable = set(unavailable)
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        if url in self.unavailable:
            raise requests.Timeout(url)
        return FakeResponse(self.responses[url])


def _client(factory, *, session=None):
    return OpenTimestampsClient(
        calendar_urls=CALENDARS,
        explorers=EXPLORERS,
        timeout=0.1,
        session=session,
        calendar_factory=factory,
    )


def _explorer_session(commitment, height, *, confirmations=6, disagreement=False, reorg=False):
    header = CBlockHeader(
        nVersion=2,
        hashPrevBlock=b"\x11" * 32,
        hashMerkleRoot=commitment,
        nTime=1_700_000_000,
        nBits=0x1D00FFFF,
        nNonce=42,
    )
    header_hex = header.serialize().hex()
    block_hash = b2lx(header.GetHash())
    responses = {}
    for index, (_name, base_url) in enumerate(EXPLORERS):
        explorer_header = header_hex
        if disagreement and index == 1:
            explorer_header = (b"\x00" * 80).hex()
        responses[f"{base_url}/block-height/{height}"] = block_hash
        responses[f"{base_url}/block/{block_hash}/header"] = explorer_header
        responses[f"{base_url}/block/{block_hash}/status"] = json.dumps(
            {"in_best_chain": not reorg},
        )
        responses[f"{base_url}/blocks/tip/height"] = str(height + confirmations - 1)
    return FakeExplorerSession(responses)


@tagged("post_install", "-at_install")
class TestOpenTimestampsProtocol(TransactionCase):
    def test_submission_uses_quorum_nonce_and_exact_document_binding(self):
        document = b"signed daily manifest"
        nonce = b"\x07" * 16
        factory = FakeCalendarFactory(offline={CALENDARS[2]})
        first = _client(factory).submit(document, nonce=nonce)
        second = _client(FakeCalendarFactory(offline={CALENDARS[2]})).submit(
            document,
            nonce=nonce,
        )

        self.assertEqual(first["calendar_count"], 2)
        self.assertEqual(first["receipt"], second["receipt"])
        self.assertEqual(len({commitment for _url, commitment in factory.submissions}), 1)
        self.assertNotEqual(factory.submissions[0][1], OpSHA256()(document))
        _client(factory)._deserialize(first["receipt"], document)
        with self.assertRaisesRegex(
            OpenTimestampsRejectedError,
            "does not cover this signed manifest",
        ):
            _client(factory)._deserialize(first["receipt"], b"different manifest")

    def test_submission_requires_two_calendars(self):
        factory = FakeCalendarFactory(offline={CALENDARS[1], CALENDARS[2]})
        with self.assertRaises(OpenTimestampsUnavailableError) as caught:
            _client(factory).submit(b"manifest", nonce=b"\x08" * 16)
        self.assertTrue(caught.exception.transient)
        self.assertEqual(caught.exception.code, "calendar_quorum_unavailable")

    def test_receipt_upgrade_and_six_confirmation_verification(self):
        document = b"signed daily manifest"
        height = 840_000
        factory = FakeCalendarFactory(upgrade_height=height)
        submitted = _client(factory).submit(document, nonce=b"\x09" * 16)
        upgraded = _client(factory).upgrade(submitted["receipt"], document)
        commitment = bytes.fromhex(upgraded["bitcoin_attestations"][0]["merkle_root"])

        report = _client(
            factory,
            session=_explorer_session(commitment, height, confirmations=6),
        ).verify(upgraded["receipt"], document)
        self.assertEqual(report["status"], "confirmed")
        self.assertEqual(report["confirmations"], 6)
        self.assertEqual(report["verification_mode"], "two-public-explorers")
        self.assertEqual(len(report["explorers"]), 2)

        pending = _client(
            factory,
            session=_explorer_session(commitment, height, confirmations=5),
        ).verify(upgraded["receipt"], document)
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(pending["confirmations"], 5)

    def test_explorer_disagreement_and_reorg_fail_closed(self):
        document = b"signed daily manifest"
        height = 840_001
        factory = FakeCalendarFactory(upgrade_height=height)
        submitted = _client(factory).submit(document, nonce=b"\x0a" * 16)
        upgraded = _client(factory).upgrade(submitted["receipt"], document)
        commitment = bytes.fromhex(upgraded["bitcoin_attestations"][0]["merkle_root"])

        with self.assertRaises(OpenTimestampsRejectedError) as disagreement:
            _client(
                factory,
                session=_explorer_session(commitment, height, disagreement=True),
            ).verify(upgraded["receipt"], document)
        self.assertEqual(disagreement.exception.code, "explorer_disagreement")

        with self.assertRaises(OpenTimestampsRejectedError) as reorg:
            _client(
                factory,
                session=_explorer_session(commitment, height, reorg=True),
            ).verify(upgraded["receipt"], document)
        self.assertEqual(reorg.exception.code, "bitcoin_reorg")

    def test_explorer_unavailable_invalid_header_and_oversized_response_fail_closed(self):
        document = b"signed daily manifest"
        height = 840_002
        factory = FakeCalendarFactory(upgrade_height=height)
        submitted = _client(factory).submit(document, nonce=b"\x0b" * 16)
        upgraded = _client(factory).upgrade(submitted["receipt"], document)
        commitment = bytes.fromhex(upgraded["bitcoin_attestations"][0]["merkle_root"])

        unavailable_session = _explorer_session(commitment, height)
        unavailable_url = f"{EXPLORERS[1][1]}/block-height/{height}"
        unavailable_session.unavailable.add(unavailable_url)
        with self.assertRaises(OpenTimestampsUnavailableError) as unavailable:
            _client(factory, session=unavailable_session).verify(
                upgraded["receipt"],
                document,
            )
        self.assertEqual(unavailable.exception.code, "explorer_unavailable")

        invalid_header_session = _explorer_session(commitment, height)
        block_hash = invalid_header_session.responses[
            f"{EXPLORERS[0][1]}/block-height/{height}"
        ]
        for _name, base_url in EXPLORERS:
            invalid_header_session.responses[f"{base_url}/block/{block_hash}/header"] = (
                b"\x00" * 80
            ).hex()
        with self.assertRaises(OpenTimestampsRejectedError) as invalid_header:
            _client(factory, session=invalid_header_session).verify(
                upgraded["receipt"],
                document,
            )
        self.assertEqual(invalid_header.exception.code, "bitcoin_header_invalid")

        oversized_session = _explorer_session(commitment, height)
        oversized_session.responses[f"{EXPLORERS[0][1]}/blocks/tip/height"] = (
            b"9" * 4097
        )
        with self.assertRaises(OpenTimestampsRejectedError) as oversized:
            _client(factory, session=oversized_session).verify(
                upgraded["receipt"],
                document,
            )
        self.assertEqual(oversized.exception.code, "explorer_response_invalid")

    def test_malformed_oversized_and_substituted_receipts_are_rejected(self):
        client = _client(FakeCalendarFactory())
        with self.assertRaises(OpenTimestampsRejectedError):
            client.upgrade(b"not-an-ots-receipt", b"manifest")
        with self.assertRaises(OpenTimestampsRejectedError) as oversized:
            client.upgrade(b"x" * (1024 * 1024 + 1), b"manifest")
        self.assertEqual(oversized.exception.code, "receipt_oversized")

        detached = DetachedTimestampFile.from_fd(OpSHA256(), io.BytesIO(b"manifest"))
        detached.timestamp.attestations.add(PendingAttestation("https://evil.example"))
        receipt = client._serialize(detached)
        with self.assertRaises(OpenTimestampsRejectedError) as substituted:
            client.upgrade(receipt, b"manifest")
        self.assertEqual(substituted.exception.code, "calendar_substitution")

    def test_endpoints_are_fixed_https_only(self):
        factory = FakeCalendarFactory()
        with self.assertRaises(OpenTimestampsRejectedError):
            OpenTimestampsClient(
                calendar_urls=("http://calendar.example", CALENDARS[0]),
                explorers=EXPLORERS,
                calendar_factory=factory,
            )
        with self.assertRaises(OpenTimestampsRejectedError) as unapproved:
            OpenTimestampsClient(
                calendar_urls=("https://calendar.example", CALENDARS[0]),
                explorers=EXPLORERS,
                calendar_factory=factory,
            )
        self.assertEqual(
            unapproved.exception.code,
            "calendar_endpoint_unapproved",
        )
        with self.assertRaises(OpenTimestampsRejectedError):
            OpenTimestampsClient(
                calendar_urls=CALENDARS,
                explorers=(EXPLORERS[0], EXPLORERS[0]),
                calendar_factory=factory,
            )
