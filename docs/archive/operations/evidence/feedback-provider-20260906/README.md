# Feedback provider and preview qualification

Captured and tested on 2026-09-06 in a disposable local Odoo database, on
`codex/fix-feedback-provider-and-capture`. No production code, configuration or
business records were changed.

## Results

- 53 focused backend tests pass, including separate source/MCP/draft phases,
  read-tool trace verification, safe diagnostics, image ownership and cutoffs,
  withdrawal cleanup, HTTP 400 fallback and HTTP 429 polling recovery.
- Three real-browser tests pass: desktop and mobile conversation journeys,
  plus a 5120×1440 capture with 50,000-pixel overflowing content and a
  20,000-pixel canvas. The wide capture produces a nonblank 1920×540 JPEG;
  excluded private elements stay excluded, and image-decode rejection settles.
- All 23 focused frontend unit tests pass.
- Repeated upgrades of `usl_feedback` pass on the disposable database.
- The product/migration source boundary passes. The full database boundary
  check does not qualify this fixture: 16 other product modules are not installed.
- Live synthetic checks successfully exercised structured generation and the
  corrected Gemini-to-MCP project lookup. A separate background final-reply
  probe encountered HTTP 429 responses and returned no text within its bounded
  polling window. Full live multi-phase completion remains to be qualified;
  mocked tests are not evidence of provider availability.

## Screenshots

[Desktop](desktop.jpg) (1440×900) and [mobile](mobile.jpg) (390×844) show the
final frontend revision with a synthetic reporter, an empty feedback board and
the deterministic local assistant. Manual capture at 2560×1440 also produced
a 1920×1080 preview. Browser warning/error logs were empty.

Both JPEGs were personally inspected before publication. They contain no real
contacts, financial records, document contents, credentials, browser address
bars or local paths. Metadata contains only JFIF information, with no EXIF.
These are layout and preview illustrations, not proof of Gemini execution.

## Release follow-up

Upgrade `usl_feedback` to `saas~19.3.2.0.16` through the normal release path.
On a qualified non-neutralized staging environment, run Settings → Test
connection and finish a synthetic feedback conversation with source lookup,
MCP and an attached image. Confirm rate-limit recovery and task preservation
before production promotion. Production has not received this fix.
