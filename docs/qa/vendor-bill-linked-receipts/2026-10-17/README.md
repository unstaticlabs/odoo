# Supplier-bill document recovery QA

Captured 17 October 2026 in isolated, disposable Odoo QA with an ordinary French invoicing persona, invented suppliers/bills and a clearly labelled synthetic PDF. Desktop is 1280 × 720; mobile is 390 × 844. Provider and fetcher interactions use local synthetic fixtures.

Files 10–12 show the final tested picker at `b042599bacbfbbf9f23fdfe1e0e1ba33ca65ea66`: desktop choices, visible native mobile card actions and the pending state after an actual mobile choice. Original English link wording comes from the invented supplier email.

Files 03–07 are **earlier illustrative qualification captures**, taken the same day at `d3bf34a2af9c252dbf5c930bd670354802eb0dbf`, before picker-only wording, translation and mobile-card changes. The displayed native bill form, upload callback, retained PDF and source-change recovery projection remain unchanged in the final source. These images cover the native original preview, sign-in recovery, retained manual upload, and desktop/mobile changed-source warnings.

Both the lead and implementation owner personally inspected every published image and its metadata. Only standard JFIF metadata is present. The exact file hashes and capture revisions are recorded in [manifest.json](manifest.json). Earlier rejected or superseded captures are excluded.

Stills illustrate the interface; they do not prove transitions. Native and Chromium tests, actual browser selection/upload/reload journeys, JSON2 calls and persisted native queue-worker receipts establish behavior. The final mobile choice was independently read back as the exact second source fingerprint, then processed through native `Job.load`: one original PDF/main attachment, attributable actor, retained email and unchanged blank bill date/financial projection.

Private synthetic qualification receipt manifest SHA-256: `1a3708453e4078ca60b34ad732ec7bed3090d4e30430523c7a40cc924ec921ed`. Raw receipts remain private.
