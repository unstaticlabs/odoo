import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { _t } from "@web/core/l10n/translation";


const journey = (message, { foldWindow = false, verifyDrawerTabs = false, sendToTeam = false } = {}) => [
    {
        id: "open_drawer",
        content: "Open the native notifications drawer",
        trigger: ".o_menu_systray i[aria-label='Messages']",
        run: "click",
    },
    ...(verifyDrawerTabs
        ? [
              {
                  content: "Feedback is beside New Message in Notifications",
                  trigger:
                      `.o-mail-MessagingMenu-header button:contains('${_t("New Message")}') + .o-usl-FeedbackButton`,
              },
              {
                  content: "Choose Chats",
                  trigger: ".o-mail-MessagingMenu-headerFilter:contains('Chats')",
                  run: "click",
              },
              {
                  content: "Feedback remains beside New Message in Chats",
                  trigger:
                      `.o-mail-MessagingMenu-header button:contains('${_t("New Message")}') + .o-usl-FeedbackButton`,
              },
              {
                  content: "Choose Channels",
                  trigger: `.o-mail-MessagingMenu-headerFilter:contains('${_t("Channels")}')`,
                  run: "click",
              },
              {
                  content: "Feedback remains beside New Message in Channels",
                  trigger:
                      `.o-mail-MessagingMenu-header button:contains('${_t("New Message")}') + .o-usl-FeedbackButton`,
              },
          ]
        : []),
    {
        id: "open_feedback",
        content: "Open Feedback",
        trigger: ".o-usl-FeedbackButton",
        run: "click",
    },
    {
        id: "conversation",
        content: "The drawer closes and a native floating conversation opens",
        trigger: ".o-usl-FeedbackChatWindow .o-usl-FeedbackPanel textarea",
    },
    {
        content: "The page preview is prepared locally after messaging closes",
        trigger: ".o-usl-FeedbackPanel-screenshot img[src^='blob:']",
        run() {
            if (document.querySelector(".o-mail-MessagingMenu")) {
                throw new Error("The messaging drawer remained visible during page preview.");
            }
        },
    },
    {
        id: "describe",
        content: "Describe the issue",
        trigger: ".o-usl-FeedbackPanel textarea",
        run: `edit ${message}`,
    },
    ...(foldWindow
        ? [
              {
                  content: "Fold the feedback conversation into the ChatHub",
                  trigger: ".o-usl-FeedbackChatWindow .o-usl-FeedbackPanel-fold",
                  run: "click",
              },
              {
                  content: "The folded feedback conversation uses a native chat bubble",
                  trigger: ".o-usl-FeedbackChatBubble .o-usl-FeedbackChatBubble-button",
                  run: "click",
              },
              {
                  content: "Reopening preserves the current draft",
                  trigger: ".o-usl-FeedbackChatWindow .o-usl-FeedbackPanel textarea",
                  run() {
                      if (this.anchor.value !== message) {
                          throw new Error("The folded feedback draft was not preserved.");
                      }
                  },
              },
          ]
        : []),
    {
        id: "context_off",
        content: "The page-details choice is explicit before sending",
        trigger: ".o-usl-FeedbackPanel input#usl_feedback_context:not(:checked), .o-usl-FeedbackPanel textarea",
    },
    {
        id: "send",
        content: "Send the saved feedback",
        trigger: `.o-usl-FeedbackPanel button:contains('${_t("Send feedback")}'):not(:disabled)`,
        run: "click",
    },
    {
        id: "chatter",
        content: "The task chatter replaces the initial prompt",
        trigger: ".o-usl-FeedbackPanel-conversation .o-mail-Chatter",
    },
    ...(sendToTeam
        ? [
              {
                  id: "confirm_draft",
                  content: "Confirm the prepared draft for the product team",
                  trigger: ".o-usl-FeedbackPanel-readyBar footer button.btn-primary:not(:disabled)",
                  run: "click",
              },
              {
                  id: "team_received",
                  content: "The conversation confirms handoff to the product team",
                  trigger: ".o-usl-FeedbackPanel-sentBar",
              },
          ]
        : []),
    {
        id: "new_conversation",
        content: "Start another conversation without losing the saved card",
        trigger: `.o-usl-FeedbackPanel-nav button:contains('${_t("New")}')`,
        run: "click",
    },
    {
        id: "tracker",
        content: "Open the clear feedback tracker",
        trigger: `.o-usl-FeedbackPanel-nav button:contains('${_t("My feedback")}')`,
        run: "click",
    },
    {
        id: "resume",
        content: "Resume the saved card after returning to the draft",
        trigger: `.o-usl-FeedbackPanel button:contains('${message}')`,
        run: "click",
    },
    {
        content: "The resumed conversation restores its native chatter",
        trigger: ".o-usl-FeedbackPanel-conversation .o-mail-Chatter",
    },
    {
        content: "Saved feedback is an explicit conversation control",
        trigger: `.o-usl-FeedbackPanel-nav button:contains('${_t("My feedback")}')`,
        run: "click",
    },
    {
        id: "recent_list",
        content: "The recent list preserves the saved feedback card",
        trigger: `.o-usl-FeedbackPanel button:contains('${message}')`,
    },
];

