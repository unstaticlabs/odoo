---
title: Correct the period of an imported bank statement
type: how-to
description: When an imported bank export covers other dates than Odoo read, set the right period and say why, so the import can continue and the history shows the correction.
lang: en
persona: accountant
app: Accounting
models:
  - account.bank.ingestion
screen: /odoo/action-usl_accounting.action_bank_ingestion_config
journey: correct-a-bank-statement-period
tour: usl_bank_period_correction
source:
  - custom-addons/usl_accounting/tests/test_accounting_journeys.py
screenshot_01-correct_png: 273fb2b82fb20f26b08d6bc77d8d089c21a2d130f2eb9b4092326e1b037b1ce7
screenshot_02-reason_png: f9590a3cd860b135e2803b57b3c1c9dbcb692d12024cc27b2003f9f3af63f058
screenshot_03-corrected_png: 6b250f76679275b444a9a9030ebb501a12165d2d76f6c3b3a41f0d42f92a15b1
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Correct the period of an imported bank statement

When an imported bank export covers other dates than Odoo read, set the right period and say why, so the import can continue and the history shows the correction.

1. Open **Accounting > Configuration > Bank Statement Email Setup**, choose the bank, and open the received export that reads **Needs attention** in its list; the activity on the bank journal points to the same record. Choose **Correct period**.

   ![Open Accounting Configuration Bank Statement Email Setup, choose the bank, and open the received export that reads Needs](correct-a-bank-statement-period/01-correct.png)

2. Set the **start** and **end** of the period the statement really covers.

3. Write the **reason**, for example what the official statement says. It is posted in the import's history with your name.

   ![Write the reason, for example what the official statement says](correct-a-bank-statement-period/02-reason.png)

4. Choose **Apply correction**.

5. The import shows the corrected period and continues from there.

   ![The import shows the corrected period and continues from there](correct-a-bank-statement-period/03-corrected.png)
