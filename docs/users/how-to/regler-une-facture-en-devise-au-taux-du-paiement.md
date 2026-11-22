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
screenshot_01-actions_png: cb2177b4ca80ebba23bc03398bacf46cfb9ffe5cf8718cd208178cb7e3620d50
screenshot_02-trace_png: f620003f15bb76dcab08dbdc4a99747c602a9be56e9e4c42378ed5c0bb9cd91d
screenshot_03-detail_png: 548b29ffd691b9f47a0d41c8ee7333070c584196cb3c0b2020103ee1a3f2fa7a
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Régler une facture en devise au taux du paiement

Utilisez le débit bancaire pour valoriser une facture fournisseur en devise, vérifiez le détail du règlement et retrouvez la valeur d’origine en cas d’erreur.

1. Dans **Comptabilité > Fournisseurs > Factures**, ouvrez la facture comptabilisée. Lorsqu’un débit du même fournisseur satisfait les critères de date et de taux, Odoo propose trois actions de rapprochement. L’action recommandée apparaît en couleur.

   ![Dans Comptabilité Fournisseurs Factures, ouvrez la facture comptabilisée](regler-une-facture-en-devise-au-taux-du-paiement/01-actions.png)

2. Consultez **Meilleur rapprochement** pour identifier la ligne bancaire proposée et examiner les autres possibilités.

3. Si le débit justifie cette valorisation, cliquez sur **Utiliser le taux de paiement**. Odoo recalcule la valeur de la facture en devise de la société au taux observé sur le paiement.

4. La facture passe à l’état payé. La ligne de règlement indique le taux de paiement et précise qu’aucun écart de change n’a été comptabilisé.

   ![La facture passe à l’état payé](regler-une-facture-en-devise-au-taux-du-paiement/02-trace.png)

5. Cliquez sur l’icône d’information de la ligne de paiement.

6. Comparez l’estimation écartée, la valeur d’origine de la facture et sa valeur au taux du paiement. Les écritures associées permettent de retracer le règlement.

   ![Comparez l’estimation écartée, la valeur d’origine de la facture et sa valeur au taux du paiement](regler-une-facture-en-devise-au-taux-du-paiement/03-detail.png)

7. Si le rapprochement est erroné, cliquez sur **Annuler le lettrage** dans le détail du paiement.

8. La facture est de nouveau ouverte à sa valeur d’origine. Vous pouvez vérifier les trois propositions et choisir le rapprochement qui convient.
