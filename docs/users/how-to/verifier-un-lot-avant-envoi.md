---
title: Vérifier un lot de dépenses avant de le soumettre
type: how-to
description: Contrôlez les montants par payeur et repérez les lignes dont la répartition analytique diffère de celle du lot.
lang: fr
persona: employee
translation_of: how-to/review-a-batch-before-submitting.md
app: Dépenses
models:
  - usl.expense.batch
  - hr.expense
screen: /odoo/action-usl_expense_batch.action_expense_batches
journey: verifier-un-lot-avant-envoi
tour: usl_expense_batch_focused_review
source:
  - custom-addons/usl_expense_batch/tests/test_expense_batch_journeys.py
screenshot_01-batch_png: 60df92e7de622143a6e9cc79d924bf672e8a8f1900d1cfa628be707f66bfaa48
screenshot_02-context_preview_png: 29446070d4355626ae35b235bb0be33cb6bc4593b5fe77e56121b1816e89d2e7
screenshot_03-exception_png: 63b369e645a4d90d9f093c9972746dc2c7b73f5905a7b09900cc59d82e0fa50c
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Vérifier un lot de dépenses avant de le soumettre

Contrôlez les montants par payeur et repérez les lignes dont la répartition analytique diffère de celle du lot.

1. Dans **Dépenses > Lots de dépenses**, ouvrez votre lot. Le récapitulatif distingue les avances du salarié des frais de la société. L’onglet **Dépenses** en donne le détail.

   ![Dans Dépenses Lots de dépenses, ouvrez votre lot](verifier-un-lot-avant-envoi/01-batch.png)

2. Dans **Contexte commun**, enregistrez la nouvelle répartition analytique. Les dépenses existantes gardent leurs valeurs tant que vous n’appliquez pas le contexte.

3. Cliquez sur **Appliquer le contexte à … notes de frais** pour ouvrir l’aperçu.

4. Vérifiez quels brouillons vont changer. Les dépenses déjà soumises ou approuvées gardent leurs valeurs, comme les brouillons qui ont leur propre répartition, sauf si un responsable les sélectionne pour la remplacer.

   ![Vérifiez quels brouillons vont changer](verifier-un-lot-avant-envoi/02-context_preview.png)

5. Dans l’aperçu, confirmez l’application du contexte commun pour mettre à jour les brouillons concernés.

6. Un repère signale une ligne dont l’affectation diffère. Survolez-le pour lire la différence. Ouvrez la ligne pour la corriger si nécessaire.

   ![Un repère signale une ligne dont l’affectation diffère](verifier-un-lot-avant-envoi/03-exception.png)

7. Vous pouvez corriger la composition du lot en conservant les dépenses.

8. Cliquez sur **Retirer du lot** pour remettre la dépense dans votre liste. Elle pourra rejoindre un autre lot.
