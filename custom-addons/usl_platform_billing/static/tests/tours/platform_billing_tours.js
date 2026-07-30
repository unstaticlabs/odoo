import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("usl_platform_billing_manager_journey", {
    steps: () => [
        {
            content: "Open a prepared platform billing session",
            trigger:
                ".o_form_view:has(.o_statusbar_status button[data-value='draft'].o_arrow_button_current)",
        },
        {
            content: "Run the accounting preflight",
            trigger: "button[name='action_check']",
            run: "click",
        },
        {
            content: "Generate customer invoices and commission bills",
            trigger: "button[name='action_generate_documents']",
            run: "click",
        },
        {
            content: "Post the documents and compensate commission",
            trigger: "button[name='action_post_documents']",
            run: "click",
        },
        {
            content: "Reconcile the selected bank receipt through OCA",
            trigger: "button[name='action_reconcile_bank']",
            run: "click",
        },
        {
            content: "The complete session is paid",
            trigger:
                ".o_form_view .o_statusbar_status button[data-value='paid'].o_arrow_button_current",
        },
        {
            content: "Open the generated native accounting documents",
            trigger: "button[name='action_open_generated_moves']",
            run: "click",
        },
        {
            content: "Native posted accounting documents remain available",
            trigger: ".o_list_view .o_data_row td[name='name']",
        },
    ],
});

registry.category("web_tour.tours").add("usl_platform_billing_reviewer_journey", {
    steps: () => [
        {
            content: "The reviewer can inspect the paid session",
            trigger:
                ".o_form_view .o_statusbar_status button[data-value='paid'].o_arrow_button_current",
        },
        {
            content: "The reviewer has no workflow mutation controls",
            trigger:
                ".o_form_view:not(:has(button[name='action_check'])):not(:has(button[name='action_generate_documents'])):not(:has(button[name='action_post_documents'])):not(:has(button[name='action_reconcile_bank']))",
        },
        {
            content: "The reviewer can inspect native generated entries",
            trigger: "button[name='action_open_generated_moves']",
            run: "click",
        },
        {
            content: "The reviewer can read the native move list",
            trigger: ".o_list_view .o_data_row td[name='name']",
        },
        {
            content: "No record creation control is available",
            trigger: ".o_control_panel:not(:has(button.o_list_button_add))",
        },
    ],
});
