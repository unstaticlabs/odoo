import { registry } from "@web/core/registry";


const journey = (message, { foldWindow = false, verifyDrawerTabs = false } = {}) => [
    {
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
        content: "Open Feedback",
        trigger: ".o-usl-FeedbackButton",
        run: "click",
    },
    {
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
        content: "Page context remains off by default",
        trigger: ".o-usl-FeedbackPanel input#usl_feedback_context:not(:checked), .o-usl-FeedbackPanel textarea",
    },
    {
        content: "Send the saved feedback",
        trigger: ".o-usl-FeedbackPanel button:contains('Send feedback'):not(:disabled)",
        run: "click",
    },
    {
        content: "The task chatter replaces the initial prompt",
        trigger: ".o-usl-FeedbackPanel-conversation .o-mail-Chatter",
    },
    {
        content: "Start another conversation without losing the saved card",
        trigger: ".o-usl-FeedbackPanel-nav button:contains('New')",
        run: "click",
    },
    {
        content: "Open the clear feedback tracker",
        trigger: ".o-usl-FeedbackPanel-nav button:contains('My feedback')",
        run: "click",
    },
    {
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

registry.category("web_tour.tours").add("usl_feedback_mobile_journey", {
    steps: () => journey("The mobile status is unclear after reload."),
});
