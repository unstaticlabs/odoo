---
title: Préparer le contexte d’un lot avant de le soumettre
type: how-to
description: Vérifiez les payeurs, prévisualisez le contexte commun et traitez les lignes à corriger.
lang: fr
persona: employee
app: Dépenses
models:
  - usl.expense.batch
  - hr.expense
source:
  - custom-addons/usl_expense_batch/models/expense_batch.py
  - custom-addons/usl_expense_batch/static/tests/tours/expense_batch_tour.js
generated: false
---

# Préparer le contexte d’un lot avant de le soumettre

1. Dans **Dépenses > Lots de dépenses**, ouvrez le lot. Si les frais ne sont pas
   encore regroupés, suivez [Créer ou choisir un lot](regrouper-des-depenses-en-lot.md).
2. Vérifiez le salarié, la société, les justificatifs et les totaux par payeur.
   Un même lot peut contenir des avances du salarié et des frais payés par la société.
3. Dans **Contexte commun**, vérifiez la répartition analytique enregistrée.
   Sélectionnez **Appliquer le contexte à … notes de frais**. Vérifiez l’aperçu
   et confirmez. Seuls les brouillons éligibles sont mis à jour. Une affectation
   propre à la dépense reste prioritaire, sauf si un responsable la remplace
   explicitement dans l’aperçu.
4. Examinez les lignes signalées. Utilisez **Retirer du lot** pour détacher un
   brouillon ou **Renvoyer pour correction** pour reprendre une dépense soumise
   ou approuvée. Suivez [Vérifier un lot avant de le soumettre](verifier-un-lot-avant-envoi.md)
   pour repérer les exceptions et les dépenses verrouillées.
5. Cliquez sur **Soumettre le lot**. La progression suit la soumission,
   l’approbation et la comptabilisation. Archivez le lot lorsque vous n’avez
   plus de dépense à ajouter. Vous pouvez le rouvrir ensuite.

En anglais, consultez
[Group related expenses](group-expenses-into-a-batch.md) et
[Review a batch](review-a-batch-before-submitting.md).
