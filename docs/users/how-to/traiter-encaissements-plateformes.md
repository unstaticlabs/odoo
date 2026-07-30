# Traiter les encaissements des plateformes de contenu

## Préparation

Un responsable Comptabilité configure les partenaires, produits, journaux,
taux de commission, devise et règles de libellé bancaire de chaque plateforme.
La comptabilisation automatique est désactivée par défaut. Les justificatifs
peuvent être joints à chaque encaissement.

## Créer et contrôler une session

1. Ouvrez **Facturation plateformes → Sessions de facturation**.
2. Créez la session du mois comptable.
3. Vérifiez la date de facture, l'échéance et la devise bancaire.
4. Ajoutez les encaissements ou utilisez **Importer les opérations bancaires**
   pour recalculer les candidats depuis les données bancaires Odoo.
5. Vérifiez la référence plateforme et le montant net.
6. Cliquez sur **Contrôler** et corrigez chaque erreur bloquante.

La détection applique d'abord le motif configuré, puis le partenaire connu,
puis les mots-clés. Elle exclut les sorties, les opérations déjà rapprochées ou
liées, les autres sociétés et les correspondances ambiguës.

## Générer et comptabiliser

1. Cliquez sur **Générer les brouillons**.
2. Contrôlez les factures client, factures de commission, taxes, comptes,
   analytique, dates et pièces jointes via le bouton intelligent.
3. Cliquez sur **Comptabiliser les pièces**.

Si la compensation est active, Odoo comptabilise une OD équilibrée entre
fournisseur et client, puis effectue les lettrages natifs. Une pièce
comptabilisée ne peut plus être réinitialisée dans cette application.

## Rapprocher la banque

1. Associez à chaque encaissement l'opération bancaire entrante correcte.
2. Conservez le montant bancaire réel, puis cliquez sur **Rapprocher la
   banque**.
3. Traitez séparément les lignes bloquées ; les lignes valides restent
   rapprochées.
4. La session passe à **Payée** uniquement lorsque les pièces requises et les
   opérations bancaires sont soldées.

En multidevise, le montant bancaire d'origine est conservé. Odoo et le module
OCA calculent le change et les éventuels écarts.

Le groupe Comptabilité en lecture seule peut consulter l'historique et les
justificatifs de ses sociétés, sans créer, comptabiliser, rapprocher ni
supprimer.

Cette application comptabilise les revenus des plateformes de contenu. Elle
n'est pas liée à la connexion aux plateformes françaises de facturation
électronique.
