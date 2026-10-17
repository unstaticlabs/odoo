# Expense posting review QA

Captured on 17 October 2026 against product source `cc5557cb21408f1713eec5703235fffa0c957188`. All people, companies, amounts and references are invented disposable fixtures. These French screenshots use an ordinary native expense manager in a standalone 98-module QA database. Canonical 164-module API and preservation qualification is separate.

1. `01-approved-before-post.jpg`: approved company expense before posting; no cached suggestions are needed.
2. `02-near-match-review.jpg`: current bank evidence explains a close amount and offers review, explicit posting or cancellation.
3. `03-company-posted.jpg`: native company posting returns automatically to the current Paid expense state.
4. `04-employee-native-controls.jpg`: employee-paid review retains the native purchase journal and accounting date.
5. `05-mobile-warning.jpg`: the same dialog at 390 × 844, with all three choices visible.
6. `06-employee-posted.jpg`: native employee posting opens the posted receipt with the chosen accounting date.

The operator exercised Cancel, Review expenses, company posting and employee posting through the native browser UI. No browser warnings or errors were captured. Functional assertions and financial receipts establish behavior; still images illustrate the interface only.

The implementation owner and lead personally inspected every selected image. JPEG metadata contains only standard JFIF APP0, with no EXIF, XMP, IPTC or comments. No credentials, real contacts, private business information, browser address bars or local filesystem paths appear.

Delegated Agent confirmation is qualified together with the native receipt-copy correction in PR #304. The combined canonical 164-module revision passes all 39 affected native tests, repeated update preservation, and actual JSON-2 no-match, explicit-confirmation, stale-evidence and replay controls. Its runtime-derived policy digest is `3a149380975accd5b5d87d17b4539dd55000b07b2452d4331d5cdc59b4ae2d78`. The two PRs remain separate review units; combine their source and runtime-derived policy when integrating.
