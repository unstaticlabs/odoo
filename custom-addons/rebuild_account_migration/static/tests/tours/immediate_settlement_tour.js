import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("usl_immediate_settlement", {
    steps: () => [
        {
            content: "All three actions coexist on an immediate payment",
            trigger:
                ".o_form_view .o_immediate_settlement_actions:has(.outstanding_credit_assign):has(.immediate_settlement_assign):has(.payment_rate_assign.btn-primary)",
        },
        {
            content: "The matching cue stays compact",
            trigger:
                ".o_form_view .o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_evidence:contains('Best match')",
        },
        {
            content: "Use the immediate payment rate",
            trigger:
                ".o_form_view .payment_rate_assign:not([disabled])",
            run: "click",
        },
        {
            content: "The invoice shows one concise settlement trace",
            trigger:
                ".o_form_view .o_payment_label:contains('Payment rate ·'):contains('no FX')",
        },
        {
            content: "Open the settlement accounting detail",
            trigger:
                ".o_form_view tr:has(.o_payment_label:contains('Payment rate ·')) .js_payment_info",
            run: "click",
        },
        {
            content: "Source facts and the document repricing remain inspectable",
            trigger:
                ".account_payment_popover:contains('Use payment rate'):contains('Discarded Odoo estimate'):contains('Original document value'):contains('Payment-rate document value')",
        },
        {
            content: "Reverse the whole linked settlement",
            trigger:
                ".account_payment_popover button:contains('Unreconcile')",
            run: "click",
        },
        {
            content: "Reversal restores the open document and all actions",
            trigger:
                ".o_form_view .o_immediate_settlement_actions:has(.outstanding_credit_assign):has(.immediate_settlement_assign):has(.payment_rate_assign)",
        },
    ],
});
