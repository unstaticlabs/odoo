import { registry } from "@web/core/registry";

const fillAndSubmit = (summary) => [
    {
        content: "The focused feedback form opens",
        trigger: ".o_dialog .o_usl_feedback_submission",
    },
    {
        content: "A summary is required",
        trigger: ".o_usl_feedback_submission .o_field_widget[name='summary'] input",
        run: `edit ${summary}`,
    },
    {
        content: "The description accepts useful detail",
        trigger: ".o_usl_feedback_submission .odoo-editor-editable",
        run: "editor The current status is hard to understand after a reload.",
    },
    {
        content: "Page context is an explicit choice",
        trigger: ".o_usl_feedback_submission .o_field_widget[name='include_page_context'] input:checked",
        run: "click",
    },
    {
        content: "Page context can be left out",
        trigger: ".o_usl_feedback_submission .o_field_widget[name='include_page_context'] input:not(:checked)",
    },
    {
        content: "Send the feedback",
        trigger: ".o_dialog footer button[name='action_submit']",
        run: "click",
    },
    {
        content: "The dialog closes and a calm confirmation is shown",
        trigger: ".o_notification:contains('Feedback sent')",
    },
];

registry.category("web_tour.tours").add("usl_feedback_desktop_journey", {
    steps: () => [
        {
            content: "Feedback is available globally from the native user menu",
            trigger: "button.o_user_menu",
            run: "click",
        },
        {
            content: "Send feedback is discoverable without another systray icon",
            trigger: "[data-menu='usl_send_feedback']",
            run: "click",
        },
        ...fillAndSubmit("Desktop feedback journey"),
    ],
});

registry.category("web_tour.tours").add("usl_feedback_mobile_journey", {
    steps: () => [
        {
            content: "Open the native mobile menu",
            trigger: ".o_mobile_menu_toggle",
            run: "click",
        },
        {
            content: "Feedback remains available on a narrow screen",
            trigger: ".o_user_menu_mobile a:contains('Send feedback')",
            run: "click",
        },
        ...fillAndSubmit("Mobile feedback journey"),
    ],
});
