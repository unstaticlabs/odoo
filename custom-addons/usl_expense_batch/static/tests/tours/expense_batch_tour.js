import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("usl_expense_batch_create_or_select", {
    steps: () => [
        {
            id: "work_list",
            content: "The two related expenses are available for grouping",
            trigger:
                ".o_list_table tbody:has(tr:contains('Browser Canada hotel')):has(tr:contains('Browser Canada taxi'))",
        },
        {
            id: "select_first",
            content: "Select the hotel",
            trigger:
                ".o_list_table tbody tr:has(td:contains('Browser Canada hotel')) .o_list_record_selector input",
            run: "click",
        },
        {
            id: "select_second",
            content: "Select the taxi",
            trigger:
                ".o_list_table tbody tr:has(td:contains('Browser Canada taxi')) .o_list_record_selector input",
            run: "click",
        },
        {
            id: "add_to_batch",
            content: "Grouping is the primary multi-expense action",
            trigger: ".o_control_panel button.btn-primary:contains('Add to a Batch')",
            run: "click",
        },
        {
            id: "proposed_batch",
            content: "The compatible travel Batch is proposed",
            trigger: ".o_dialog .o_field_widget[name='batch_id'] input",
        },
        {
            id: "preview",
            content: "The mixed payer context is previewed before mutation",
            trigger:
                ".o_dialog .o_form_view:has(.o_field_widget[name='employee_paid_total']):has(.o_field_widget[name='company_paid_total'])",
        },
        {
            id: "confirm",
            content: "Add both expenses to the proposed Batch",
            trigger: ".o_dialog footer button:contains('Add to Batch')",
            run: "click",
        },
        {
            id: "grouped",
            content: "The refreshed work list no longer contains grouped lines",
            trigger:
                ".o_list_view:not(:has(tbody tr:contains('Browser Canada hotel'))):not(:has(tbody tr:contains('Browser Canada taxi')))",
        },
    ],
});

registry.category("usl_docs.journeys").add("usl_expense_batch_create_or_select", {
    id: "group-expenses-into-a-batch",
    type: "how-to",
    title: "Group related expenses into a Batch",
    description: "Select the expenses that belong together and add them to a proposed Batch so they share one business context.",
    persona: "employee",
    lang: "en",
    app: "Expenses",
    models: ["hr.expense", "usl.expense.batch"],
    steps: {
        work_list: {
            text: "Open **Expenses > My Expenses**. Expenses that belong together, such as a hotel and a taxi from one trip, sit in the same work list.",
            screenshot: true,
        },
        select_first: { text: "Tick the first expense of the trip." },
        select_second: { text: "Tick the other expenses of the same trip." },
        add_to_batch: {
            text: "Choose **Add to a Batch**, the primary action once several expenses are selected.",
        },
        proposed_batch: {
            text: "Odoo proposes a compatible Batch from the employee, the company, the dates and the analytics. Keep it, or create a new one.",
        },
        preview: {
            text: "Check the preview: what the employee paid and what the company paid are totalled separately before anything changes.",
            screenshot: true,
        },
        confirm: { text: "Choose **Add to Batch**." },
        grouped: {
            text: "The work list no longer shows the grouped lines; they now live on the Batch, where you review and submit them together.",
            screenshot: true,
        },
    },
});

registry.category("web_tour.tours").add("usl_expense_batch_receipt_capture", {
    steps: () => [
        {
            id: "upload",
            content: "Native receipt upload remains available on the expense work list",
            trigger: ".o_list_view .o_button_upload_expense",
        },
        {
            id: "open_expense",
            content: "Open the captured expense evidence",
            trigger:
                ".o_list_table tbody tr.o_data_row td[name='name']:contains('Browser receipt evidence')",
            run: "click",
        },
        {
            id: "attachments",
            content: "The captured receipt count is available on its expense",
            trigger:
                ".o_form_view:has(.o_field_widget[name='product_id']) button[aria-label='Attach files']:contains('1')",
            run: "click",
        },
        {
            id: "receipt",
            content: "The receipt evidence is inspectable from the expense",
            trigger: ".o-mail-AttachmentBox:contains('browser-receipt.pdf')",
        },
        {
            id: "payer",
            content: "The payer choice remains explicit after receipt capture",
            trigger: ".o_form_view .o_field_widget[name='payment_mode']",
        },
    ],
});

