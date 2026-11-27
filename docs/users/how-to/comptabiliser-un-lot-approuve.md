---
title: Comptabiliser un lot de dépenses approuvé
type: how-to
description: Enregistrez les frais réglés par la société et le remboursement dû au salarié, puis retrouvez les deux écritures.
lang: fr
persona: accountant
translation_of: how-to/post-an-approved-batch.md
app: Dépenses
models:
  - usl.expense.batch
  - hr.expense
  - account.move
screen: /odoo/action-usl_expense_batch.action_expense_batches
journey: comptabiliser-un-lot-approuve
tour: usl_expense_batch_accountant_handoff
source:
  - custom-addons/usl_expense_batch/tests/test_expense_batch_journeys.py
screenshot_01-approved_png: eeff3464d7836be3545b0137d2c12e13bda2f1e6f8dd7fceb0933a02666ce3a6
screenshot_02-entries_png: fc34957fd7419364dd23619135f8340f429264a6626bcc91c34a02da6fac8455
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Comptabiliser un lot de dépenses approuvé

Enregistrez les frais réglés par la société et le remboursement dû au salarié, puis retrouvez les deux écritures.

1. Dans **Dépenses > Lots de dépenses**, ouvrez le lot approuvé. Vérifiez les montants payés par la société et par le salarié.

   ![Dans Dépenses Lots de dépenses, ouvrez le lot approuvé](comptabiliser-un-lot-approuve/01-approved.png)

2. Cliquez sur **Comptabiliser le lot**. Odoo comptabilise les frais réglés par la société et ouvre la fenêtre de comptabilisation des avances du salarié.

3. Vérifiez le journal et la date proposés, puis cliquez sur **Comptabiliser les dépenses**.

4. Vérifiez les deux écritures. L’une correspond aux frais réglés par la société, l’autre au remboursement dû au salarié. Chacune renvoie au lot.

   ![Vérifiez les deux écritures](comptabiliser-un-lot-approuve/02-entries.png)
