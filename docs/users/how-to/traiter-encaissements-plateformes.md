# Traiter les encaissements des plateformes de contenu

## Préparation

Un administrateur Facturation plateformes configure les partenaires, produits,
journaux, taux de commission, devise et règles de libellé bancaire de chaque
plateforme. La comptabilisation automatique est désactivée par défaut. Les
justificatifs peuvent être joints à chaque encaissement. Le rôle Comptable
seul ne donne pas accès à cette application.

## Créer et contrôler une session

1. Ouvrez **Facturation plateformes → Sessions de facturation**.
2. Créez la session du mois comptable.
3. Vérifiez la date de facture, l'échéance et la devise bancaire.
4. Ajoutez les encaissements ou utilisez **Importer les opérations bancaires**
   pour sélectionner les règlements reçus.
5. Dans l'onglet **Encaissements**, complétez ensuite la plateforme, la
   référence, la devise et le montant d'origine de chaque ligne importée.
6. Cliquez sur **Contrôler** et corrigez chaque erreur bloquante.

La vue **Toutes ouvertes** affiche les opérations entrantes, comptabilisées,
non rapprochées et disponibles dans la devise bancaire de la session. La
détection classe les suggestions selon le motif configuré, le partenaire connu,
puis les mots-clés ; elle ne masque pas les libellés inconnus ou ambigus.
Utilisez **Suggestions uniquement** pour une liste plus courte.

## Générer et comptabiliser

1. Cliquez sur **Générer les brouillons**.
2. Contrôlez les factures client, factures de commission, taxes, comptes,
   analytique, dates et pièces jointes via le bouton intelligent.
3. Cliquez sur **Comptabiliser les pièces**.

Si la compensation est active, Odoo comptabilise une OD équilibrée entre
fournisseur et client, puis effectue les lettrages natifs. Une pièce
comptabilisée ne peut plus être réinitialisée dans cette application.

## Rapprocher la banque

1. Ouvrez **Importer les opérations bancaires** depuis une session.
2. Sélectionnez les encaissements ouverts à régler, y compris ceux d'autres
   sessions.
3. Sélectionnez les opérations reçues et ajustez les montants en cas de
   règlement partiel.
4. Cliquez sur **Associer les opérations sélectionnées**, puis sur
   **Rapprocher la banque**.
5. Laissez sans opération bancaire un règlement retardé : la session reste
   **Comptabilisée** et la facture client reste une créance ouverte.
6. Si un virement couvre plusieurs mois, sélectionnez tous les encaissements
   concernés et répartissez ce virement une seule fois.
7. La session passe à **Payée** uniquement lorsque les pièces requises et les
   opérations bancaires sont soldées.

Lorsqu'un encaissement dans la devise de la société crée un règlement dans une
devise étrangère, l'application affiche **Taux bancaire effectif**. Par exemple,
un règlement de 1 000 USD créé depuis un encaissement de 700 EUR valorise les
pièces à `1 USD = 0,70 EUR`. Les 700 EUR bancaires sont conservés et le
rapprochement immédiat ne génère aucun écart de change. Ce taux ne modifie pas
les taux généraux d'Odoo.

Si le règlement est enregistré avant la réception bancaire, les pièces gardent
le taux de référence Odoo. Le paiement reçu plus tard peut alors générer un gain
ou une perte de change normale, car il s'agit d'un règlement différé.

Les rôles Lecteur, Opérateur ou Administrateur Facturation plateformes doivent
être attribués explicitement. Le Lecteur consulte l'historique de ses sociétés
sans créer, comptabiliser, rapprocher ni supprimer.

Cette application comptabilise les revenus des plateformes de contenu. Elle
n'est pas liée à la connexion aux plateformes françaises de facturation
électronique.
