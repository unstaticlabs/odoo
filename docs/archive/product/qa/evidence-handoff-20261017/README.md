# Evidence handoff: synthetic QA captures

Captured 17 October 2026 in the Codex in-app browser, using an ordinary local
expense/accounting reviewer and a delegated Agent in a disposable database.
Every record, person, company, amount and PDF in these captures is synthetic.
Desktop is 1280×720; mobile is 390×844. The product source is the final tested
implementation in this PR; no older illustrative captures are included.

| Capture | Journey and observable state |
| --- | --- |
| 01-before-receipt.jpg | Known draft expense before upload; its native receipt warning is present. |
| 02-attachment-and-audit.jpg | The uploaded file appears in native attachments with an attributed internal audit note. |
| 03-original-pdf-preview.jpg | The exact synthetic PDF opens in Odoo’s native viewer. |
| 04-ready-with-native-duplicate-warning.jpg | Receipt readiness updates while the expense stays Draft. Native duplicate-receipt guidance remains visible. |
| 05-copy-keeps-source-reference.jpg | A separate destination retains the original attachment reference in its copy audit note. |
| 06-mobile-receipt-ready.jpg | Native readiness and record details reflow at 390 px. |
| 07-mobile-attachment-and-audit.jpg | Native attachment access and the full checksum attribution wrap on mobile. |

The Agent uses an English locale, so its stored chatter notes are in English;
the reviewer uses French. New feature messages also have maintained French
translations. Some inherited labels in the base release remain English.

The archive badge is pending because outbound jobs are disabled. These images
do not claim completed external archiving, submission, posting or payment. The
native duplicate warning is expected because the same synthetic PDF is attached
to multiple records during the copy/deduplication tests.

The ordinary reviewer opened both the copied destination and the still-readable
source, previewed the original on desktop and mobile, navigated Back and checked
basic keyboard focus. The page width stayed 390 px on mobile. One PDF.js scroll
diagnostic (`offsetParent is not set -- cannot scroll`) was captured while the
PDF remained visible and usable; no RPC error was observed. No baseline cause
is claimed. This change adds no browser code.

Screenshots show the rendered states, not functional or access-control proof.
Native tests, real SDK/native HTTP journeys, byte-for-byte readback and
upgrade-preservation receipts provide that evidence. Every selected image was
personally inspected for private content and credentials before copying. JPEG
segments contain only standard JFIF APP0 metadata; no EXIF, XMP, IPTC or comments
were retained. Original capture and publication bytes match.
