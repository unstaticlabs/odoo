"""Opt-in live OpenTimestamps smoke using synthetic, non-confidential bytes."""

import hashlib
import json
import os
from datetime import UTC, datetime

from odoo.addons.usl_sign.services import OpenTimestampsClient

if os.getenv("USL_SIGN_OTS_LIVE_SMOKE") != "1":
    msg = "Set USL_SIGN_OTS_LIVE_SMOKE=1 to contact public proof services."
    raise RuntimeError(msg)

document = json.dumps(
    {
        "format": "usl-sign-opentimestamps-live-smoke-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "nonce": os.urandom(16).hex(),
        "synthetic": True,
    },
    sort_keys=True,
    separators=(",", ":"),
).encode()
client = OpenTimestampsClient()
submitted = client.submit(document)
upgraded = client.upgrade(submitted["receipt"], document)
verification = {"status": "pending", "confirmations": 0}
if upgraded["bitcoin_attestations"]:
    verification = client.verify(upgraded["receipt"], document)

print(  # noqa: T201 -- operator-facing smoke result
    json.dumps(
        {
            "calendar_count": submitted["calendar_count"],
            "document_sha256": hashlib.sha256(document).hexdigest(),
            "receipt_sha256": hashlib.sha256(upgraded["receipt"]).hexdigest(),
            "status": verification["status"],
            "confirmations": verification.get("confirmations", 0),
            "note": (
                "Awaiting confirmation is expected for a new submission; "
                "OpenTimestamps aggregation normally takes several hours."
            ),
        },
        sort_keys=True,
    ),
)
