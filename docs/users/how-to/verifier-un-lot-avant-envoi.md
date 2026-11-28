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
screenshot_01-batch_png: 4fb0210a76b2b2381f92675ce0305fc9c17fa9a91f882ff1c5ba0a1f40b462c9
screenshot_02-context_preview_png: 657e5afa0a0bc13f241a78c5d52c094f30b5a01e0814ea86223f5eb2c17e25aa
screenshot_03-exception_png: 3bef7c6d981f5b49e6eca9804a7e5783f4640e4d2c38027bb2e9b83489bffc0f
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Vérifier un lot de dépenses avant de le soumettre

Contrôlez les montants par payeur et repérez les lignes dont la répartition analytique diffère de celle du lot.

1. Dans **Dépenses > Lots de dépenses**, ouvrez votre lot. Le récapitulatif distingue les avances du salarié des frais de la société. L’onglet **Dépenses** en donne le détail.

   ![Dans Dépenses Lots de dépenses, ouvrez votre lot](verifier-un-lot-avant-envoi/01-batch.png)

2. Dans **Contexte commun**, vérifiez la répartition analytique enregistrée. Les dépenses existantes gardent leurs valeurs tant que vous n’appliquez pas le contexte.

3. Cliquez sur **Appliquer le contexte à … notes de frais** pour ouvrir l’aperçu.

4. Vérifiez quels brouillons vont changer. Les dépenses déjà soumises ou approuvées gardent leurs valeurs, comme les brouillons qui ont leur propre répartition, sauf si un responsable les sélectionne pour la remplacer.

   ![Vérifiez quels brouillons vont changer](verifier-un-lot-avant-envoi/02-context_preview.png)

5. Dans l’aperçu, confirmez l’application du contexte commun pour mettre à jour les brouillons concernés.

6. Un repère signale une ligne dont l’affectation diffère. Survolez-le pour lire la différence. Ouvrez la ligne pour la corriger si nécessaire.

   ![Un repère signale une ligne dont l’affectation diffère](verifier-un-lot-avant-envoi/03-exception.png)

7. Vous pouvez corriger la composition du lot en conservant les dépenses.

8. Cliquez sur **Retirer du lot** pour remettre la dépense dans votre liste. Elle pourra rejoindre un autre lot.
