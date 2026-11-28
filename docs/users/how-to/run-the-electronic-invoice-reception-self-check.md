---
title: Run the electronic-invoice reception self-check
type: how-to
description: Check a company’s local invoice import with a sample UBL invoice. Live reception is verified separately after activation.
lang: en
persona: administrator
app: Accounting
models:
  - res.company
screen: /odoo/action-rebuild_account_migration.action_rebuild_einvoice_readiness
journey: run-the-electronic-invoice-reception-self-check
tour: usl_einvoice_manager_reception
source:
  - custom-addons/rebuild_account_migration/tests/test_einvoice_journeys.py
screenshot_01-setup_png: 0d2480717c64350dd6aab0f3d0be9b68529a04a64ec159b45f44af2a42c7049e
screenshot_02-passed_png: b7830aa1aa6d4196c6058cd5f3da84db702b0099f5a786f7115f9a2370ea67df
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Run the electronic-invoice reception self-check

Check a company’s local invoice import with a sample UBL invoice. Live reception is verified separately after activation.

1. Open **Accounting > Configuration > Electronic Invoicing**. Each company you may act in is listed with its reception status. Choose the company.

2. The setup screen gathers what reception needs: the journals, the accounts, the approved platform and the last self-check.

   ![The setup screen gathers what reception needs: the journals, the accounts, the approved platform and the last self-check](run-the-electronic-invoice-reception-self-check/01-setup.png)

3. Choose **Run self-check**. Odoo uses its usual importer to create a temporary draft bill from a sample UBL invoice. It checks the supplier, invoice lines, taxes, total and original file, then rolls back the test records.

4. Odoo keeps the result and the test date. Look for **Test passed** and run the check again after a setup change. Use the [activation guide](activate-electronic-invoice-reception.md) to verify the first live invoice from the approved platform.

   ![Odoo keeps the result and the test date](run-the-electronic-invoice-reception-self-check/02-passed.png)
