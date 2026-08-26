/** @odoo-module **/

import {_t} from "@web/core/l10n/translation";
import {registry} from "@web/core/registry";
import {RadioField, radioField} from "@web/views/fields/radio/radio_field";

const METHOD_HELP = {
    standard: _t(
        "Requirements: no passkey or identity review. How it works: each signer uses a private link and confirms their consent. Proof kept: signer actions, validation results, the signed PDF, and its proof package."
    ),
    strong_personal: _t(
        "Requirements: an approved signing identity and a fresh Pocket ID passkey confirmation. How it works: Odoo applies the signer’s personal digital certificate after Pocket ID confirms them. Proof kept: identity review, passkey authorization, validation results, the signed PDF, and its proof package."
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
