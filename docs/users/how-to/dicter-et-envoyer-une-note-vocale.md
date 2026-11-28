---
title: Dicter dans un champ et envoyer une note vocale
type: how-to
description: Transformez la parole en texte dans un champ Odoo, ou partagez un vocal et sa transcription dans le fil d’une fiche.
lang: fr
persona: employee
app: Fil de discussion
models:
  - mail.message
source:
  - custom-addons/usl_voice/README.md
  - custom-addons/usl_voice/static/src/dictation.xml
  - custom-addons/usl_voice/static/src/chatter.js
generated: false
---

# Dicter dans un champ et envoyer une note vocale

Utilisez la **dictée** pour remplir un champ. Utilisez une **note vocale** pour partager l’enregistrement dans le fil d’une fiche.

## Dicter dans un champ

1. Placez le curseur à l’endroit voulu. Sélectionnez le microphone ou appuyez sur **Alt+Maj+D**.
2. Parlez en français ou en anglais. Arrêtez la dictée et relisez le texte avant d’enregistrer la fiche ou d’envoyer votre message.
3. Si vous avez changé de champ pendant la transcription, utilisez l’option proposée pour copier le texte à l’endroit voulu.

Si la transcription tarde ou échoue, choisissez **Réessayer la transcription** ou **Télécharger l’audio**. Si Odoo a pu enregistrer l’audio localement, vous le retrouverez sous **Dictée non utilisée** dans le même navigateur et la même base. Si le navigateur refuse l’enregistrement, téléchargez l’audio avant de fermer l’onglet.

## Partager un vocal dans le fil d’une fiche

1. Ouvrez la fiche, puis cliquez sur **Envoyer un message** ou **Note**. Choisissez **Enregistrer une note vocale** avec l’icône en forme d’onde.
2. Parlez, arrêtez et écoutez l’aperçu. Envoyez le message ou publiez la note. Si le fichier est déjà joint à un brouillon, rouvrez ce brouillon pour l’envoyer ou le retirer.
3. Les personnes qui ont accès au message peuvent écouter l’audio. Quand le traitement est terminé, elles peuvent lire la **Transcription** sous le message.

Choisissez **Note** pour partager un enregistrement en interne. Le service de transcription reçoit l’audio. La fiche et le reste du message restent dans Odoo.
