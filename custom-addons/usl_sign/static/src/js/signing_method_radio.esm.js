/** @odoo-module **/

import {_t} from "@web/core/l10n/translation";
import {registry} from "@web/core/registry";
import {RadioField, radioField} from "@web/views/fields/radio/radio_field";

const METHOD_HELP = {
    standard: _t(
        "Requirements: no passkey or identity review. How it works: each signer uses a private link and confirms consent; one platform seal protects the final PDF. Proof kept: each signer’s attestation and actions, validation results, the sealed PDF, and its proof package."
    ),
    strong_personal: _t(
        "Requirements: every signer needs an approved signing identity and a fresh Pocket ID passkey confirmation. How it works: personal PDF signatures are applied sequentially, then a platform seal protects the final PDF. Proof kept: identity review, passkey authorization, personal certificate chains, validation results, the signed PDF, and its proof package."
    ),
    qualified_external: _t(
        "Requirements: a reviewed qualified-signature provider. How it works: Odoo freezes and exports the PDF, then checks the signed file when it returns. Proof kept: provider evidence, validation results, the signed PDF, and its proof package."
    ),
};

export class SigningMethodRadio extends RadioField {
    static template = "usl_sign.SigningMethodRadio";

    helpFor(value) {
        return METHOD_HELP[value] || "";
    }
}

registry.category("fields").add("usl_sign_method_radio", {
    ...radioField,
    component: SigningMethodRadio,
});
