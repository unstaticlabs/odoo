# Préparer et suivre une signature électronique

L’application **Signature** centralise les modèles, demandes, signataires et
preuves. Odoo conserve le document original et le document signé; le prestataire
de confiance réalise l’authentification et la cérémonie de signature.

## Choisir le bon niveau

- **Standard** convient aux validations courantes à faible risque.
- **Vérifiée** ajoute les contrôles d’identité prévus par la politique de
  l’entreprise.
- **Qualifiée** est réservée aux actes pour lesquels une signature électronique
  qualifiée est expressément requise. Le signataire quitte alors brièvement
  Odoo pour le parcours d’identité sécurisé du prestataire.

Le niveau demandé et le niveau réellement atteint sont affichés séparément.
Une attestation de fin de processus est une preuve technique; elle ne suffit
pas, à elle seule, à qualifier juridiquement le contrat.

## Créer un modèle

1. Ouvrez Signature → Modèles, puis chargez un PDF lisible et non chiffré.
2. Placez les champs Signature, Paraphe, Texte, Nom, Date, Case à cocher,
   Société ou Rôle sur les pages voulues.
3. Affectez chaque champ à un rôle de signataire et rendez obligatoires les
   champs nécessaires.
4. Choisissez la politique, le délai d’expiration et la fréquence/limite des
   relances, puis marquez le modèle **Prêt**.

Un modèle déjà utilisé est versionné lors d’une modification importante. Une
demande en cours conserve toujours sa version et sa mise en page gelées.

## Envoyer et suivre une demande

Créez la demande depuis Signature → Demandes ou depuis le menu Action d’une
fiche liée. Vérifiez les signataires, leur adresse électronique, leur numéro de
mobile si la politique l’exige, l’ordre de signature et l’échéance. Marquez la
demande Prête, puis envoyez-la.

Les états usuels sont Brouillon, Prête, Envoyée, Consultée, Partiellement
signée et Terminée. Refusée, Expirée et Annulée sont définitifs. **Action
requise** signifie qu’un administrateur doit rapprocher l’état du prestataire
ou récupérer une preuve manquante; ne recréez pas une demande pour masquer
l’erreur.

Les relances sont plafonnées et ne concernent que le prochain signataire
éligible. Le titre confidentiel du document n’apparaît pas dans l’objet du
courriel. Après achèvement, les signataires autorisés peuvent télécharger le
PDF signé depuis leur portail.

## Lien public réutilisable

Un gestionnaire peut activer un lien public uniquement pour un modèle Standard
à un seul rôle. Chaque personne saisit ses propres nom, courriel, mobile et
consentement; une nouvelle demande indépendante est créée. Le formulaire ne
réaffiche jamais les données d’un signataire précédent. Désactivez le lien dès
qu’il n’est plus nécessaire.

## Contrôler les preuves

La demande terminée contient le PDF original, le PDF signé, les pistes d’audit
des signataires et leurs empreintes SHA-256. Les preuves sont non modifiables.
Les demandes historiques provenant d’Odoo Online portent une mention indiquant
que le niveau réellement atteint n’a pas pu être établi; elles sont consultables
mais ne peuvent jamais être renvoyées.

Signalez à l’administrateur toute preuve absente, empreinte incohérente, lien
inattendu ou demande affectée à la mauvaise société. Ne transmettez jamais un
lien de signature à une autre personne.
