import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("usl_einvoice_manager_reception", {
    steps: () => [
        {
            content: "Open the company's electronic-invoice readiness",
            trigger: ".o_list_view .o_data_row td[name='name']",
            run: "click",
        },
        {
            content: "The focused readiness screen is open",
            trigger: "button[name='action_rebuild_run_einvoice_acceptance_test']",
        },
        {
            content: "Run the safe offline reception journey",
            trigger: "button[name='action_rebuild_run_einvoice_acceptance_test']",
            run: "click",
        },
        {
            content: "The safe test reception is explicit",
            trigger: ".o_form_view .ribbon span:contains('Safe test')",
        },
        {
            content: "The structured document created a draft bill",
            trigger:
                ".o_form_view .o_statusbar_status button[data-value='bill_created'].o_arrow_button_current",
        },
        {
            content: "Open the native vendor bill",
            trigger: "button[name='action_open_bill']",
            run: "click",
        },
        {
            content: "A native draft vendor bill is open",
            trigger:
                ".o_form_view:has(.o_statusbar_status button[data-value='draft'].o_arrow_button_current):has(button[name='action_post'])",
        },
        {
            content: "Open the preserved structured evidence",
            trigger: "button[role='tab']:contains('E-Invoice Evidence')",
            run: "click",
        },
        {
            content: "The bill retains the safe-test reception evidence",
            trigger:
                ".o_field_widget[name='rebuild_einvoice_reception_ids'] .o_data_row:contains('Draft Bill Created')",
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
            trigger: "button[role='tab']:contains('E-Invoice Evidence')",
            run: "click",
        },
        {
            content: "The original structured document remains visible",
            trigger:
                ".o_field_widget[name='rebuild_einvoice_reception_ids'] .o_data_row:contains('Draft Bill Created')",
        },
    ],
});
