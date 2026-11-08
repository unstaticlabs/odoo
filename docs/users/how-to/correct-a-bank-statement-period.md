---
title: Correct the period of an imported bank statement
type: how-to
description: When an imported bank export covers other dates than Odoo read, set the right period and say why, so the import can continue and the history shows the correction.
lang: en
persona: accountant
app: Accounting
models:
  - account.bank.ingestion
screen: /odoo/action-usl_accounting.action_bank_ingestion
journey: correct-a-bank-statement-period
tour: usl_bank_period_correction
source:
  - custom-addons/usl_accounting/tests/test_accounting_journeys.py
screenshot_01-correct_png: 966c15364beaae84f47dc6372653825933384a1fa1da6323338701237e0807e3
screenshot_02-reason_png: a395919b6292728e5fe6328a518da2bf3950cd27c45948adbc8259913bd21097
screenshot_03-corrected_png: d9aa81b4c704d1ad2c4285955c7f017143e1b1848fce9524fc8e1428e0adcbb7
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Correct the period of an imported bank statement

When an imported bank export covers other dates than Odoo read, set the right period and say why, so the import can continue and the history shows the correction.

1. Open **Accounting > Bank > Imports** and choose the import that needs attention. Choose **Correct period**.

   ![Open Accounting Bank Imports and choose the import that needs attention](correct-a-bank-statement-period/01-correct.png)

2. Set the **start** and **end** of the period the statement really covers.

3. Write the **reason**, for example what the official statement says. It is posted in the import's history with your name.

   ![Write the reason, for example what the official statement says](correct-a-bank-statement-period/02-reason.png)

4. Choose **Apply**.

5. The import shows the corrected period and continues from there.

   ![The import shows the corrected period and continues from there](correct-a-bank-statement-period/03-corrected.png)
