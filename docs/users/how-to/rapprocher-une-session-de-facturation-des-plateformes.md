---
title: Comptabiliser et rapprocher une session de facturation
type: how-to
description: À partir des versements saisis et de l’encaissement bancaire identifié, retrouvez les factures, la compensation des commissions et le règlement final.
lang: fr
persona: accountant
translation_of: how-to/settle-a-platform-billing-session.md
app: Facturation des plateformes
models:
  - usl.platform.billing.session
  - account.move
screen: /odoo/action-usl_platform_billing.action_platform_billing_sessions
journey: rapprocher-une-session-de-facturation-des-plateformes
tour: usl_platform_billing_manager_journey
source:
  - custom-addons/usl_platform_billing/tests/test_platform_billing_journeys.py
screenshot_01-draft_png: a80f1a33e09962b1a7a4cc603e097e70e5267d273bec9ced96add449c643a50f
screenshot_02-paid_png: 0b0a2adedc24eb580805f5cae60e04bcedd437135b1a659fbf876810b25a1a29
screenshot_03-moves_png: e541e913d1470c7dd3f24f1725e4bebbac818700a9254596a5d2617d5270f846
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Comptabiliser et rapprocher une session de facturation

À partir des versements saisis et de l’encaissement bancaire identifié, retrouvez les factures, la compensation des commissions et le règlement final.

1. Dans **Facturation des plateformes > Sessions de facturation**, ouvrez la session en brouillon. Vérifiez que les versements sont saisis et que l’encaissement bancaire est associé.

   ![Dans Facturation des plateformes Sessions de facturation, ouvrez la session en brouillon](rapprocher-une-session-de-facturation-des-plateformes/01-draft.png)

2. Cliquez sur **Contrôle** pour lancer les vérifications comptables.

3. Cliquez sur **Générer les brouillons**, puis vérifiez les factures de vente et les factures de commission créées.

4. Cliquez sur **Comptabiliser les pièces**. La commission est compensée avec la recette. Le montant à rapprocher correspond alors au versement net.

5. Cliquez sur **Rapprocher les transactions bancaires**. L’encaissement associé au versement solde le net.

6. Vérifiez que la session est payée. La facture de vente, la facture de commission et l’encaissement sont rapprochés.

   ![Vérifiez que la session est payée](rapprocher-une-session-de-facturation-des-plateformes/02-paid.png)

7. Ouvrez **Pièces comptables** pour consulter les documents comptabilisés.

8. Ouvrez une pièce dans la liste pour retrouver ses lignes et ses références dans les rapports comptables.

   ![Ouvrez une pièce dans la liste pour retrouver ses lignes et ses références dans les rapports comptables](rapprocher-une-session-de-facturation-des-plateformes/03-moves.png)

9. La mention **Aucun paiement requis** identifie la compensation de commission. Elle ne crée pas de paiement à effectuer.
