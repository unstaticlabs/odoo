import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("usl_expense_batch_create_or_select", {
    steps: () => [
        {
            content: "The two related expenses are available for grouping",
            trigger:
                ".o_list_table tbody:has(tr:contains('Browser Canada hotel')):has(tr:contains('Browser Canada taxi'))",
        },
        {
            content: "Select the hotel",
            trigger:
                ".o_list_table tbody tr:has(td:contains('Browser Canada hotel')) .o_list_record_selector input",
            run: "click",
        },
        {
            content: "Select the taxi",
            trigger:
                ".o_list_table tbody tr:has(td:contains('Browser Canada taxi')) .o_list_record_selector input",
            run: "click",
        },
        {
            content: "Grouping is the primary multi-expense action",
            trigger: ".o_control_panel button.btn-primary:contains('Add to a Batch')",
            run: "click",
        },
        {
            content: "The compatible travel Batch is proposed",
            trigger: ".o_dialog .o_field_widget[name='batch_id'] input",
        },
        {
            content: "The mixed payer context is previewed before mutation",
            trigger:
                ".o_dialog .o_form_view:has(.o_field_widget[name='employee_paid_total']):has(.o_field_widget[name='company_paid_total'])",
        },
        {
            content: "Add both expenses to the proposed Batch",
            trigger: ".o_dialog footer button:contains('Add to Batch')",
            run: "click",
        },
        {
            content: "The refreshed work list no longer contains grouped lines",
            trigger:
                ".o_list_view:not(:has(tbody tr:contains('Browser Canada hotel'))):not(:has(tbody tr:contains('Browser Canada taxi')))",
        },
    ],
});

registry.category("web_tour.tours").add("usl_expense_batch_focused_review", {
    steps: () => [
        {
            content: "The Batch opens on the focused review surface",
            trigger:
                ".o_form_view:has(.o_usl_batch_summary):has(.o_notebook_headers .nav-link:contains('Expenses'))",
        },
        {
            content: "Shared analytics stay editable while the Batch is draft",
            trigger:
                ".o_form_view .o_field_widget[name='analytic_distribution']:not(.o_readonly_modifier)",
        },
        {
            content: "The context action explains its safe preview behavior",
            trigger:
                "button[name='action_open_context_wizard'][data-tooltip*='Line-specific choices stay unchanged']",
        },
        {
            content: "A real line exception has a compact, specific helper",
            trigger:
                ".o_usl_batch_attention[title*='differ from Batch analytics']",
        },
        {
            content: "The expense list has no destructive trash control",
            trigger:
                ".o_field_widget[name='expense_ids'] .o_list_table:not(:has(.o_list_record_remove))",
        },
        {
            content: "Removal is the single explicit line action",
            trigger:
                ".o_field_widget[name='expense_ids'] button[name='action_return_from_batch']:contains('Remove from Batch')",
        },
    ],
});
