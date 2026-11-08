import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("usl_platform_billing_operator_creation_journey", {
    steps: () => [
        {
            id: "sessions",
            content: "No session filter is selected by default",
            trigger: ".o_control_panel:not(:has(.o_searchview_facet))",
        },
        {
            content: "Closed sessions remain visible in the unfiltered list",
            trigger:
                ".o_list_view .o_data_row td[name='name']:contains('Browser closed session')",
        },
        {
            id: "create",
            content: "Create a monthly platform billing session",
            trigger: ".o_list_button_add",
            run: "click",
        },
        {
            content: "Open the billing period picker",
            trigger: ".o_field_widget[name='period_month'] button",
            run: "click",
        },
        {
            content: "Move the billing period picker to August",
            trigger: ".o_datetime_picker_header .o_next",
            run: "click",
        },
        {
            content: "The billing period picker shows August",
            trigger: ".o_datetime_picker_header button:contains('Aug 2026')",
        },
        {
            content: "Select the first day of August",
            trigger:
                '.o_datetime_picker .o_date_item_cell:not(.o_out_of_range):contains("/^1$/")',
            run: "click",
        },
        {
            content: "The August period is committed",
            trigger: ".o_field_widget[name='period_month'] button",
            run() {
                const value = this.anchor.textContent.trim();
                if (!value.includes("Aug") || !value.includes("1")) {
                    throw new Error(`Expected the August period, got "${value}"`);
                }
            },
        },
        {
            id: "period",
            content: "The historical French session name is suggested",
            trigger: ".o_field_widget[name='name'] input:value('Août 2026')",
        },
        {
            content: "Open the invoice date picker",
            trigger: ".o_field_widget[name='invoice_date'] button",
            run: "click",
        },
        {
            content: "Move the invoice date picker to August",
            trigger: ".o_datetime_picker_header .o_next",
            run: "click",
        },
        {
            content: "The invoice date picker shows August",
            trigger: ".o_datetime_picker_header button:contains('Aug 2026')",
        },
        {
            content: "Use the August month-end invoice date",
            trigger:
                '.o_datetime_picker .o_date_item_cell:not(.o_out_of_range):contains("/^31$/")',
            run: "click",
        },
        {
            id: "add_payout",
            content: "Add the platform payout",
            trigger: "div[name='payout_ids'] .o_field_x2many_list_row_add button",
            run: "click",
        },
        {
            content: "Select the configured platform",
            trigger:
                ".o_selected_row .o_field_widget[name='platform_id'] input",
            run: "edit Browser CreatorHub",
        },
        {
            content: "Confirm the configured platform",
            trigger:
                ".o_selected_row .o_field_widget[name='platform_id'] " +
                ".o-autocomplete--dropdown-item:contains('Browser CreatorHub')",
            run: "click",
        },
        {
            content: "The configured platform is selected",
            trigger:
                ".o_selected_row .o_field_widget[name='platform_id']" +
                ":not(:has(.o-autocomplete--dropdown-menu)) " +
                "input:value('Browser CreatorHub')",
        },
        {
            content: "Open the payout date picker",
            trigger:
                ".o_selected_row .o_field_widget[name='payout_date'] input",
            run: "click",
        },
        {
            content: "Open the payout month selector",
            trigger: ".o_datetime_picker .o_zoom_out",
            run: "click",
        },
        {
            content: "Open the payout year selector",
            trigger: ".o_datetime_picker .o_zoom_out",
            run: "click",
        },
        {
            content: "Select the payout year",
            trigger:
                '.o_datetime_picker .o_date_item_cell:not(.o_out_of_range):contains("/^2026$/")',
            run: "click",
        },
        {
            content: "Select the payout month",
            trigger:
                '.o_datetime_picker .o_date_item_cell:not(.o_out_of_range):contains("/^Aug$/")',
            run: "click",
        },
        {
            content: "Select the payout day",
            trigger:
                '.o_datetime_picker .o_date_item_cell:not(.o_out_of_range):contains("/^15$/")',
            run: "click",
        },
        {
            content: "Enter the platform payout reference",
            trigger:
                ".o_selected_row .o_field_widget[name='platform_reference'] input",
            run: "edit BROWSER-NEW-2026-08-001",
        },
        {
            id: "net_amount",
            content: "Enter the platform net amount",
            trigger:
                ".o_selected_row .o_field_widget[name='net_platform_amount'] input",
            run: "edit 80",
        },
        {
            id: "check",
            content: "Save through the real form and run the accounting checks",
            trigger: "button[name='action_check']",
            run: "click",
        },
        {
            id: "generate",
            content: "Generate customer invoice and commission bill drafts",
            trigger: "button[name='action_generate_documents']",
            run: "click",
        },
        {
            id: "post",
            content: "Post the monthly accounting documents",
            trigger: "button[name='action_post_documents']",
            run: "click",
        },
        {
            id: "coverage",
            content: "Incomplete active-platform coverage is clearly identified",
            trigger:
                ".modal .alert:contains('Browser platform without August payout')",
        },
        {
            id: "confirm_coverage",
            content: "Confirm the deliberate monthly coverage exception",
            trigger: ".modal button[name='action_confirm']",
            run: "click",
        },
        {
            id: "posted",
            content: "The delayed payout stays posted and open",
            trigger:
                ".o_form_view .o_statusbar_status button[data-value='posted'].o_arrow_button_current",
        },
        {
            id: "reconcile_later",
            content: "Bank reconciliation remains available for the later receipt",
            trigger: "button[name='action_reconcile_bank']",
        },
    ],
});

