import { registry } from "@web/core/registry";
import { formatDate } from "@web/core/l10n/dates";
import { user } from "@web/core/user";

registry.category("web_tour.tours").add("usl_bank_period_correction", {
    steps: () => [
        ...(window.innerWidth < 768
            ? [{ trigger: ".o_statusbar_buttons button:has(.oi-ellipsis-v)", run: "click" }]
            : []),
        {
            id: "correct",
            content: "The ingestion that needs attention offers a period correction",
            trigger: "button[name='action_correct_period']",
            run: "click",
        },
        { id: "dialog", content: "The correction dialog opens", trigger: ".o_dialog [data-field='period_start']", run: "click" },
        { trigger: ".o_dialog .o_field_widget[name='period_start'] input", run: `edit ${formatDate(luxon.DateTime.fromISO("2026-08-01"))}` },
        { trigger: ".o_dialog [data-field='period_end']", run: "click" },
        { trigger: ".o_dialog .o_field_widget[name='period_end'] input", run: `edit ${formatDate(luxon.DateTime.fromISO("2026-08-31"))}` },
        {
            id: "reason",
            content: "A reason is required and kept in the history",
            trigger: ".o_dialog .o_field_widget[name='reason'] textarea",
            run: user.lang.startsWith("fr") ? "edit Dates vérifiées sur le relevé bancaire" : "edit Verified official bank statement",
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
            text: "Open **Accounting > Configuration > Bank Statement Email Setup**, choose the bank, and open the received export that reads **Needs attention** in its list; the activity on the bank journal points to the same record. Choose **Correct period**.",
            screenshot: true,
        },
        dialog: { text: "Set the **start** and **end** of the period the statement really covers." },
        reason: {
            text: "Write the **reason**, for example what the official statement says. It is posted in the import's history with your name.",
            screenshot: true,
        },
        apply: { text: "Choose **Apply correction**." },
        corrected: {
            text: "The import shows the corrected period and continues from there.",
            screenshot: true,
        },
    },
});

registry.category("usl_docs.journey_translations").add("usl_bank_period_correction.fr", {
    id: "corriger-la-periode-dun-releve-bancaire",
    title: "Corriger la période d’un relevé bancaire importé",
    description: "Renseignez les dates figurant sur le relevé et consignez la raison de la correction pour que l’import puisse reprendre.",
    app: "Comptabilité",
    steps: {
        correct: "Ouvrez l’import depuis l’activité du journal bancaire. Si vous gérez la configuration, vous le retrouvez aussi dans **Comptabilité > Configuration > Configuration des e-mails de relevés bancaires**. Ouvrez l’import à vérifier, puis cliquez sur **Corriger la période**.",
        dialog: "Recopiez les dates de début et de fin qui figurent sur le relevé.",
        reason: "Indiquez sur quelle pièce vous avez vérifié les dates. Le motif de correction figurera dans l’historique de l’import avec votre nom.",
        apply: "Vérifiez les deux dates, puis cliquez sur **Appliquer la correction**.",
        corrected: "Relisez la période sur la fiche d’import. Odoo peut poursuivre le traitement avec les dates corrigées.",
    },
});
