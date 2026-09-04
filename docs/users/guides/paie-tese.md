# Paie TESE

L’application **Paie TESE** conserve dans Odoo le bulletin calculé par TESE,
son contexte RH et toutes les dettes comptables encore ouvertes. Odoo ne
recalcule pas la paie légale : le PDF du prestataire reste le justificatif.

## Avant la première paie

Un administrateur RH et comptable configure :

1. l’employé et sa **fiche employé** applicable, avec la date d’effet, le
   salaire et le temps de travail ;
2. le journal **Paie TESE** et le contact collecteur URSSAF/TESE dans la
   société ;
3. un **profil TESE** daté pour l’employé ;
4. les onze comptes français et les montants fournis par TESE.

Le bouton **Charger les valeurs françaises** cherche les comptes exacts
`641100`, `645100`, `645200`, `645300`, `633300`, `633500`, `421000`,
`431000`, `437020`, `437030` et `442100`. Il ne crée et ne renumérote jamais
un compte. Si un compte manque ou existe en double, corrigez le plan comptable
avant de continuer.

Une seule version active du profil peut couvrir une période. Archivez
l’ancien profil ou renseignez sa date de fin avant de créer le suivant.

## Enregistrer un mois

1. Ouvrez l’application **Paie TESE** et cliquez sur **Nouveau**.
2. Vérifiez le mois proposé. Odoo choisit le plus ancien mois terminé qui
   manque pour l’employé ; s’il n’en manque aucun, il propose le mois suivant.
3. Comparez la synthèse avec la déclaration TESE : **Net payé**,
   **Prélèvement URSSAF**, brut, cotisations et prélèvement à la source.
4. Joignez le bulletin PDF officiel fourni par TESE.
5. Cliquez sur **Préparer**, puis contrôlez l’écriture comptable.
6. Cliquez sur **Créer l’écriture brouillon**, puis **Comptabiliser**.

Les dates usuelles sont proposées automatiquement : salaire le premier jour
après le mois de paie et prélèvement URSSAF le 15 du deuxième mois suivant.
Une date saisie manuellement est conservée.

Le montant **Prélèvement bancaire attendu / rapproché** n'est pas un nouveau
calcul de cotisations. Avant comptabilisation, ne le changez que si TESE
annonce un prélèvement bancaire différent du total déclaré. Il sert à chercher
le paiement. Le détail des dettes reste inchangé et l'écart sûr est reporté
sur `431000` pour le suivi comptable.

Le premier onglet reproduit la lecture utile du bulletin sans créer un second
bulletin légal. L’onglet **Paiements** montre ce qui est attendu, ce qui a été
trouvé en banque, l’écart et le solde encore ouvert. L’onglet
**Comptabilité** contient l’écriture et les onze lignes figées.

## Mettre à jour les paramètres

Depuis une paie encore brouillon, cliquez sur **Mettre à jour les paramètres**
si la déclaration TESE ou le contrat a changé.

- Modifiez les chiffres TESE, les conditions d’emploi, ou les deux.
- La date d’effet est le mois de paie.
- Odoo archive et clôture l’ancien profil TESE, crée le nouveau profil et, si
  nécessaire, une nouvelle version native du contrat.
- La paie est immédiatement repréparée avec ces versions.

L'ancien profil n'est jamais supprimé. La liste affiche la version actuelle
par défaut ; retirez le filtre **En cours** ou choisissez **Archivés** pour
retrouver toutes les versions précédentes.

Quand les montants bruts ou les heures TESE diffèrent du contrat RH, un
avertissement court affiche les deux valeurs sur le profil, dans cet assistant
et sur la paie en préparation. Alignez TESE avec le contrat, sauf si l'écart
est réellement intentionnel.

Une écriture comptable encore brouillon est régénérée. Une écriture
comptabilisée reste immuable. Cette opération exige les droits Administrateur
RH et Administrateur comptable.

Le PDF est obligatoire. Après comptabilisation, le bulletin, son instantané
comptable et le justificatif ne peuvent plus être modifiés. Une correction
nécessite une extourne comptable explicite et une nouvelle fiche de paie.

