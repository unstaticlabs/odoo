import { registry } from "@web/core/registry";


const journey = (message) => [
    {
        content: "Open the native notifications drawer",
        trigger: ".o_menu_systray i[aria-label='Messages']",
        run: "click",
    },
    {
        content: "Choose Chats",
        trigger: ".o-mail-MessagingMenu-headerFilter:contains('Chats'), .o-mail-MessagingMenu-navbar button:contains('Chats')",
        run: "click",
    },
    {
        content: "Feedback is available from Chats",
        trigger: ".o-usl-FeedbackButton",
        run() {
            Object.defineProperty(navigator.mediaDevices, "getDisplayMedia", {
                configurable: true,
                value: () => Promise.reject(new Error("Screenshot capture skipped by the tour")),
            });
            this.anchor.click();
        },
    },
    {
        content: "The conversational feedback surface opens",
        trigger: ".o-usl-FeedbackPanel textarea",
    },
    {
        content: "Describe the issue",
        trigger: ".o-usl-FeedbackPanel textarea",
        run: `edit ${message}`,
    },
    {
        content: "Page context remains off by default",
        trigger: ".o-usl-FeedbackPanel input#usl_feedback_context:not(:checked), .o-usl-FeedbackPanel textarea",
    },
    {
        content: "Start the saved conversation",
        trigger: ".o-usl-FeedbackPanel button:contains('Start conversation'):not(:disabled)",
        run: "click",
    },
    {
        content: "The task chatter replaces the initial prompt",
        trigger: ".o-usl-FeedbackPanel-conversation .o-mail-Chatter",
    },
    {
        content: "Start another conversation without losing the saved card",
        trigger: ".o-usl-FeedbackPanel header button[aria-label='New feedback']",
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
        content: "Recent feedback is an explicit conversation control",
        trigger: ".o-usl-FeedbackPanel header button[aria-label='Recent feedback']",
        run: "click",
    },
    {
        content: "The recent list preserves the saved feedback card",
        trigger: `.o-usl-FeedbackPanel button:contains('${message}')`,
    },
];

registry.category("web_tour.tours").add("usl_feedback_desktop_journey", {
    steps: () => journey("The desktop status is unclear after reload."),
});

registry.category("web_tour.tours").add("usl_feedback_mobile_journey", {
    steps: () => journey("The mobile status is unclear after reload."),
});
