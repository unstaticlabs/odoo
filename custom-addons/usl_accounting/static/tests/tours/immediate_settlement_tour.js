import { registry } from "@web/core/registry";
import { user } from "@web/core/user";

function label(english, french) {
    return user.lang.startsWith("fr") ? french : english;
}

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
                `.o_form_view .o_rebuild_payment_suggestion_detail .o_rebuild_payment_suggestion_best:contains('${label("Best match", "Meilleur rapprochement")}')`,
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
                `.o_form_view .o_payment_label:contains('${label("Payment rate ·", "Taux de paiement ·")}'):contains('${label("no FX", "sans écart de change")}')`,
        },
        {
            id: "open_detail",
            content: "Open the settlement accounting detail",
            trigger:
                `.o_form_view tr:has(.o_payment_label:contains('${label("Payment rate ·", "Taux de paiement ·")}')) .js_payment_info`,
            run: "click",
        },
        {
            id: "detail",
            content: "Source facts and the document repricing remain inspectable",
            trigger:
                `.account_payment_popover:contains('${label("Use payment rate", "Utiliser le taux de paiement")}'):contains('${label("Discarded Odoo estimate", "Estimation Odoo rejetée")}'):contains('${label("Original document value", "Valeur d’origine du document")}'):contains('${label("Payment-rate document value", "Valeur du document au taux du paiement")}')`,
        },
        {
            id: "unreconcile",
            content: "Reverse the whole linked settlement",
            trigger:
                ".account_payment_popover button.js_unreconcile_payment",
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
            scrollTo: ".o_immediate_settlement_actions",
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
            scrollTo: ".o_form_view .o_payment_label",
        },
        open_detail: { text: "Choose the info icon on the payment line." },
        detail: {
            text: "The detail keeps the facts: what Odoo had estimated and discarded, the original value of the document, its value at the payment rate, and the entries involved.",
            screenshot: true,
            scrollTo: ".account_payment_popover",
        },
        unreconcile: {
            text: "**Unreconcile** reverses the whole settlement in one step when the match was wrong.",
        },
        restored: {
            text: "The bill is open again at its original value and the three actions are offered once more.",
        },
    },
});

registry.category("usl_docs.journey_translations").add("usl_immediate_settlement.fr", {
    id: "regler-une-facture-en-devise-au-taux-du-paiement",
    title: "Régler une facture en devise au taux du paiement",
    description: "Utilisez le débit bancaire pour valoriser une facture fournisseur en devise, vérifiez le détail du règlement et retrouvez la valeur d’origine en cas d’erreur.",
    app: "Comptabilité",
    steps: {
        actions: "Dans **Comptabilité > Fournisseurs > Factures**, ouvrez la facture comptabilisée. Lorsqu’un débit du même fournisseur satisfait les critères de date et de taux, Odoo propose trois actions de rapprochement. L’action recommandée apparaît en couleur.",
        best_match: "Consultez **Meilleur rapprochement** pour identifier la ligne bancaire proposée et examiner les autres possibilités.",
        use_rate: "Si le débit justifie cette valorisation, cliquez sur **Utiliser le taux de paiement**. Odoo recalcule la valeur de la facture en devise de la société au taux observé sur le paiement.",
        trace: "La facture passe à l’état payé. La ligne de règlement indique le taux de paiement et précise qu’aucun écart de change n’a été comptabilisé.",
        open_detail: "Cliquez sur l’icône d’information de la ligne de paiement.",
        detail: "Comparez l’estimation écartée, la valeur d’origine de la facture et sa valeur au taux du paiement. Les écritures associées permettent de retracer le règlement.",
        unreconcile: "Si le rapprochement est erroné, cliquez sur **Annuler le lettrage** dans le détail du paiement.",
        restored: "La facture est de nouveau ouverte à sa valeur d’origine. Vous pouvez vérifier les trois propositions et choisir le rapprochement qui convient.",
    },
});
