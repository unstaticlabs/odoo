/** @odoo-module **/

import {patch} from "@web/core/utils/patch";
import {SignOcaPdfPortal} from "@sign_oca/components/sign_oca_pdf_portal/sign_oca_pdf_portal.esm";

patch(SignOcaPdfPortal.prototype, {
    async _onClickSign(ev) {
        const consent = document.getElementById("usl_sign_consent");
        const error = document.getElementById("usl_sign_consent_error");
        if (!consent?.checked) {
            error?.classList.remove("d-none");
            consent?.focus();
            return;
        }
        error?.classList.add("d-none");
        ev.target.disabled = true;
        const position = await this.getLocation();
        const action = await this.rpc(
            `/sign_oca/sign/${this.signer_id}/${this.access_token}`,
            {
                items: this.info.items,
                consent: true,
                latitude: position?.coords?.latitude,
                longitude: position?.coords?.longitude,
            }
        );
        window.location = action.type === "ir.actions.act_url" ? action.url : window.location;
    },

    async _onClickDecline() {
        const reason = window.prompt("Please briefly explain why you decline this document.");
        if (!reason?.trim()) {
            return;
        }
        await this.rpc(`/sign/decline/${this.signer_id}/${this.access_token}`, {
            reason: reason.trim(),
        });
        window.location = "/sign/result/declined";
    },
});
