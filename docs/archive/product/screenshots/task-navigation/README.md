# Task navigation QA captures

Captured 14 October 2026, Europe/Paris, from final tested product source
`3cd48b12eed23e3f7028a2207a0dbf489b40d031`. The later screenshot commit adds
only these images and this record. The QA database contains disposable
synthetic records; the actor is an ordinary Project user with Company A selected.
No production data or credentials are included.

Actual authenticated native interactions use the repository-supported headless
Chrome helper because in-app browser control was unavailable. Functional tours
and explicit browser assertions support the journeys; still images illustrate
the result. Mobile captures use a 390 × 844 viewport, not a physical phone.
The 1440 × 900 desktop captures and all mobile images retain native Odoo styling.

| Capture | Paris time | Viewport | What it illustrates |
|---|---|---|---|
| [milestone-desktop.png](milestone-desktop.png) | 01:20:37 | 1440x900 | Native milestone title and task IDs after reload. |
| [milestone-mobile.png](milestone-mobile.png) | 01:20:48 | 390x844 (mobile) | The same milestone context and task IDs in native mobile Kanban after reload. |
| [overdue-desktop.png](overdue-desktop.png) | 01:20:59 | 1440x900 | Home Overdue destination with Open and Overdue facets despite a default favorite. |
| [overdue-reload-mobile.png](overdue-reload-mobile.png) | 01:21:11 | 390x844 (mobile) | The expected Overdue task after reload. Native mobile search is collapsed; its hidden facet is not claimed as visible evidence. |
| [activities-desktop.png](activities-desktop.png) | 01:21:22 | 1440x900 | Native My Activities with Summary before Linked to, entered from Home. |

The native desktop reload restores metric population using a Shared domain
facet and personal-stage grouping. The mobile browser assertion checks the URL
domain, exact expected task ID/name and exclusion of Due soon, Later and the
other-company task. It does not require hidden desktop search markup.

Each PNG was personally inspected before publication for private data,
credentials, real contacts, financial/document details, address bars and local
paths. Metadata inspection found only IHDR, IDAT and IEND chunks; text, EXIF,
time and ancillary chunks are absent. The private capture sidecars stay outside
the repository. SHA-256 identities:

- `milestone-desktop.png`: `027fdf97d0191f4f7ad6e942df11bd7494948410b05f1c6e6789733212dc2cbc`
- `milestone-mobile.png`: `5aa8c0de95425809ac6463338b54087c134a5e428021022e7c449a8300025bf9`
- `overdue-desktop.png`: `012317ec2974d5251f7e304d2fe916cce30492fb1b945ebfddcd1394b8994195`
- `overdue-reload-mobile.png`: `8e192aed7d2ff2e6ed2f99032862a4e079c39debaa6937f30503e06d2a72f630`
- `activities-desktop.png`: `ed3cbc5773aab8f46ed8649ee7fdf16f81b08ef86c1063cd7df005524cc097c5`
