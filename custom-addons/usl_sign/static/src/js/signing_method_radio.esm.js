/** @odoo-module **/

import {_t} from "@web/core/l10n/translation";
import {registry} from "@web/core/registry";
import {RadioField, radioField} from "@web/views/fields/radio/radio_field";

const METHOD_DETAILS = {
    standard: {
        recommendation: _t("Recommended for most agreements"),
        summary: _t("Simple signing with a strong audit trail."),
        authentication: _t("Private link and explicit consent"),
        pdf: _t("One final platform integrity seal"),
        evidence: _t("Signer actions, consent, validation, and dossier"),
    },
    strong_personal: {
        summary: _t("Personal digital signatures for higher assurance."),
        authentication: _t("Approved identity and Pocket ID passkey"),
        pdf: _t("One personal PAdES signature per signer, then a platform seal"),
        evidence: _t("Identity review, authorization, certificates, validation, and dossier"),
    },
    qualified_external: {
        summary: _t("Qualified signing through an approved external provider."),
        authentication: _t("The provider verifies each signer"),
        pdf: _t("Qualified signature returned by the provider"),
        evidence: _t("Provider proof, independent validation, and dossier"),
    },
};

export class SigningMethodRadio extends RadioField {
    static template = "usl_sign.SigningMethodRadio";

    detailsFor(value) {
        return METHOD_DETAILS[value] || {};
    }
}

registry.category("fields").add("usl_sign_method_radio", {
    ...radioField,
    component: SigningMethodRadio,
});
