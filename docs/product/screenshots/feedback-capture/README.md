# Quick feedback capture QA

Captured on 13 October 2026, 23:56–23:59 Europe/Paris, against tested product revision `f1bf8d7a106ad13aaf9fb4ea0dd429e4a0d370aa`.

The disposable local fixture uses a non-administrator reporter, synthetic companies and reports, and fixed local agent replies. No provider or MCP credentials were configured. Desktop viewport: 1280 × 720. Mobile viewport: 390 × 844.

| Capture | Visible state |
|---|---|
| palette-desktop.png | Feedback is the first selected native palette suggestion. |
| draft-desktop.png | Editable report, pasted image, sharing choices and compact preview controls. |
| modal-desktop.png | Feedback remains usable above an existing Preferences dialog. |
| ready-desktop.png | Review before submitting the desktop report. |
| sent-desktop.png | Desktop report reaches Triage. |
| draft-mobile.png | Narrow-screen report with sharing controls before the preview. |
| ready-mobile.png | Initial reply is ready; textarea focus was released. |
| reply-mobile.png | Follow-up reply is ready; composer focus was released. |
| sent-mobile.png | Narrow-screen report reaches Triage. |

Both the implementation owner and delivery lead inspected all nine images for sensitive pixels. Each PNG contains only IHDR, IDAT and IEND chunks. These images illustrate the final tested interface; functional behavior is demonstrated separately by browser interactions and native tests. A narrow viewport and focus assertions do not establish physical-device keyboard behavior.