registry.category("web_tour.tours").add("usl_platform_billing_manager_journey", {
    steps: () => [
        {
            id: "draft",
            content: "Open a prepared platform billing session",
            trigger:
                ".o_form_view:has(.o_statusbar_status button[data-value='draft'].o_arrow_button_current)",
        },
        {
            id: "preflight",
            content: "Run the accounting preflight",
            trigger: "button[name='action_check']",
            run: "click",
        },
        {
            id: "documents",
            content: "Generate customer invoices and commission bills",
            trigger: "button[name='action_generate_documents']",
            run: "click",
        },
        {
            id: "post_documents",
            content: "Post the documents and compensate commission",
            trigger: "button[name='action_post_documents']",
            run: "click",
        },
        {
            id: "reconcile",
            content: "Reconcile the selected bank receipt through OCA",
            trigger: "button[name='action_reconcile_bank']",
            run: "click",
        },
        {
            id: "paid",
            content: "The complete session is paid",
            trigger:
                ".o_form_view .o_statusbar_status button[data-value='paid'].o_arrow_button_current",
        },
        {
            id: "open_moves",
            content: "Open the generated native accounting documents",
            trigger: "button[name='action_open_generated_moves']",
            run: "click",
        },
        {
            id: "moves",
            content: "Native posted accounting documents remain available",
            trigger: ".o_list_view .o_data_row td[name='name']",
        },
        {
            id: "no_payment",
            content: "Miscellaneous entries clearly require no payment",
            trigger:
                ".o_list_view td[name='platform_billing_payment_state']:contains('No Payment Required')",
        },
    ],
});

registry.category("web_tour.tours").add("usl_platform_billing_manager_config_journey", {
    steps: () => [
        {
            content: "The Platform Billing Administrator can create a platform",
            trigger: ".o_list_button_add",
            run: "click",
        },
        {
            content: "A new platform form opens",
            trigger: ".o_form_view .o_field_widget[name='name'] input",
        },
        {
            content: "Open the optional native analytic configuration",
            trigger: ".o_form_view .nav-link:contains('Analytic')",
            run: "click",
        },
        {
            content: "The native analytic distribution widget loads",
            trigger:
                ".o_form_view .o_field_widget[name='analytic_distribution']",
        },
        {
            content: "An unset distribution has a clear action",
            trigger:
                ".o_form_view .analytic_distribution_placeholder:contains('Add analytic distribution')",
        },
        {
            content: "Open bank matching configuration",
            trigger: ".o_form_view .nav-link:contains('Bank Matching')",
            run: "click",
        },
        {
            content: "The manager can edit platform matching settings",
            trigger:
                ".o_form_view .o_field_widget[name='bank_label_pattern']",
        },
    ],
});

