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

1. Ouvrez l’application **Paie TESE**, puis **Paies**, et créez une fiche.
2. Sélectionnez l’employé, le mois, l’année et saisissez la référence TESE.
3. Cliquez sur **Préparer**.
4. Contrôlez la période, la version RH retenue, le brut, le net, les
   cotisations, le prélèvement à la source et les onze lignes comptables.
5. Lisez les avertissements si le brut ou les heures TESE diffèrent de la
   fiche RH. Un avertissement n’altère pas l’historique RH.
6. Cliquez sur **Créer l’écriture brouillon** et examinez-la.
7. Joignez le bulletin PDF fourni par TESE.
8. Cliquez sur **Comptabiliser**.

Le PDF est obligatoire. Après comptabilisation, le bulletin, son instantané
comptable et le justificatif ne peuvent plus être modifiés. Une correction
nécessite une extourne comptable explicite et une nouvelle fiche de paie.

## Rapprocher le salaire et TESE

La fiche passe à **À rapprocher** tant qu’une dette reste ouverte :

- `421000` représente le salaire net dû à l’employé ;
- `431000`, `437020`, `437030` et `442100` représentent le prélèvement TESE,
  les organismes sociaux et le prélèvement à la source.

Cliquez sur **Actualiser les candidats**. La suggestion tient compte du
montant, de la date, du contact, du libellé et de la référence, mais elle
n’est jamais une validation.

Le rapprochement automatique n’est proposé que lorsqu’il existe un seul
candidat exact et sûr. S’il y a plusieurs candidats, un écart d’arrondi, un
paiement partiel ou une devise différente, ouvrez **Rapprochement bancaire**
et documentez la décision comptable. L’application ne force pas un écart sur
un compte de cotisations.

Le statut **Payée** apparaît uniquement lorsque les soldes résiduels du
salaire et de toutes les dettes TESE sont nuls. Une ancienne case cochée ou un
statut importé ne suffit pas.

## Contrôler les anomalies

Dans l’application **Paie TESE**, ouvrez **Configuration**, cliquez sur
**Exécuter les diagnostics**, puis ouvrez **Diagnostics**. Les anomalies
bloquantes signalent notamment :

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
