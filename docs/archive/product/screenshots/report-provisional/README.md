# Provisional accounting PDF captures

Captured 17 October 2026 through ordinary native browser login, company
selection, Generate Export and download, against product revision
`b7e61982b77dbcf8e99960115377140f6310074a` and pinned renderer
`a01385982eb406947bd1e312a1cefd7fe4636943`. The companies, legal identity,
square logo and ledger are invented QA 2619 fixtures. Native company-logo
precedence is retained; the document-brand choice remains pending.

These are ordinary browser exports, so the optional shell qualification banner
is absent. They illustrate the final tested product revision; native tests,
PDF text checks and upgrade preservation separately prove behavior.

- `final-accountant-ledger-open-page4.png`: continuation of that eleven-page
  browser-generated ledger; the footer retains Document provisoire.
- `final-reader-trial-locked-page1.png`: native read-only accountant after
  switching to the locked company; no generated provisional label. This does
  not certify the report.

The first-page capture of the open ledger was removed: its preamble reason
predates the owner decision of 17 October 2026 that a report is provisional
until the global fiscal lock or hard lock covers its closing date. The status
itself is proved by `TestReportProvisionalStatus`; a fresh first-page capture
can be added with the next browser qualification.

The lead personally inspected the pixels and all PNG chunks of these exact
bytes before publication. These replace the earlier pre-format illustrative
captures. Registry-selector probes and candidate-renderer previews stay private.
No live company logo or historical file was replaced.

## Provisional notice merged into the entries box (18 October 2026)

Page 1 of a trial-balance PDF built by the real export wizard
(`rebuild.account.report.export.wizard._pdf_payload`) in an `odoo shell` on the
action-risk closure database, with invented data only: the company
`Atelier Exemple`, a flat square logo and four invented accounts. The wizard's
renderer payload was captured and compiled by the pinned renderer
`a01385982eb406947bd1e312a1cefd7fe4636943`, built from its submodule and run
through its compiler (`usl_document_renderer.compiler.render`), not through its
HTTPS service and not through a browser download. Everything was rolled back.

- `merged-notice-before-provisional-page1.png`: the previous layout, with
  `Document provisoire — …` as a cell of its own ahead of the company.
- `merged-notice-provisional-page1.png`: the notice now sits in the same box as
  `Écritures comptabilisées au 18/10/2026`, on the line below it.
- `merged-notice-provisional-drafts-page1.png`: draft-inclusive export
  (`Écritures comptabilisées et brouillons au …`); the longer reasons wrap inside
  the box.
- `merged-notice-final-page1.png`: a final report (global fiscal lock on the
  closing date), unchanged.

The footer keeps `Document provisoire` on every provisional page.

Only the delivering agent has inspected these four captures so far.
