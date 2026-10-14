# Draft bill posting and matching — synthetic QA

Captured 14 October 2026 from final tested product revision `cfced5c14930560065c1e68a31f3f1620e931d6f` in a disposable native Odoo database. Desktop uses 1280×900; mobile uses a 390×844 browser viewport. Mobile screenshots are browser emulation, not photographs of a phone.

All suppliers, invoice references, bank descriptions and amounts are invented QA fixtures. The delivery lead personally inspected all six final captures and parsed their embedded metadata before publication. JPEG metadata contains standard JFIF only, without EXIF, comments, embedded credentials, real contacts, business data, address bars or local paths. Private fixture keys, logs and metadata sidecars are not published.

- `post-match-confirmation-desktop.jpg` and `post-match-confirmation-mobile.jpg`: the review identifies the bill and chosen bank payment, explains posting and preserves Cancel.
- `post-match-native-warning-desktop.jpg` and `post-match-native-warning-mobile.jpg`: Odoo’s existing warning confirmation opens when invoice history requires it. Confirm and Discard stay native and readable on both viewports. The mobile capture is taken after the native opening animation completes.
- `post-match-result-desktop.jpg`: the natively posted and paid synthetic desktop bill with its total, bank payment and zero amount due.
- `post-match-result-mobile.jpg`: the natively posted and paid synthetic mobile bill, including its heading and supplier. Its lower totals are outside this viewport.

These stills illustrate layout. Native regressions and actual caller-scoped database readback separately prove one reconciliation for each confirmed desktop/mobile bill, unchanged bank facts, Draft and no match after Discard, and no match after cancelling a dialog when another native request posted the bill. No production mutation or external financial transaction occurred.
