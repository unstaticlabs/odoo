# Purchase attachment review qualification

Captured on **24 October 2026** against runtime source **ed1a42f81858**. All screens use a disposable French-speaking billing user, invented company, generated PDFs and zero-value drafts. The user has native billing access and is not an administrator. The later evidence commit adds only these images and this receipt; it does not change runtime behavior.

The lead personally inspected every image and its embedded metadata before publication. Each JPEG contains only the standard JFIF marker, with no EXIF, XMP or comments. The short PDF thumbnail labels, names and avatar belong to the synthetic fixture. No production records, private contacts, credentials, business document contents, browser address bars or local paths are published.

## Screens

| Capture | Viewport | What is visible |
|---|---|---|
| [01: Entry](01-desktop-entry.jpg) | 1280 × 900 | Review remains available before the bill date is known. The missing-date styling belongs to the deliberately incomplete draft. |
| [02: Main choice](02-desktop-choices.jpg) | 1280 × 900 | One role per file; choosing a different main document clears the former main. |
| [03: Separate invoice](03-desktop-separation.jpg) | 1280 × 900 | Another invoice changes the action to creating a draft and explains retained originals. |
| [04: Blank draft](04-desktop-draft.jpg) | 1280 × 900 | The result starts with blank supplier, date, reference and invoice lines. This still alone does not establish that it is a distinct record. |
| [05: Phone choices](05-phone-choices.jpg) | 390 × 844 | Full filenames and role labels remain visible after editing on a phone; the long name wraps. |
| [06: Phone result](06-phone-draft.jpg) | 390 × 844 | Internal history links the source and explains completion before posting. |
| [07: Changed-file recovery](07-stale-recovery.jpg) | 1280 × 900 | A file changed after opening review; the native message explains closing and reopening. |
| [08: Provenance](08-draft-provenance.jpg) | 1280 × 900 | A copied file and source link appear in internal Chatter, with retained-original guidance. |

## Functional results

Still images establish appearance only. Separate executed browser and record checks established:

- Changing the main document leaves exactly one main choice. Confirmation closes review; Other Info can reopen it afterwards.
- Selecting Another invoice opens one new empty native draft. Repeating the same selection from a new review opens that same draft rather than creating another.
- The phone journey also creates a separate empty draft. After editing, its file list measures 358 px available / 358 px total, with zero horizontal scroll.
- Both source bills retain identical financial/header values, original file bytes and existing history. Result drafts retain the source company, journal and document type, have automatic posting disabled, and contain no supplier, financial lines or printable terms. Copies match original bytes; provenance is internal Chatter.
- Changing one explicitly disposable fixture file after review opens produces the recovery message. Closing the old review and opening a fresh one succeeds.

Native Chromium regression qualification passes **14 cases / 90 assertions**, including actual file-list geometry at 320 and 390 px and immediate confirmation during a delayed onchange. Source/backend, upgrade, financial-preservation, Agent and MCP qualification details are in [Odoo PR #349](https://github.com/unstaticlabs/odoo/pull/349) and [MCP PR #191](https://github.com/unstaticlabs/odoo-mcp/pull/191).

Independent design assessment: **31/36, Good**, no material open visual findings. Independent scoped XML detector: **0 findings**. The browser API supports read-only evaluation, so no mutable detector overlay was injected or claimed. Actual native browser journeys, screenshots and geometry supplied the runtime evidence. Keyboard and assistive-technology behavior are not established by the images.

No feature was merged or deployed during this qualification.
