---
title: Régler une facture en devise au taux du paiement
type: how-to
description: Utilisez le débit bancaire pour valoriser une facture fournisseur en devise, vérifiez le détail du règlement et retrouvez la valeur d’origine en cas d’erreur.
lang: fr
persona: accountant
translation_of: how-to/settle-a-foreign-bill-at-the-payment-rate.md
app: Comptabilité
models:
  - account.move
  - account.bank.statement.line
screen: /odoo/action-account.action_move_in_invoice_type
journey: regler-une-facture-en-devise-au-taux-du-paiement
tour: usl_immediate_settlement
source:
  - custom-addons/usl_accounting/tests/test_accounting_journeys.py
screenshot_01-actions_png: 40769fcdf9042557a05a33a8e653ef356b77c7c5c14a5fc43c0c911e5da399fb
screenshot_02-trace_png: f620003f15bb76dcab08dbdc4a99747c602a9be56e9e4c42378ed5c0bb9cd91d
screenshot_03-detail_png: 548b29ffd691b9f47a0d41c8ee7333070c584196cb3c0b2020103ee1a3f2fa7a
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Régler une facture en devise au taux du paiement

Utilisez le débit bancaire pour valoriser une facture fournisseur en devise, vérifiez le détail du règlement et retrouvez la valeur d’origine en cas d’erreur.

1. Dans **Comptabilité > Fournisseurs > Factures**, ouvrez la facture comptabilisée. Sous **Paiements suggérés**, un rapprochement éligible propose **Ajouter**, **Régler** et **Corriger le taux initial**.

   ![Dans Comptabilité Fournisseurs Factures, ouvrez la facture comptabilisée](regler-une-facture-en-devise-au-taux-du-paiement/01-actions.png)

2. Survolez la ligne **Meilleur rapprochement** ou placez-y le focus avec la touche Tab. L’infobulle indique la transaction bancaire et les éléments qui justifient le rapprochement.

3. Choisissez **Corriger le taux initial** si les justificatifs montrent que le taux était erroné à la comptabilisation. Vérifiez le taux et les montants affichés avant de cliquer. Odoo corrige la facture et la rapproche du paiement.

4. La facture passe à l’état payé. La ligne de règlement indique le taux de paiement et précise qu’aucun écart de change n’a été comptabilisé.

   ![La facture passe à l’état payé](regler-une-facture-en-devise-au-taux-du-paiement/02-trace.png)

5. Cliquez sur l’icône d’information de la ligne de paiement.

6. Comparez l’estimation écartée, la valeur d’origine de la facture et sa valeur au taux du paiement. Les écritures associées permettent de retracer le règlement.

   ![Comparez l’estimation écartée, la valeur d’origine de la facture et sa valeur au taux du paiement](regler-une-facture-en-devise-au-taux-du-paiement/03-detail.png)

7. Si le rapprochement est erroné, cliquez sur **Annuler le lettrage** dans le détail du paiement.

8. La facture est de nouveau ouverte à sa valeur d’origine. Vous pouvez vérifier les trois propositions et choisir le rapprochement qui convient.
