import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("usl_einvoice_manager_reception", {
    steps: () => [
        {
            content: "Open the company's electronic-invoice setup",
            trigger: ".o_list_view .o_data_row td[name='name']",
            run: "click",
        },
        {
            content: "The focused setup screen is open",
            trigger: "button[name='action_rebuild_run_einvoice_acceptance_test']",
        },
        {
            content: "Run the safe reception self-check",
            trigger: "button[name='action_rebuild_run_einvoice_acceptance_test']",
            run: "click",
        },
        {
            content: "The self-check passed without retaining test accounting",
            trigger: ".o_notification:contains('Reception is ready')",
        },
    ],
});

registry.category("web_tour.tours").add("usl_einvoice_readonly_reception", {
    steps: () => [
        {
            content: "The read-only accountant can inspect reception evidence",
            trigger:
                ".o_form_view .o_statusbar_status button[data-value='bill_created'].o_arrow_button_current",
        },
        {
            content: "The read-only view has no processing mutation",
            trigger: "body:not(:has(button[name='action_retry_processing']))",
        },
        {
            content: "Open the resulting vendor bill",
            trigger: "button[name='action_open_bill']",
            run: "click",
        },
        {
            content: "The read-only accountant cannot post the draft bill",
            trigger: ".o_form_view:not(:has(button[name='action_post']))",
        },
        {
            content: "Open evidence from the native bill",
            trigger:
                "button[name='action_open_rebuild_einvoice_reception']",
            run: "click",
        },
        {
            content: "The original structured document remains visible",
            trigger:
                ".o_form_view .o_field_widget[name='attachment_id']",
        },
    ],
});
