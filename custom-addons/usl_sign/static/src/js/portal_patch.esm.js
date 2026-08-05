/** @odoo-module **/

import {patch} from "@web/core/utils/patch";
import {registry} from "@web/core/registry";
import {SignOcaPdfPortal} from "@sign_oca/components/sign_oca_pdf_portal/sign_oca_pdf_portal.esm";

const signatureField = registry.category("sign_oca").get("signature");

patch(signatureField, {
    generate(parent, item, signatureItem) {
        const input = super.generate(...arguments);
        if (item.role_id === parent.info.role_id) {
            input.setAttribute("role", "button");
            input.setAttribute("tabindex", item.tabindex || 0);
            input.setAttribute("aria-label", `Add ${item.name || "signature"}`);
            input.addEventListener("keydown", (event) => {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    input.click();
                }
            });
        }
        return input;
    },
});

patch(SignOcaPdfPortal.prototype, {
    postIframeFields() {
        super.postIframeFields(...arguments);
        const iframeDocument = this.iframe.el.contentDocument;
        if (!iframeDocument.getElementById("usl-sign-portal-viewer-style")) {
            const style = iframeDocument.createElement("style");
            style.id = "usl-sign-portal-viewer-style";
            style.textContent = `
                #editorModeButtons, #printButton, #downloadButton, #secondaryPrint,
                #secondaryDownload, #viewBookmark, #openFile { display: none !important; }
                [role="button"][aria-label^="Add "] { cursor: pointer; }
            `;
            iframeDocument.head.append(style);
        }
        for (const button of iframeDocument.querySelectorAll(
            '[role="button"][aria-label^="Add "]'
        )) {
            const field = button.closest(".o_sign_oca_field");
            if (!field || field.dataset.uslSignatureClickBound) {
                continue;
            }
            field.dataset.uslSignatureClickBound = "true";
            field.style.cursor = "pointer";
            field.addEventListener("click", (event) => {
                if (event.target === field) {
                    button.click();
                }
            });
        }
    },

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
