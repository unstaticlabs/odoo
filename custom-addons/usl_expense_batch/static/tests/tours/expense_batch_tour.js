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

registry.category("web_tour.tours").add("usl_expense_batch_receipt_capture", {
    steps: () => [
        {
            content: "Native receipt upload remains available on the expense work list",
            trigger: ".o_list_view .o_button_upload_expense",
        },
        {
            content: "Open the captured expense evidence",
            trigger:
                ".o_list_table tbody tr.o_data_row td[name='name']:contains('Browser receipt evidence')",
            run: "click",
        },
        {
            content: "The captured receipt count is available on its expense",
            trigger:
                ".o_form_view:has(.o_field_widget[name='product_id']) button[aria-label='Attach files']:contains('1')",
            run: "click",
        },
        {
            content: "The receipt evidence is inspectable from the expense",
            trigger: ".o-mail-AttachmentBox:contains('browser-receipt.pdf')",
        },
        {
            content: "The payer choice remains explicit after receipt capture",
            trigger: ".o_form_view .o_field_widget[name='payment_mode']",
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
            content: "Shared analytics stay editable while the Batch is open",
            trigger:
                ".o_form_view .o_field_widget[name='analytic_distribution']:not(.o_readonly_modifier)",
        },
        {
            content: "The context action explains its safe preview behavior",
            trigger:
                "button[name='action_open_context_wizard'][data-tooltip*='Line-specific choices remain unchanged']",
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

registry.category("web_tour.tours").add("usl_expense_batch_submitter_handoff", {
    steps: () => [
        {
            content: "The submitter sees a complete mixed-payer Batch",
            trigger:
                ".o_form_view:has(.o_usl_batch_summary):has(.o_field_widget[name='expense_ids']):has(button[name='action_submit'])",
        },
        {
            content: "Submit only the draft expenses for manager review",
            trigger: "button[name='action_submit']:contains('Submit batch')",
            run: "click",
            expectUnloadPage: true,
        },
    ],
});

registry.category("web_tour.tours").add("usl_expense_batch_manager_handoff", {
    steps: () => [
        {
            content: "The manager receives the submitted Batch",
            trigger: "button[name='action_approve']:contains('Approve batch')",
        },
        {
            content: "Approve only the submitted expenses",
            trigger: "button[name='action_approve']:contains('Approve batch')",
            run: "click",
            expectUnloadPage: true,
        },
    ],
});

registry.category("web_tour.tours").add("usl_expense_batch_accountant_handoff", {
    steps: () => [
        {
            content: "Accounting receives the approved mixed-payer Batch",
            trigger: "button[name='action_post']:contains('Post batch')",
        },
        {
            content: "Post the company-paid side and open native reimbursement",
            trigger: "button[name='action_post']:contains('Post batch')",
            run: "click",
        },
        {
            content: "Complete the native employee-paid posting wizard",
            trigger:
                ".o_dialog footer button[name='action_post_entry']:contains('Post Expenses')",
            run: "click",
        },
        {
            content: "Both mixed-payer journal entries are available for review",
            trigger: ".o_list_view .o_list_table tbody:has(tr:nth-child(2))",
        },
    ],
});

registry.category("web_tour.tours").add("usl_expense_batch_readonly_audit", {
    steps: () => [
        {
            content: "The read-only accountant can inspect the Batch evidence",
            trigger:
                ".o_form_view:has(.o_usl_batch_summary):has(.o_field_widget[name='expense_ids'])",
        },
        {
            content: "No lifecycle or line-correction action is exposed",
            trigger:
                ".o_form_view:not(:has(header button)):not(:has(button[name='action_return_from_batch']))",
        },
        {
            content: "Accounting history remains available for audit",
            trigger: ".o_notebook_headers .nav-link:contains('Accounting and history')",
            run: "click",
        },
        {
            content: "Accounting reconciliation context is visible but read-only",
            trigger:
                ".o_form_view .o_field_widget[name='accounting_reconciliation_state'].o_readonly_modifier",
        },
    ],
});
