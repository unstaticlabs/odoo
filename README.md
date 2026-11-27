# Linked record preview — native QA captures

Captured 27 November 2026 in French, using a disposable synthetic company and an ordinary internal user with Documents, Project and Billing access. The user has no administrator or Project Manager role. Desktop is 1440×900 CSS pixels (DPR 2); mobile is 390×844 (DPR 3), using real touch input.

Product revision: [7c9ca31b5bea](https://github.com/unstaticlabs/odoo/commit/7c9ca31b5bea086844fee14580a6075e1225615c). Final integration [7a726f5e28e0](https://github.com/unstaticlabs/odoo/commit/7a726f5e28e00f75090f1497ee78e44f712b9a4d) adds staging recovery fixes and has identical add-on bytes. These captures show that final product implementation, not an earlier illustrative UI.

| Image | Native journey shown |
| --- | --- |
| drawer-desktop.png | Closing the linked preview leaves the selected document and its Open record control available. |
| preview-return-mobile.png | The same document remains after dismissing the mobile preview with browser Back. |
| native-bank-form-mobile.png | Open record has loaded the native bank statement form. Its Reference identifies the invented QA statement. The empty-statement balance warning is native fixture state. |
| native-bank-return-mobile.png | The native form's Back button has returned to the same document. The drawer was then scrolled to the linked record for readability. |
| access-revoked-mobile.png | The preview no longer exposes the private project's name or fields after access withdrawal; the independently readable document remains open. |
| access-restored-mobile.png | Access has been restored and Retry has recovered the native preview after a deliberately stalled recheck expired. |

The archive stub is offline/partial, so thumbnail and archive-availability warnings are expected in this fixture. This slice qualifies the Odoo record journey independently of archive availability. No real bank, supplier, contact, purchase or financial data is shown.

All pixels were personally inspected before publication. PNG signatures and CRCs were validated; the files contain only IHDR, IDAT and IEND chunks. Private sidecars, credentials, browser traces and fixture IDs are excluded. Stills illustrate UI states; the native browser journeys and regression tests provide functional evidence.
