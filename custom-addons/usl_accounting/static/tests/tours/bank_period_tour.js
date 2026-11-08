import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("usl_bank_period_correction", {
    steps: () => [
        ...(window.innerWidth < 768
            ? [{ trigger: ".o_statusbar_buttons button[title='More']", run: "click" }]
            : []),
        {
            id: "correct",
            content: "The ingestion that needs attention offers a period correction",
            trigger: "button[name='action_correct_period']",
            run: "click",
        },
        { id: "dialog", content: "The correction dialog opens", trigger: ".o_dialog [data-field='period_start']", run: "click" },
        { trigger: ".o_dialog .o_field_widget[name='period_start'] input", run: "edit 2026-08-01" },
        { trigger: ".o_dialog [data-field='period_end']", run: "click" },
        { trigger: ".o_dialog .o_field_widget[name='period_end'] input", run: "edit 2026-08-31" },
        {
            id: "reason",
            content: "A reason is required and kept in the history",
            trigger: ".o_dialog .o_field_widget[name='reason'] textarea",
            run: "edit Verified official bank statement",
        },
        { id: "apply", content: "Apply the corrected period", trigger: ".o_dialog button[name='action_apply']", run: "click" },
        {
            id: "corrected",
            content: "The ingestion carries the corrected period",
            trigger: "body:not(:has(.o_dialog)) .o_form_view .o_field_widget[name='period_start']",
        },
    ],
});

registry.category("usl_docs.journeys").add("usl_bank_period_correction", {
    id: "correct-a-bank-statement-period",
    type: "how-to",
    title: "Correct the period of an imported bank statement",
    description: "When an imported bank export covers other dates than Odoo read, set the right period and say why, so the import can continue and the history shows the correction.",
    persona: "accountant",
    lang: "en",
    app: "Accounting",
    models: ["account.bank.ingestion"],
    steps: {
        correct: {
            text: "Open **Accounting > Bank > Imports** and choose the import that needs attention. Choose **Correct period**.",
            screenshot: true,
        },
        dialog: { text: "Set the **start** and **end** of the period the statement really covers." },
        reason: {
            text: "Write the **reason**, for example what the official statement says. It is posted in the import's history with your name.",
            screenshot: true,
        },
        apply: { text: "Choose **Apply**." },
        corrected: {
            text: "The import shows the corrected period and continues from there.",
            screenshot: true,
        },
    },
});
