---
title: Dictate into a field or record a voice note
type: how-to
description: Turn speech into text in an Odoo field, or share a voice note and its transcript in the chatter.
lang: en
persona: employee
app: Chatter
models:
  - mail.message
source:
  - custom-addons/usl_voice/README.md
  - custom-addons/usl_voice/static/src/dictation.xml
  - custom-addons/usl_voice/static/src/chatter.js
generated: false
---

# Dictate into a field or record a voice note

Use **dictation** to fill an editable field. Use a **voice note** to share the recording in a record’s chatter.

## Dictate into a field

1. Place the cursor where the text should go. Select the microphone or press **Alt+Shift+D**.
2. Speak in French or English. Select **Stop** and check the text before saving the record or sending your message.
3. If you moved to another field during transcription, use the offered copy action to paste the text where you want it.

If transcription is delayed or fails, use **Retry transcription** or **Download audio**. When Odoo can save the audio locally, reopening the same browser and database brings it back under **Unused dictation**. If browser storage is unavailable, download the audio before closing the tab.

## Share a voice note in the chatter

1. Open the record and choose **Send message** or **Log note**. Choose **Record a voice note** using the waveform icon.
2. Speak, stop and listen to the preview. Send the message or log the note when it is ready. If the audio is already attached to a draft, reopen that draft to send or remove it.
3. Readers with access can play the recording in the chatter. When transcription finishes, they can read **Transcript** under the message.

Choose **Log note** when the recording is for internal readers. The transcription service receives the audio. The record and surrounding message stay in Odoo.
