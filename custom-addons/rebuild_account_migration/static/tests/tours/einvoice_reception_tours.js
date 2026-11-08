import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("usl_einvoice_manager_reception", {
    steps: () => [
        {
            id: "companies",
            content: "Open the company's electronic-invoice setup",
            trigger: ".o_list_view .o_data_row td[name='name']",
            run: "click",
        },
        {
            id: "setup",
            content: "The focused setup screen is open",
            trigger: "button[name='action_rebuild_run_einvoice_acceptance_test']",
        },
        {
            id: "run_check",
            content: "Run the safe reception self-check",
            trigger: "button[name='action_rebuild_run_einvoice_acceptance_test']",
            run: "click",
        },
        {
            id: "passed",
            content: "The self-check passed without retaining test accounting",
            trigger: ".o_form_view .o_field_widget[name='rebuild_einvoice_test_status']:contains('Test passed')",
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

registry.category("usl_docs.journeys").add("usl_einvoice_manager_reception", {
    id: "run-the-electronic-invoice-reception-self-check",
    type: "how-to",
    title: "Run the electronic-invoice reception self-check",
    description: "Prove that a company can receive electronic invoices, with a representative invoice that leaves no accounting behind.",
    persona: "administrator",
    lang: "en",
    app: "Accounting",
    models: ["res.company"],
    steps: {
        companies: {
            text: "Open **Accounting > Configuration > Electronic Invoicing**. Each company you may act in is listed with its reception status. Choose the company.",
        },
        setup: {
            text: "The setup screen gathers what reception needs: the journals, the accounts, the approved platform and the last self-check.",
            screenshot: true,
        },
        run_check: {
            text: "Choose **Run self-check**. Odoo receives a representative electronic invoice through the whole reception path, then discards every record it created.",
        },
        passed: {
            text: "The self-check reads **Test passed**, with when it ran and the confirmation that no test bill was kept. Run it again after any change to the journals, the accounts or the platform.",
            screenshot: true,
        },
    },
});