registry.category("web_tour.tours").add("usl_feedback_desktop_journey", {
    steps: () =>
        journey(user.lang.startsWith("fr") ? "Le statut est difficile à comprendre après le rechargement." : "After I reload the page, the status is unclear.", {
            foldWindow: true,
            verifyDrawerTabs: true,
            sendToTeam: true,
        }),
});

registry.category("usl_docs.journeys").add("usl_feedback_desktop_journey", {
    id: "send-feedback-from-any-page",
    type: "how-to",
    title: "Send feedback from any page",
    description: "Report a problem, suggest an improvement or ask a question without leaving the screen you are on, then follow the answer in My feedback.",
    persona: "everyone",
    lang: "en",
    app: "Feedback",
    models: ["project.task"],
    steps: {
        open_drawer: {
            text: "Open the **Messages** drawer from the top bar. **Feedback** sits beside **New Message** in every tab.",
            screenshot: true,
        },
        open_feedback: { text: "Choose **Feedback**." },
        conversation: {
            text: "The drawer closes and a floating conversation opens, like a chat. Odoo prepares a preview of the page you are on; it stays on your computer until you decide to send it.",
            screenshot: true,
        },
        describe: {
            text: "Describe what you saw and what you expected, in your own words. One message is enough; the product team asks if it needs more.",
        },
        context_off: {
            text: "**Page details** is ticked when the screen you are on gives useful context: the action, the record you can read, the company and the release, never the address, form values or passwords. Untick it when the page does not matter.",
        },
        send: { text: "Choose **Send feedback** to start the conversation. The assistant prepares a draft for your review." },
        chatter: {
            text: "Read the draft in the conversation. You can add a clarification or edit it before sharing it with the product team.",
            screenshot: true,
        },
        confirm_draft: { text: "When the draft describes what you meant, choose **Send feedback** on the draft." },
        team_received: { text: "The conversation confirms that the product team has received your feedback. Further replies appear here.", screenshot: true },
        new_conversation: { text: "**New** starts another conversation. The card you just sent is kept." },
        tracker: { text: "**My feedback** lists every card you sent with its current status." },
        resume: { text: "Choose a card to reopen its conversation and reply." },
        recent_list: {
            text: "Come back to **My feedback** at any time from the same drawer to see where each report stands.",
            screenshot: true,
        },
    },
});

registry.category("web_tour.tours").add("usl_feedback_mobile_journey", {
    steps: () => journey("The mobile status is unclear after reload."),
});

