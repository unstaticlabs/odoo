import { registry } from "@web/core/registry";


const journey = (message, { foldWindow = false, verifyDrawerTabs = false } = {}) => [
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
                      ".o-mail-MessagingMenu-header button:contains('New Message') + .o-usl-FeedbackButton",
              },
              {
                  content: "Choose Chats",
                  trigger: ".o-mail-MessagingMenu-headerFilter:contains('Chats')",
                  run: "click",
              },
              {
                  content: "Feedback remains beside New Message in Chats",
                  trigger:
                      ".o-mail-MessagingMenu-header button:contains('New Message') + .o-usl-FeedbackButton",
              },
              {
                  content: "Choose Channels",
                  trigger: ".o-mail-MessagingMenu-headerFilter:contains('Channels')",
                  run: "click",
              },
              {
                  content: "Feedback remains beside New Message in Channels",
                  trigger:
                      ".o-mail-MessagingMenu-header button:contains('New Message') + .o-usl-FeedbackButton",
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
                  trigger: ".o-usl-FeedbackChatWindow button[aria-label='Fold feedback']",
                  run: "click",
              },
              {
                  content: "The folded feedback conversation uses a native chat bubble",
                  trigger: ".o-usl-FeedbackChatBubble button[aria-label='Open feedback']",
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
        trigger: ".o-usl-FeedbackPanel button:contains('Send feedback'):not(:disabled)",
        run: "click",
    },
    {
        id: "chatter",
        content: "The task chatter replaces the initial prompt",
        trigger: ".o-usl-FeedbackPanel-conversation .o-mail-Chatter",
    },
    {
        id: "new_conversation",
        content: "Start another conversation without losing the saved card",
        trigger: ".o-usl-FeedbackPanel-nav button:contains('New')",
        run: "click",
    },
    {
        id: "tracker",
        content: "Open the clear feedback tracker",
        trigger: ".o-usl-FeedbackPanel-nav button:contains('My feedback')",
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
        trigger: ".o-usl-FeedbackPanel-nav button:contains('My feedback')",
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
        journey("The desktop status is unclear after reload.", {
            foldWindow: true,
            verifyDrawerTabs: true,
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
        send: { text: "Choose **Send feedback**." },
        chatter: {
            text: "Your message becomes a card for the product team, and the conversation turns into its chatter: answers and status changes appear here.",
            screenshot: true,
        },
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
