# Notification productivity QA

Captured 14 October 2026 (Europe/Paris) after final-source tests, both affected
add-on upgrades and a QA server restart. The reviewer is a synthetic non-admin
internal user; all contacts, tasks and messages are disposable fixtures.

- `preferences-desktop.png`: native preferences after saving Email then In Odoo.
- `inbox-desktop.png`: two concise fixture messages with native task source links.

The native layout is retained. These images show final UI state; persisted saves,
Agent delivery, assignment filtering and exact source navigation are established
by the backend/browser assertions described in the PR, not by still images.
Both PNGs contain only IHDR, IDAT and IEND chunks; metadata sidecars stay private.
