import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("usl_immediate_settlement", {
    steps: () => [
        {
            content: "Settle and Add coexist on an eligible payment",
            trigger:
                ".o_form_view .o_immediate_settlement_actions:has(.immediate_settlement_assign):has(.outstanding_credit_assign)",
        },
        {
            content: "Settle the payment at its executed rate",
            trigger:
                ".o_form_view .immediate_settlement_assign:not([disabled])",
            run: "click",
        },
        {
            content: "The invoice shows one concise settlement trace",
            trigger:
                ".o_form_view .o_payment_label:contains('Settled at payment rate')",
        },
        {
            content: "Open the settlement accounting detail",
            trigger:
                ".o_form_view tr:has(.o_payment_label:contains('Settled at payment rate')) .js_payment_info",
            run: "click",
        },
        {
            content: "The executed pair and provenance remain inspectable",
            trigger:
                ".account_payment_popover:contains('5.00 EUR = 4.40 USD'):contains('journal_item')",
        },
        {
            content: "Reverse the whole linked settlement",
            trigger:
                ".account_payment_popover button:contains('Unreconcile')",
            run: "click",
        },
        {
            content: "Reversal restores the open document and both actions",
            trigger:
                ".o_form_view .o_immediate_settlement_actions:has(.immediate_settlement_assign):has(.outstanding_credit_assign)",
        },
    ],
});
