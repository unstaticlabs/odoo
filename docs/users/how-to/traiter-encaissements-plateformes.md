---
title: Rapprocher les versements des plateformes
type: how-to
description: Associer un encaissement bancaire à un versement déjà saisi, puis rapprocher les règlements tardifs, partiels ou groupés.
lang: fr
persona: finance_operator
app: Facturation des plateformes
models:
  - usl.platform.billing.payout
  - usl.platform.billing.session
translation_of: how-to/process-platform-payouts.md
source:
  - custom-addons/usl_platform_billing
generated: false
---

# Rapprocher les versements des plateformes

Les guides illustrés expliquent comment [comptabiliser les versements du
mois](comptabiliser-les-versements-mensuels-des-plateformes.md) et [rapprocher
une session dont l’encaissement est déjà
lié](rapprocher-une-session-de-facturation-des-plateformes.md).

## Associer un encaissement à un versement déjà saisi

Depuis la session, ouvrez **Importer des transactions bancaires** et choisissez
**Lier des transactions bancaires aux versements enregistrés**. Sélectionnez le
versement et la transaction reçue, vérifiez la répartition, puis cliquez sur
**Lier les transactions sélectionnées**. Une fois la session comptabilisée,
cliquez sur **Rapprocher les transactions bancaires**.

Si vous partez de l’encaissement et que le versement reste à saisir,
choisissez **Créer des versements depuis les transactions bancaires**.
Complétez ensuite la plateforme, la référence, la devise et le montant
d’origine.

**Toutes les transactions ouvertes** affiche les transactions entrantes disponibles dans la
devise bancaire de la session et les journaux autorisés. **Suggestions
uniquement** réduit la liste selon le libellé, le partenaire connu ou les
mots-clés. En cas de doute, vérifiez le libellé, le montant et la date.

## Versements tardifs, partiels ou groupés

- Si le versement tarde, laissez la transaction bancaire vide. La session
  reste **Comptabilisée** et la facture reste ouverte.
- Pour un règlement partiel, répartissez le montant reçu. Liez les règlements
  suivants à leur arrivée. La session passe à **Payée** lorsque les pièces et
  les encaissements requis sont soldés.
- Si un virement couvre plusieurs mois, sélectionnez les versements concernés
  et répartissez-le une seule fois. Le rapprochement groupé automatique exige
  le même client et la même devise de facture. Pour un virement mixte,
  conservez les répartitions vérifiées et terminez dans le rapprochement
  standard d’Odoo.

## Devise et accès

Un versement créé depuis la banque peut afficher un **Taux bancaire
effectif**. Ce taux valorise les pièces concernées et conserve les taux
généraux d’Odoo. Un versement saisi avant sa réception utilise le taux de
référence d’Odoo. Le règlement reçu plus tard peut donc générer un gain ou
une perte de change normale.

L’administrateur Facturation des plateformes configure les plateformes et
leurs règles bancaires. L’opérateur prépare et rapproche les sessions. Le
lecteur consulte l’historique de ses sociétés. Ces droits sont attribués
séparément de la Comptabilité et la comptabilisation automatique est
désactivée par défaut. Joignez le relevé de la plateforme quand il est disponible.