registry.category("web_tour.tours").add("usl_platform_billing_bank_create_journey", {
    steps: () => [
        {
            content: "Open bank import from an empty session",
            trigger: "button[name='action_open_bank_import']",
            run: "click",
        },
        {
            content: "All open transactions are visible by default",
            trigger: ".modal .o_field_widget[name='candidate_scope']",
        },
        {
            content: "Regenerate candidates through Suggested only",
            trigger: ".modal .o_field_widget[name='candidate_scope'] .o_select_menu_toggler",
            run: "click",
        },
        {
            trigger: ".o_field_selection_menu .dropdown-item:contains('Suggested only')",
            run: "click",
        },
        {
            content: "Restore unmatched candidates before importing",
            trigger: ".modal:not(:has(.o_data_row)) .o_field_widget[name='candidate_scope'] .o_select_menu_toggler",
            run: "click",
        },
        {
            trigger: ".o_field_selection_menu .dropdown-item:contains('All open')",
            run: "click",
        },
        {
            content: "The matching transaction is available",
            trigger:
                ".modal .o_data_row:has(td[name='bank_label']:contains('Unrecognised browser platform receipt')) [name='selected'] button",
            run: "click",
        },
        {
            content: "Import the bank transaction as a draft payout",
            trigger: ".modal button[name='action_create_payouts']",
            run: "click",
        },
        {
            content: "Open the payout platform field",
            trigger:
                ".o_form_view div[name='payout_ids'] .o_data_row td[name='platform_id']",
            run: "click",
        },
        {
            content: "Configure the platform on the payout row",
            trigger:
                ".o_form_view div[name='payout_ids'] .o_data_row .o_field_widget[name='platform_id'] input",
            run: "edit Browser CreatorHub",
        },
        {
            content: "Confirm the platform",
            trigger: ".dropdown-item:contains('Browser CreatorHub')",
            run: "click",
        },
        {
            content: "Enter the original platform reference",
            trigger:
                ".o_form_view div[name='payout_ids'] .o_data_row .o_field_widget[name='platform_reference'] input",
            run: "edit BROWSER-CREATE-001",
        },
        {
            content: "Enter the original platform payout amount",
            trigger:
                ".o_form_view div[name='payout_ids'] .o_data_row .o_field_widget[name='net_platform_amount'] input",
            run: "edit 80",
        },
        {
            content: "Validate the completed payout",
            trigger: "button[name='action_check']",
            run: "click",
        },
        {
            content: "The imported and completed session is ready",
            trigger:
                ".o_form_view .o_statusbar_status button[data-value='ready'].o_arrow_button_current",
        },
    ],
});

registry.category("web_tour.tours").add("usl_platform_billing_bank_rate_journey", {
    steps: () => [
        {
            content: "Open the bank-created foreign payout flow",
            trigger: "button[name='action_open_bank_import']",
            run: "click",
        },
        {
            content: "Select the EUR 700 bank transaction",
            trigger:
                ".modal .o_data_row:has(td[name='bank_label']:contains('Browser FX payout BROWSER-FX-1000')) [name='selected'] button",
            run: "click",
        },
        {
            content: "Create the payout from the selected bank transaction",
            trigger: ".modal button[name='action_create_payouts']",
            run: "click",
        },
        {
            content: "Open the platform net amount",
            trigger:
                ".o_form_view div[name='payout_ids'] .o_data_row td[name='net_platform_amount']",
            run: "click",
        },
        {
            content: "Enter the USD 1000 platform net",
            trigger:
                ".o_form_view div[name='payout_ids'] .o_data_row .o_field_widget[name='net_platform_amount'] input",
            run: "edit 1000",
        },
        {
            content: "Validate the bank-created payout",
            trigger: "button[name='action_check']",
            run: "click",
        },
        {
            content: "The saved transaction-derived rate is visible",
            trigger:
                ".o_form_view div[name='payout_ids'] .o_data_row td[name='effective_bank_rate_label']:contains('0.700000')",
        },
        {
            content: "Generate documents at the effective bank rate",
            trigger: "button[name='action_generate_documents']",
            run: "click",
        },
        {
            content: "Post the rate-valued documents",
            trigger: "button[name='action_post_documents']",
            run: "click",
        },
        {
            content: "Confirm the deliberate monthly coverage exception",
            trigger: ".modal button[name='action_confirm']",
            run: "click",
        },
        {
            content: "Reconcile the exact USD debt with the EUR receipt",
            trigger: "button[name='action_reconcile_bank']",
            run: "click",
        },
        {
            content: "The zero-FX bank-created session is paid",
            trigger:
                ".o_form_view .o_statusbar_status button[data-value='paid'].o_arrow_button_current",
        },
    ],
});