// The maintainer board is a set of primary views bound to their own action. These
// journeys prove both of its create routes reach the governed board through them,
// rather than the stray project-less task the bare create="true" used to make.
// They are two tours because an empty board renders the no-content helper instead
// of its columns, so the column quick add only exists once a card does.
registry.category("web_tour.tours").add("usl_feedback_maintainer_form_journey", {
    steps: () => [
        {
            content: "New opens the full maintainer card",
            trigger: ".o_control_panel .o-kanban-button-new",
            run: "click",
        },
        {
            content: "Title the card",
            trigger: ".o_form_view div[name='name'] input",
            run: "edit Batch export presets",
        },
        {
            content: "The category is a real choice, not a read-only badge",
            trigger: ".o_form_view div[name='usl_feedback_category'] .o_select_menu_toggler",
            run: "click",
        },
        {
            content: "Classify it",
            trigger: ".o_select_menu_menu .o_select_menu_item:contains('UX')",
            run: "click",
        },
        {
            content: "The assistant status is server-owned",
            trigger:
                ".o_form_view div[name='usl_feedback_agent_state']:contains('Sent to product team')",
        },
        {
            content: "Park it in the Icebox",
            trigger: ".o_statusbar_status button:contains('Icebox')",
            run: "click",
        },
        {
            content: "Save the card",
            trigger: ".o_form_button_save",
            run: "click",
        },
        {
            content: "The card is saved on the governed board",
            trigger: ".o_form_view .o_form_saved",
        },
    ],
});

registry.category("web_tour.tours").add("usl_feedback_maintainer_quick_journey", {
    steps: () => [
        {
            content: "Add to a column straight from the board",
            trigger: ".o_kanban_header:contains('Inbox') .o_kanban_quick_add",
            run: "click",
        },
        {
            content: "A title alone is enough",
            trigger: ".o_kanban_quick_create div[name='name'] input",
            run: "edit Keyboard shortcut for triage",
        },
        {
            content: "Add it to the board",
            trigger: ".o_kanban_quick_create .o_kanban_add",
            run: "click",
        },
        {
            content: "The quick card is on the shared board",
            trigger: ".o_kanban_view .o_kanban_record:contains('Keyboard shortcut for triage')",
        },
    ],
});

registry.category("usl_docs.journey_translations").add("usl_feedback_desktop_journey.fr", {
    id: "envoyer-un-retour-depuis-une-page",
    title: "Envoyer un retour depuis une page Odoo",
    description: "Signalez une difficulté, proposez une amélioration ou posez une question depuis la page concernée, puis retrouvez les réponses de l’équipe produit.",
    app: "Retours",
    steps: {
        open_drawer: "Ouvrez **Messages** dans la barre du haut. Le bouton de retour se trouve à côté de **Nouveau message**, dans chaque onglet.",
        open_feedback: "Cliquez sur le bouton de retour.",
        conversation: "Une conversation s’ouvre. Odoo prépare un aperçu de la page. Il reste sur votre appareil jusqu’à ce que vous choisissiez de l’envoyer.",
        describe: "Décrivez ce que vous vouliez faire, puis ce que vous avez observé.",
        context_off: "Vérifiez **Détails de la page**. Cette option joint la page de travail, la fiche accessible, la société et la version. Décochez-la si la page n’aide pas à comprendre le retour.",
        send: "Cliquez sur **Envoyer un retour** pour démarrer la conversation. L’assistant prépare un brouillon à relire.",
        chatter: "Relisez le brouillon dans la conversation. Vous pouvez ajouter une précision ou modifier le texte avant de le transmettre à l’équipe produit.",
        confirm_draft: "Lorsque le brouillon exprime bien votre demande, cliquez sur **Envoyer un retour** sur la fiche du brouillon.",
        team_received: "Le bandeau confirme la transmission à l’équipe produit. Retrouvez ses réponses dans cette conversation.",
        new_conversation: "Cliquez sur **Nouveau** pour préparer un autre retour. Le premier reste accessible.",
        tracker: "Dans **Mes retours**, retrouvez vos demandes et leur statut.",
        resume: "Choisissez une demande pour reprendre la conversation ou ajouter une précision.",
        recent_list: "Revenez dans **Mes retours** pour consulter l’avancement depuis n’importe quelle page.",
    },
});
