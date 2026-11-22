---
title: Corriger la période d’un relevé bancaire importé
type: how-to
description: Renseignez les dates figurant sur le relevé et consignez la raison de la correction pour que l’import puisse reprendre.
lang: fr
persona: accountant
translation_of: how-to/correct-a-bank-statement-period.md
app: Comptabilité
models:
  - account.bank.ingestion
screen: /odoo/action-usl_accounting.action_bank_ingestion_config
journey: corriger-la-periode-dun-releve-bancaire
tour: usl_bank_period_correction
source:
  - custom-addons/usl_accounting/tests/test_accounting_journeys.py
screenshot_01-correct_png: 95edf657d3dcd0f67d2defb9fca5603d8fe48ebe37fb454366533e592092f2a3
screenshot_02-reason_png: a81daabc419ca9b23b429ec424d96ddee9e5639624b61020e39c736ab057dce0
screenshot_03-corrected_png: 3fd7b3c25abf192db6bb549aa91722daea331834154df47a5491414e96e50d00
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Corriger la période d’un relevé bancaire importé

Renseignez les dates figurant sur le relevé et consignez la raison de la correction pour que l’import puisse reprendre.

1. Ouvrez l’import depuis l’activité du journal bancaire. Si vous gérez la configuration, vous le retrouvez aussi dans **Comptabilité > Configuration > Configuration des e-mails de relevés bancaires**. Ouvrez l’import à vérifier, puis cliquez sur **Corriger la période**.

   ![Ouvrez l’import depuis l’activité du journal bancaire](corriger-la-periode-dun-releve-bancaire/01-correct.png)

2. Recopiez les dates de début et de fin qui figurent sur le relevé.

3. Indiquez sur quelle pièce vous avez vérifié les dates. Le motif de correction figurera dans l’historique de l’import avec votre nom.

   ![Indiquez sur quelle pièce vous avez vérifié les dates](corriger-la-periode-dun-releve-bancaire/02-reason.png)

4. Vérifiez les deux dates, puis cliquez sur **Appliquer la correction**.

5. Relisez la période sur la fiche d’import. Odoo peut poursuivre le traitement avec les dates corrigées.

   ![Relisez la période sur la fiche d’import](corriger-la-periode-dun-releve-bancaire/03-corrected.png)