registry.category("web_tour.tours").add("usl_platform_billing_pooled_link_journey", {
    steps: () => [
        {
            content: "The first delayed session is posted",
            trigger:
                ".o_form_view .o_statusbar_status button[data-value='posted'].o_arrow_button_current",
        },
        {
            content: "Open bank transaction linking",
            trigger: "button[name='action_open_bank_import']",
            run: "click",
        },
        {
            content: "Select the second delayed payout",
            trigger:
                ".modal .o_data_row:has(td[name='platform_reference']:contains('BROWSER-POOL-TWO')) button[name='action_select']",
            run: "click",
        },
        {
            content: "Select the pooled receipt",
            trigger:
                ".modal .o_data_row:has(td[name='bank_label']:contains('Browser pooled receipt 160')) [name='selected'] button",
            run: "click",
        },
        {
            content: "Link the pooled receipt to both payouts",
            trigger: ".modal button[name='action_link_payouts']",
            run: "click",
        },
        {
            content: "Reconcile both delayed debts through the saved allocations",
            trigger: "button[name='action_reconcile_bank']",
            run: "click",
        },
        {
            content: "The first session is paid",
            trigger:
                ".o_form_view .o_statusbar_status button[data-value='paid'].o_arrow_button_current",
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

registry.category("usl_docs.journeys").add("usl_platform_billing_operator_creation_journey", {
    id: "create-a-monthly-platform-billing-session",
    type: "how-to",
    title: "Create and post a monthly platform billing session",
    description: "Open the month, enter the payout each platform reported, run the checks, generate the invoices and bills and post them, even when a platform has not paid yet.",
    persona: "finance_operator",
    lang: "en",
    app: "Platform billing",
    models: ["usl.platform.billing.session", "usl.platform.billing.payout"],
    steps: {
        sessions: {
            text: "Open **Platform Billing > Billing Sessions**. The list shows every month, closed ones included, so you can see what was done before.",
            screenshot: true,
        },
        create: { text: "Choose **New**." },
        period: {
            text: "Pick the **billing period**: the first day of the month you are closing. Odoo names the session after the month, in French as the history always has, and proposes the month-end as invoice date.",
            screenshot: true,
        },
        add_payout: { text: "Under **Payouts**, choose **Add a line** for each platform that reported a payout this month." },
        net_amount: {
            text: "Pick the platform, the payout date and the platform's own reference, then enter the **net amount** the platform reported. The gross revenue and the commission are derived from the platform's rate.",
            screenshot: true,
        },
        check: {
            text: "Choose **Check**. The session is saved and the accounting checks run: platforms, journals, products and amounts must all be in place before anything is generated.",
        },
        generate: { text: "Choose **Generate drafts**: one customer invoice for the revenue and one bill for the commission, per payout, in draft." },
        post: { text: "Choose **Post documents**." },
        coverage: {
            text: "When an active platform has no payout this month, Odoo says which one before posting. This is the moment to add a forgotten line.",
            screenshot: true,
        },
        confirm_coverage: { text: "Confirm when the gap is deliberate, for example a platform that pays quarterly." },
        posted: {
            text: "The session is **Posted**. A payout whose money has not arrived stays open; the invoice is posted and waits for the bank.",
            screenshot: true,
        },
        reconcile_later: { text: "**Reconcile bank** stays available: come back when the receipt shows on the bank statement." },
    },
});

registry.category("usl_docs.journeys").add("usl_platform_billing_manager_journey", {
    id: "settle-a-platform-billing-session",
    type: "how-to",
    title: "Generate, post and reconcile a platform billing session",
    description: "Take a prepared session from draft to paid: run the preflight, generate and post the documents, and reconcile the bank receipt already matched to the payout.",
    persona: "accountant",
    lang: "en",
    app: "Platform billing",
    models: ["usl.platform.billing.session", "account.move"],
    steps: {
        draft: {
            text: "Open **Platform Billing > Billing Sessions** and choose a session in **Draft** whose payouts are entered and whose bank receipt was matched.",
            screenshot: true,
        },
        preflight: { text: "Choose **Check** to run the accounting preflight." },
        documents: { text: "Choose **Generate drafts**: the customer invoices and the commission bills are created in draft." },
        post_documents: {
            text: "Choose **Post documents**. The documents are posted and the commission is compensated against the revenue, so only the net amount is expected from the bank.",
        },
        reconcile: { text: "Choose **Reconcile bank**. The receipt matched to the payout settles the net amount through the bank reconciliation." },
        paid: {
            text: "The session is **Paid**: the invoice, the bill and the bank line agree.",
            screenshot: true,
        },
        open_moves: { text: "Choose **Journal Entries** to see what was posted." },
        moves: {
            text: "Every document is a native Odoo journal entry, open to the usual accounting views and reports.",
            screenshot: true,
        },
        no_payment: { text: "The compensation entries read **No Payment Required**: they move the commission, they do not expect money." },
    },
});
