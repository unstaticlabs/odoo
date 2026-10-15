# Upstream synchronization visual evidence

Captured 15 October 2026 at 01:57 UTC on source
`7d8fe3b3915c22eb8d32ef24924a48bf13d9427f`.
Subsequent delivery commits add documentation and these images only.

The separate disposable database contains an invented company, supplier and
posted bill. The account has the ordinary Accounting Reviewer role, belongs only
to the synthetic company, and has no Irreversible Actions capability. Desktop is
1440 × 1000; mobile is 390 × 844. No production backup or provider was used.

- `desktop-posted-bill-journal-items.png`: the posted synthetic bill exposes the
  native Journal Items button.
- `desktop-linked-journal-items.png`: the button opens the two journal items
  belonging to that bill, with balanced debit and credit.
- `desktop-home-shell.png` and `mobile-home-shell.png`: the existing Home shell
  remains readable on the upgraded source at both breakpoints.

A separate executed browser assertion verifies the action model, its domain and
both rendered record IDs; the screenshots alone do not prove behavior. That
journey reported no page errors and no external origins.

The implementing reviewer and lead personally inspected every image before
publication: only invented QA labels and amounts are visible. There are no real
contacts, credentials, private documents, browser address bars or local paths.
The PNG files contain only IHDR, IDAT and IEND chunks; no text or EXIF metadata.
