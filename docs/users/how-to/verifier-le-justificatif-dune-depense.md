---
title: Vérifier le justificatif d’une dépense
type: how-to
description: Retrouvez le reçu joint à une dépense et vérifiez qui a payé avant d’ajouter la dépense à un lot.
lang: fr
persona: employee
translation_of: how-to/check-the-receipt-on-an-expense.md
app: Dépenses
models:
  - hr.expense
screen: /odoo/action-hr_expense.hr_expense_actions_my_all
journey: verifier-le-justificatif-dune-depense
tour: usl_expense_batch_receipt_capture
source:
  - custom-addons/usl_expense_batch/tests/test_expense_batch_journeys.py
screenshot_01-upload_png: 6c7e5cbff37272e4c4d05065abeaad4d33f1f040695a4f7e14eb7685fc77885a
screenshot_02-receipt_png: f1417ad80c9fe0db2cdc49a717a25568527ec55f86b9b55689dfbe5664cea278
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Vérifier le justificatif d’une dépense

Retrouvez le reçu joint à une dépense et vérifiez qui a payé avant d’ajouter la dépense à un lot.

1. Ouvrez **Dépenses > Mes dépenses**. Si vous partez d’un nouveau reçu, **Charger** permet de créer la dépense à partir du fichier.

   ![Ouvrez Dépenses Mes dépenses](verifier-le-justificatif-dune-depense/01-upload.png)

2. Ouvrez la dépense à contrôler.

3. Cliquez sur le trombone dans le fil de discussion. Le chiffre indique le nombre de fichiers joints.

4. Sous **Fichiers**, ouvrez le justificatif et vérifiez qu’il est lisible et correspond à cette dépense.

   ![Sous Fichiers, ouvrez le justificatif et vérifiez qu’il est lisible et correspond à cette dépense](verifier-le-justificatif-dune-depense/02-receipt.png)

5. Vérifiez qui a payé. Une avance personnelle doit être remboursée au salarié. Si la carte de la société a servi, choisissez la société.
