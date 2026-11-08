# Local attachment removal — 14 October 2026

Captured from final product source `8b5ea36ddce0922ce9e3738d5677464b6bd93013`, in a synthetic, isolated 164-module Odoo QA database using normal assets. The actor is a non-admin user with the existing Irreversible Actions capability. Desktop is 1280 × 720; mobile is 390 × 844. No production data or provider was used.

Both the Lead and implementation owner personally inspected all six published images. They contain invented task/file names and messages, with no private contacts, credentials, browser address bars or local paths. JPEG metadata contains JFIF only; no Exif, XMP, IPTC or comments. The unchanged image bytes match the approved hashes below.

| Image | Caption |
| --- | --- |
| `desktop-confirm.jpg` | Remove opens Odoo’s existing permanent-delete confirmation. Four files are still attached. |
| `desktop-cancelled.jpg` | Choosing Discard leaves all four files attached; Documents remains empty. |
| `mobile-confirm.jpg` | The same native confirmation fits the 390-pixel viewport with both actions reachable. |
| `mobile-cancelled.jpg` | Mobile Discard also preserves all four files. |
| `desktop-after-history.jpg` | After separate authorized native HTTP QA deletions of two disposable files, two control files remain, Documents is empty, and the original comment and notification remain visible. |
| `mobile-after-history.jpg` | The same final state and retained history on mobile. |

The final deletions were made through ordinary authenticated native HTTP QA, not a browser final-confirmation click. Functional receipts independently verify that only the two intended files were deleted, a user lacking the existing capability was denied, both controls retained their original bytes/checksums, and no archive operation was created for the task. The native comment receives its standard edited marker. Each actual deletion has two authorization checks, not two deletions. Local audit policy provenance is development `unverified`; the separately qualified source/runtime seal is not a release-image provenance claim.

These still images show layout and visible state; they do not establish functional behavior. The evidence-only publication commit does not change the tested product source.

| File | SHA-256 |
| --- | --- |
| `desktop-confirm.jpg` | `7cee4e741d7d66008b5cdc667ec9cc0447c5348382f1eddb9eec9532fb2adf9b` |
| `desktop-cancelled.jpg` | `fee5b01d314af49b3e1c77dc30801e595bc92b8b2c081e114f4d0fb30d8fa913` |
| `mobile-confirm.jpg` | `ac7bb9c5659aa9bb1b48711ab58ee1ae911353ddcc2d3530605a3628e3669047` |
| `mobile-cancelled.jpg` | `6f76fdfa41e1978a3204250c719602aeecb17ec7b464108c97102a53016ea7e9` |
| `desktop-after-history.jpg` | `19031507a31134a3cfaa61c686f03f23df877f35a12dd9ac9665a3b68714b5fc` |
| `mobile-after-history.jpg` | `2e4d640fab0f4fb029fabb959ded478627f90a367b732791a3f1f405f9e30cd5` |
