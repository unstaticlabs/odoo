import { registry } from "@web/core/registry";
import { user } from "@web/core/user";

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
            content: "The focused setup identifies the company by its SIREN or SIRET",
            trigger: `.o_form_view label[for^='company_registry']:contains('${user.lang.startsWith("fr") ? "SIREN ou SIRET" : "SIREN or SIRET"}')`,
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
            trigger: `.o_form_view .o_field_widget[name='rebuild_einvoice_test_status']:contains('${user.lang.startsWith("fr") ? "Test réussi" : "Test passed"}')`,
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

const companyIdentity = ["vat", "company_registry", "account_peppol_contact_email", "account_peppol_phone_number", "peppol_endpoint"]
    .map((name) => `.o_field_widget[name='${name}']`);

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
            mask: companyIdentity,
        },
        run_check: {
            text: "Choose **Run self-check**. Odoo receives a representative electronic invoice through the whole reception path, then discards every record it created.",
        },
        passed: {
            text: "The self-check reads **Test passed**, with when it ran and the confirmation that no test bill was kept. Run it again after any change to the journals, the accounts or the platform.",
            screenshot: true,
            mask: companyIdentity,
        },
    },
});

registry.category("usl_docs.journey_translations").add("usl_einvoice_manager_reception.fr", {
    id: "tester-la-reception-des-factures-electroniques",
    title: "Tester la réception des factures électroniques",
    description: "Vérifiez la configuration d’une société avec une facture électronique d’essai. Le résultat du contrôle reste consultable, et la facture d’essai est supprimée.",
    app: "Comptabilité",
    steps: {
        companies: "Dans **Comptabilité > Configuration > Facturation électronique**, ouvrez la société dont vous voulez tester la réception.",
        setup: "Vérifiez les journaux, les comptes utilisés et la plateforme agréée. Le résultat du dernier essai figure sur la même fiche.",
        run_check: "Cliquez sur **Exécuter une auto-vérification**. Odoo fait passer une facture d’essai par les étapes de réception, puis supprime les pièces créées pour ce contrôle.",
        passed: "Vérifiez la mention **Test réussi**, la date de l’essai et la confirmation qu’aucune facture d’essai n’a été conservée. Relancez le contrôle après une modification des journaux, des comptes ou de la plateforme.",
    },
});
