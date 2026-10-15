# Notification productivity QA

Captured 15 October 2026, 13:40–13:41 Europe/Paris, in the Codex in-app browser
against tested combined product source `3ea1d10ef590bc4a5ca790e6b726edeb6e57720e`,
after two preservation-checked upgrades. These replace the 14 October captures.
The reviewer is a synthetic non-admin internal user; all contacts, tasks and
messages are disposable fixtures. Viewport: 1280 × 720.

- `preferences-desktop.png`: native Preferences reopened after saving Email then In Odoo.
- `inbox-desktop.png`: two synthetic Agent messages with native task source links.

The same preference round trip and exact task destination were exercised in the
browser. Backend tests establish Agent routing and assignment filtering; a still
image does not prove those behaviors. The historical wrong-destination report was
not reproduced, so no navigation repair is claimed.

The implementation owner and delivery lead personally inspected both images and
metadata before publication. PNGs contain only IHDR, IDAT and IEND chunks.