registry.category("web_tour.tours").add("usl_expense_batch_focused_review", {
    steps: () => [
        {
            id: "batch",
            content: "The Batch opens on the focused review surface",
            trigger:
                ".o_form_view:has(.o_usl_batch_summary):has(.o_notebook_headers .nav-link:contains('Expenses'))",
        },
        {
            id: "analytics",
            content: "Shared analytics stay editable while the Batch is open",
            trigger:
                ".o_form_view .o_field_widget[name='analytic_distribution']:not(.o_readonly_modifier)",
        },
        {
            id: "context_action",
            content: "The context action explains its safe preview behavior",
            trigger:
                "button[name='action_open_context_wizard'][data-tooltip*='Line-specific choices remain unchanged']",
        },
        {
            id: "exception",
            content: "A real line exception has a compact, specific helper",
            trigger:
                ".o_usl_batch_attention[title*='differ from Batch analytics']",
        },
        {
            id: "no_trash",
            content: "The expense list has no destructive trash control",
            trigger:
                ".o_field_widget[name='expense_ids'] .o_list_table:not(:has(.o_list_record_remove))",
        },
        {
            id: "remove",
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
            id: "approved",
            content: "Accounting receives the approved mixed-payer Batch",
            trigger: "button[name='action_post']:contains('Post batch')",
        },
        {
            id: "post",
            content: "Post the company-paid side and open native reimbursement",
            trigger: "button[name='action_post']:contains('Post batch')",
            run: "click",
        },
        {
            id: "post_entries",
            content: "Complete the native employee-paid posting wizard",
            trigger:
                ".o_dialog footer button[name='action_post_entry']:contains('Post Expenses')",
            run: "click",
        },
        {
            id: "entries",
            content: "Both mixed-payer journal entries are available for review",
            trigger: ".o_list_view .o_list_table tbody:has(tr:nth-child(2))",
        },
    ],
});

registry.category("web_tour.tours").add("usl_expense_batch_readonly_audit", {
    steps: () => [
        {
            id: "evidence",
            content: "The read-only accountant can inspect the Batch evidence",
            trigger:
                ".o_form_view:has(.o_usl_batch_summary):has(.o_field_widget[name='expense_ids'])",
        },
        {
            id: "no_actions",
            content: "No lifecycle or line-correction action is exposed",
            trigger:
                ".o_form_view:not(:has(header button)):not(:has(button[name='action_return_from_batch']))",
        },
        {
            id: "history",
            content: "Accounting history remains available for audit",
            trigger: ".o_notebook_headers .nav-link:contains('Accounting and history')",
            run: "click",
        },
        {
            id: "reconciliation",
            content: "Accounting reconciliation context is visible but read-only",
            trigger:
                ".o_form_view .o_field_widget[name='accounting_reconciliation_state'].o_readonly_modifier",
        },
    ],
});

registry.category("usl_docs.journeys").add("usl_expense_batch_receipt_capture", {
    id: "check-the-receipt-on-an-expense",
    type: "how-to",
    title: "Check the receipt attached to an expense",
    description: "Find the receipt behind an expense, open it, and confirm who paid before the expense goes into a Batch.",
    persona: "employee",
    lang: "en",
    app: "Expenses",
    models: ["hr.expense"],
    steps: {
        upload: {
            text: "Open **Expenses > My Expenses**. **Upload** adds a receipt and creates its expense in one go; expenses that already have a receipt are listed with it.",
            screenshot: true,
        },
        open_expense: { text: "Open the expense you want to check." },
        attachments: {
            text: "The paper-clip counter in the chatter says how many files the expense carries. Choose it.",
        },
        receipt: {
            text: "The receipt is listed under **Files**, below the expense. Open it and check that it is the right document and that it is readable.",
            screenshot: true,
            scrollTo: ".o-mail-AttachmentBox",
        },
        payer: {
            text: "Check **Paid by**: your own money is reimbursed to you, a company card is settled by the company. Change it here if the receipt says otherwise.",
        },
    },
});

