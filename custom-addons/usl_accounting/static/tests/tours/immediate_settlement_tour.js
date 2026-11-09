import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("usl_immediate_settlement", {
    steps: () => [
        {
            id: "actions",
            content: "All three actions coexist on an immediate payment",
            trigger:
                ".o_form_view .o_immediate_settlement_actions:has(.outstanding_credit_assign):has(.immediate_settlement_assign):has(.payment_rate_assign.btn-primary)",
        },
        {
            id: "best_match",
            content: "The matching cue stays compact",
            trigger:
                ".o_form_view .o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_best:contains('Best match')",
        },
        {
            id: "use_rate",
            content: "Use the immediate payment rate",
            trigger:
                ".o_form_view .payment_rate_assign:not([disabled])",
            run: "click",
        },
        {
            id: "trace",
            content: "The invoice shows one concise settlement trace",
            trigger:
                ".o_form_view .o_payment_label:contains('Payment rate ·'):contains('no FX')",
        },
        {
            id: "open_detail",
            content: "Open the settlement accounting detail",
            trigger:
                ".o_form_view tr:has(.o_payment_label:contains('Payment rate ·')) .js_payment_info",
            run: "click",
        },
        {
            id: "detail",
            content: "Source facts and the document repricing remain inspectable",
            trigger:
                ".account_payment_popover:contains('Use payment rate'):contains('Discarded Odoo estimate'):contains('Original document value'):contains('Payment-rate document value')",
        },
        {
            id: "unreconcile",
            content: "Reverse the whole linked settlement",
            trigger:
                ".account_payment_popover button:contains('Unreconcile')",
            run: "click",
        },
        {
            id: "restored",
            content: "Reversal restores the open document and all actions",
            trigger:
                ".o_form_view .o_immediate_settlement_actions:has(.outstanding_credit_assign):has(.immediate_settlement_assign):has(.payment_rate_assign)",
        },
    ],
});

registry.category("usl_docs.journeys").add("usl_immediate_settlement", {
    id: "settle-a-foreign-bill-at-the-payment-rate",
    type: "how-to",
    title: "Settle a foreign-currency bill at the payment rate",
    description: "Match a bill in a foreign currency to the company-currency debit the bank reports, value the bill at the rate the payment implies, and undo it if needed.",
    persona: "accountant",
    lang: "en",
    app: "Accounting",
    models: ["account.move", "account.bank.statement.line"],
    steps: {
        actions: {
            text: "Open the posted bill from **Accounting > Vendors > Bills**. When a bank line of the same partner matches it within the allowed days and rate deviation, the outstanding box offers three actions: the native **Add**, **Settle immediately** and **Use payment rate**, the recommended one in colour.",
            screenshot: true,
        },
        best_match: {
            text: "**Best match** names the bank line Odoo compared the bill with; open it when you want to see the other candidates.",
        },
        use_rate: {
            text: "Choose **Use payment rate**. The bill is revalued at the rate the bank payment implies, so no exchange difference is written.",
        },
        trace: {
            text: "The bill is paid and a single line records the settlement: the mechanism, and **no FX** when nothing was booked for the exchange.",
            screenshot: true,
        },
        open_detail: { text: "Choose the info icon on the payment line." },
        detail: {
            text: "The detail keeps the facts: what Odoo had estimated and discarded, the original value of the document, its value at the payment rate, and the entries involved.",
            screenshot: true,
        },
        unreconcile: {
            text: "**Unreconcile** reverses the whole settlement in one step when the match was wrong.",
        },
        restored: {
            text: "The bill is open again at its original value and the three actions are offered once more.",
        },
    },
});
