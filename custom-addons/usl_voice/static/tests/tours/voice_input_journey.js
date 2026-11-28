import { registry } from "@web/core/registry";

const taskTitle = ".o_form_view .o_field_widget[name='name'] textarea";

registry.category("web_tour.tours").add("usl_voice_input_journey", {
    steps: () => [
        {
            id: "field",
            trigger: taskTitle,
            content: "The task title is editable.",
            run: "click",
        },
        {
            id: "dictate",
            trigger: ".o-usl-dictation-mic",
            content: "The focused field has a microphone.",
            run: "click",
        },
        {
            id: "stop_dictation",
            trigger: ".o-usl-dictation-pill:contains('0:01') [data-usl-action='stop']",
            content: "The dictation timer is running.",
            run: "click",
        },
        {
            id: "retry",
            trigger: ".o-usl-recovered:has([data-usl-action='download']) [data-usl-action='retry']",
            content: "A failed transcription offers Retry and Download.",
        },
        { trigger: ".o-usl-recovered [data-usl-action='retry']", run: "click" },
        {
            id: "read_text",
            trigger: `${taskTitle}:value(/example|Exemple/)`,
            content: "Odoo inserted the transcript in the selected field.",
        },
        { trigger: ".o_form_button_save", run: "click" },
        {
            id: "open_note",
            trigger: ".o-mail-Chatter-logNote",
            content: "The chatter offers an internal note.",
            run: "click",
        },
        {
            id: "voice_note",
            trigger: ".o-mail-Composer button:has(.o-usl-icon-voice-note)",
            content: "The composer offers voice recording.",
            run: "click",
        },
        {
            id: "stop_note",
            trigger: ".o-usl-voice-bar:contains('0:01') button:has(.fa-stop)",
            content: "The voice note is recording.",
            run: "click",
        },
        {
            id: "preview",
            trigger: ".o-mail-Composer .o-mail-VoicePlayer:not(:contains('-- : --')) button.fa-play",
            content: "The draft has a playable voice note.",
            run: "click",
        },
        { trigger: ".o-mail-Composer .o-mail-VoicePlayer button.fa-stop" },
        {
            id: "submit",
            trigger: ".o-mail-Composer .o-mail-Composer-send:not(:disabled)",
            content: "The internal note is ready to post.",
            run: "click",
        },
        {
            id: "read_note",
            trigger: ".o-mail-Message:has(.o-mail-VoicePlayer) .o-usl-transcript p",
            content: "The posted note contains the audio and the transcript.",
        },
    ],
});

registry.category("usl_docs.journeys").add("usl_voice_input_journey", {
    id: "dictate-text-and-send-voice-notes",
    type: "how-to",
    title: "Dictate into a field or record a voice note",
    description: "Turn speech into text in an Odoo field, or share a voice note and its transcript in the chatter.",
    persona: "employee",
    lang: "en",
    app: "Chatter",
    models: ["project.task", "mail.message"],
    steps: {
        field: { text: "To dictate text, place the cursor in an editable field." },
        dictate: { text: "Choose the microphone or press **Alt+Shift+D**. Speak in French or English." },
        stop_dictation: { text: "Choose **Stop** when you finish speaking." },
        retry: { text: "If transcription fails or takes too long, choose **Retry transcription** or **Download audio**. A recording saved locally can reappear under **Unused dictation** in the same browser and database. If browser storage is unavailable, download the audio before closing the tab.", screenshot: true },
        read_text: { text: "Check the inserted text before saving or sending it. If you switched fields while waiting, choose **Copy transcript** and paste the text where it belongs.", screenshot: true },
        open_note: { text: "To share an audio recording, open the record’s chatter. Choose **Log note** for internal readers or **Send message** to send it as a message." },
        voice_note: { text: "Choose **Record a voice note**, the waveform icon, and speak." },
        stop_note: { text: "Choose **Stop and preview**." },
        preview: { text: "Listen to the draft recording. If it is already attached to a saved draft, reopen that draft to send or remove it." },
        submit: { text: "When the recording is ready, post the note or send the message." },
        read_note: { text: "Readers with access can play the audio. Once processing finishes, they can read **Transcript** under the message. The transcription service receives the audio. The record and surrounding message stay in Odoo.", screenshot: true },
    },
});

registry.category("usl_docs.journey_translations").add("usl_voice_input_journey.fr", {
    id: "dicter-et-envoyer-une-note-vocale",
    title: "Dicter dans un champ et envoyer une note vocale",
    description: "Transformez la parole en texte dans un champ Odoo, ou partagez un vocal et sa transcription dans le fil d’une fiche.",
    app: "Fil de discussion",
    steps: {
        field: "Pour dicter du texte, placez le curseur dans un champ modifiable.",
        dictate: "Sélectionnez le microphone ou appuyez sur **Alt+Maj+D**. Parlez en français ou en anglais.",
        stop_dictation: "Arrêtez la dictée lorsque vous avez fini de parler.",
        retry: "Si la transcription échoue ou tarde, choisissez **Réessayer la transcription** ou **Télécharger l’audio**. Un enregistrement conservé localement peut réapparaître sous **Dictée non utilisée** dans le même navigateur et la même base. Si le navigateur ne peut pas le conserver, téléchargez l’audio avant de fermer l’onglet.",
        read_text: "Relisez le texte inséré avant de l’enregistrer ou de l’envoyer. Si vous avez changé de champ pendant l’attente, copiez la transcription et collez-la à l’endroit voulu.",
        open_note: "Pour partager un enregistrement, ouvrez le fil de la fiche. Choisissez **Note** pour un partage interne ou **Envoyer message** pour écrire aux destinataires du message.",
        voice_note: "Choisissez **Enregistrer une note vocale**, l’icône en forme d’onde, puis parlez.",
        stop_note: "Arrêtez l’enregistrement pour afficher l’aperçu.",
        preview: "Écoutez l’enregistrement. S’il est déjà joint à un brouillon, rouvrez ce brouillon pour l’envoyer ou le retirer.",
        submit: "Quand l’enregistrement est prêt, publiez la note ou envoyez le message.",
        read_note: "Les personnes qui ont accès au message peuvent écouter l’audio. Après le traitement, elles peuvent lire la **Transcription** sous le message. Le service de transcription reçoit l’audio. La fiche et le reste du message restent dans Odoo.",
    },
});
