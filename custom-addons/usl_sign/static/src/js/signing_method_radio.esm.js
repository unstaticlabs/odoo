/** @odoo-module **/

import {_t} from "@web/core/l10n/translation";
import {registry} from "@web/core/registry";
import {RadioField, radioField} from "@web/views/fields/radio/radio_field";

const METHOD_DETAILS = {
    standard: {
        title: _t("Standard"),
        icon: "fa-star-o",
        fit: _t("For everyday agreements"),
        authentication: _t("Private link and explicit consent"),
        pdf: _t("One final platform integrity seal"),
        evidence: _t("Actions, consent, validation, and evidence dossier"),
    },
    strong_personal: {
        title: _t("Strong"),
        recommendation: _t("Recommended"),
        icon: "fa-shield",
        fit: _t("For higher-assurance agreements"),
        authentication: _t("Reviewed identity and Pocket ID passkey"),
        pdf: _t("Personal PAdES per signer, then a platform seal"),
        evidence: _t("Identity authorization, certificates, validation, and dossier"),
    },
    qualified_external: {
        title: _t("Qualified"),
        icon: "fa-certificate",
        fit: _t("When qualified signing is required"),
        authentication: _t("The provider verifies each signer"),
        pdf: _t("Qualified signature returned for validation"),
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