Le bouton **Documents** ouvre les justificatifs archivés associés à cette
paie. **Ajouter un document** permet de déposer une nouvelle pièce dans
l’archive selon vos droits. L’écriture et la paie conservent aussi leur pièce
opérationnelle native : l’archive ne remplace pas la preuve de comptabilisation.

**Choisir dans Documents** propose d’abord les PDF de paie non liés reconnus
par Paperless. Recherchez par nom si le document attendu a été mal classé.

## Rapprocher le salaire et TESE

La fiche passe à **À rapprocher** tant qu’une dette reste ouverte :

- `421000` représente le salaire net dû à l’employé ;
- `431000`, `437020`, `437030` et `442100` représentent le prélèvement TESE,
  les organismes sociaux et le prélèvement à la source.

Cliquez sur **Actualiser les candidats**. La suggestion tient compte du
montant, de la date, du contact, du libellé et de la référence, mais elle
n’est jamais une validation.

Le rapprochement automatique n’est proposé que lorsqu’il existe un seul
candidat sûr. Pour l’URSSAF, un écart d’arrondi de 5 € maximum est accepté :
le vrai montant bancaire est utilisé, les dettes déclarées sont soldées et
l’écart exact reste ouvert sur `431000` comme **report URSSAF**. La paie est
**soldée** : aucune action supplémentaire n’est demandée ici. Le report reste
visible jusqu’à sa compensation dans le suivi comptable normal. Il n’est
jamais envoyé automatiquement sur `658` ou `758`.

S’il y a plusieurs candidats, un écart supérieur à 5 €, un paiement partiel
ou une structure inhabituelle, ouvrez **Rapprochement bancaire** et
documentez la décision comptable.

Le statut **Soldée** apparaît lorsque le salaire et le prélèvement URSSAF réel
sont rapprochés. Un petit report URSSAF sûr peut rester sur `431000` sans
rouvrir la paie. Une ancienne case cochée ou un statut importé ne suffit pas.

Les filtres **Paiements ouverts**, **Salaire ouvert**, **URSSAF ouvert**,
**Report URSSAF**, **Soldée** et **PDF manquant** permettent de retrouver
rapidement le travail restant. La liste d’accueil montre toutes les paies par
défaut.

## Contrôler les anomalies

Dans l’application **Paie TESE**, ouvrez **Configuration → Diagnostics**.
Cette seule action relance les contrôles et ouvre immédiatement la liste
actualisée. Les anomalies bloquantes signalent notamment :

- un PDF absent ou d’un mauvais type ;
- un journal, un collecteur ou un compte manquant ;
- un profil incomplet ou incohérent ;
- une écriture absente, non comptabilisée ou mal reliée ;
- un écart débit/crédit ;
- une dette ouverte ou un candidat bancaire ambigu.

Une anomalie corrigée reste dans l’historique avec sa date de résolution. Elle
n’est plus active et peut être filtrée.

## Vérifier les comptes de paie

Ouvrez **Configuration → Comptes de paie** pour afficher uniquement les onze
comptes utilisés par les profils TESE. Cette vue réutilise le plan comptable
Odoo : une modification faite ici est donc la même modification que dans
**Comptabilité → Configuration → Plan comptable**.

## Retrouver les anciens profils

Ouvrez **Profils de paie** dans le menu principal de l’application. La vue
affiche les profils en cours. Retirez le filtre **En cours**, ou utilisez
**Tous** ou **Archivés**, pour retrouver les anciennes versions, leurs dates
de validité, leur contrat lié et leur historique d’utilisation.

## Droits

Les données de paie exigent à la fois les droits **Administrateur RH** et un
droit comptable :

- lecture : Administrateur RH + lecture comptable ;
- préparation, comptabilisation et rapprochement : Administrateur RH +
  Comptable ;
- profils et paramètres société : Administrateur RH + Administrateur
  comptable.

Un comptable qui n’a pas le rôle RH et un responsable RH qui n’a pas le rôle
comptable ne voient pas les fiches de paie.
