# Provisional status on the report screen

Captured 17 October 2026 in a real browser against the running local stack
(Odoo `saas~19.3`, Rebuild `19.3.1.13.40`), signed in as an ordinary Accounting
Manager of an invented `[QA] Atelier Lumière` company. The company, accounts and
four posted entries are synthetic seed data, with no business data. The report is
the **Balance générale**, 1 January to 31 December 2026.

The status is derived from the lock dates, never set by hand: the same report was
captured, then the global fiscal-year lock was moved to 31 December 2026 and the
report was opened again.

| Capture | State |
| --- | --- |
| `01-desktop-provisional-open-period.png` | Open period, desktop width: yellow **PROVISOIRE** pill first among the scope pills, and a notice with the reason. |
| `02-desktop-provisional-two-reasons.png` | Same, with draft entries included: the notice lists two reasons. |
| `03-mobile-provisional-open-period.jpg` | Open period at 390 px wide: pill and notice wrap, no horizontal scroll. |
| `04-desktop-final-locked.png` | Lock on 31 December 2026: no pill and no notice. |
| `05-mobile-final-locked.jpg` | Locked, 390 px wide: nothing added. |

Desktop images are the 1280 px viewport scaled to 800 px; mobile images are at
twice the 390 px viewport. The rule itself (lock against the closing date, every
company, comparison period, drafts, no tax/sales/purchase locks, no exceptions) is
proved by `TestReportProvisionalStatus` and `TestReportProvisionalSurfaces`, not by
these images.