registry.category("usl_docs.journeys").add("usl_expense_batch_focused_review", {
    id: "review-a-batch-before-submitting",
    type: "how-to",
    title: "Review a Batch before submitting it",
    description: "Read the Batch summary, fix the shared analytics once, and deal with a line that differs before you submit.",
    persona: "employee",
    lang: "en",
    app: "Expenses",
    models: ["usl.expense.batch", "hr.expense"],
    steps: {
        batch: {
            text: "Open **Expenses > Batches** and choose your Batch. The summary at the top totals what you paid and what the company paid; the **Expenses** tab lists the lines.",
            screenshot: true,
        },
        analytics: {
            text: "The analytic distribution of the Batch is shared by its lines. Change it here and every line that follows the Batch follows the change.",
        },
        context_action: {
            text: "**Apply context** previews what would change before it changes anything; a line with its own explicit choice is left alone.",
        },
        exception: {
            text: "A line that differs from the Batch is marked with a short helper saying what differs. Open the line to align it, or leave it when the difference is intended.",
            screenshot: true,
        },
        no_trash: { text: "Nothing in the list deletes an expense." },
        remove: {
            text: "**Remove from Batch** is the only line action: the expense goes back to your work list unchanged, ready for another Batch.",
        },
    },
});

registry.category("usl_docs.journeys").add("usl_expense_batch_accountant_handoff", {
    id: "post-an-approved-batch",
    type: "how-to",
    title: "Post an approved Batch",
    description: "Post a Batch whose expenses the manager approved: the company-paid side first, then the employee-paid side through Odoo's own posting wizard.",
    persona: "accountant",
    lang: "en",
    app: "Expenses",
    models: ["usl.expense.batch", "hr.expense", "account.move"],
    steps: {
        approved: {
            text: "Open **Expenses > Batches** and choose an approved Batch. **Post batch** is the primary action; the summary says how much is company-paid and how much is owed to the employee.",
            screenshot: true,
        },
        post: {
            text: "Choose **Post batch**. The company-paid expenses are posted at once; for the employee-paid ones Odoo opens its posting wizard.",
        },
        post_entries: {
            text: "Confirm **Post Expenses** in the wizard. The journal and date are the ones Odoo proposes for expense reports.",
        },
        entries: {
            text: "Both journal entries are listed: one for the company-paid side, one for the reimbursement owed to the employee. Each links back to the Batch.",
            screenshot: true,
        },
    },
});

registry.category("usl_docs.journeys").add("usl_expense_batch_readonly_audit", {
    id: "audit-a-batch-read-only",
    type: "how-to",
    title: "Audit a Batch as a read-only accountant",
    description: "Read everything about a Batch, its lines, its receipts and its accounting, without being able to change any of it.",
    persona: "accountant",
    lang: "en",
    app: "Expenses",
    models: ["usl.expense.batch"],
    steps: {
        evidence: {
            text: "Open **Expenses > Batches** and choose the Batch. With read-only accounting access you see the summary, the lines and their receipts exactly as the employee and the manager do.",
            screenshot: true,
        },
        no_actions: {
            text: "No lifecycle button and no line action is shown: nothing you do here can submit, approve, post or move an expense.",
        },
        history: { text: "Open the **Accounting and history** tab." },
        reconciliation: {
            text: "The reconciliation state of the Batch and its history are visible and locked. Use them to answer an audit question or to prepare a remark for the accountant who can act.",
            screenshot: true,
        },
    },
});
