# 2. Use the existing link-preview fallback for unknown charset names

Date: 2026-10-14
Status: accepted for qualification

## Context

Product Feedback 2747 reports a server error while sending a message with a file.
The failing optional preview decodes a remote HTML page using its advertised
`ISO-8852` charset. Python has no codec by that name and raises `LookupError`.
The native helper already handles invalid bytes and absent detector results by
falling back to UTF-8; it does not handle an unknown codec name.

## Decision

Add `LookupError` to the existing narrow decode fallback in
`addons/mail/tools/link_preview.py`. Preserve valid remote charsets, the current
UTF-8 error handling, stream behavior and native preview publication path.

An isolated addon would need to replace or patch the module-level helper imported
by native mail preview code. That creates more integration surface than adding
one exception to its existing fallback. A broad catch around preview publication
would also hide unrelated access or persistence failures. This distribution-level
bug fix uses neither approach.

## Consequences and upgrade cost

This adds a one-line native divergence, covered by native URL/HTML regressions and
an actual message/attachment publication regression. The fallback may omit bytes
that are not valid UTF-8, as it already does for invalid declared encodings; it
makes no claim to recover arbitrary damaged text.

During upstream synchronization, compare the helper's decode exception handling
and run these regressions. Remove the patch when upstream safely handles unknown
remote codec names through an equivalent fallback. Keep a regression that checks
valid non-UTF-8 text so a future simplification does not discard accents.

This change introduces no model, stored field, permission grant or external
write. All regression pages and attachments are invented offline fixtures.
