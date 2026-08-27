/** @odoo-module **/

import {_t} from "@web/core/l10n/translation";
import {registry} from "@web/core/registry";
import {RadioField, radioField} from "@web/views/fields/radio/radio_field";

const METHOD_DETAILS = {
    standard: {
        title: _t("Standard"),
        recommendation: _t("Recommended"),
        fit: _t("For everyday agreements"),
        summary: _t("Straightforward signing with a complete evidence trail."),
        authentication: _t("Private link and explicit consent"),
        pdf: _t("One final platform integrity seal"),
        evidence: _t("Actions, consent, validation, and evidence dossier"),
    },
    strong_personal: {
        title: _t("Strong"),
        fit: _t("For higher-assurance agreements"),
        summary: _t("Each signer confirms with a reviewed identity and passkey."),
        authentication: _t("Reviewed identity and Pocket ID passkey"),
        pdf: _t("Personal PAdES per signer, then a platform seal"),
        evidence: _t("Identity authorization, certificates, validation, and dossier"),
    },
    qualified_external: {
        title: _t("Qualified"),
        fit: _t("When qualified signing is required"),
        summary: _t("Signing is completed by an approved external provider."),
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
