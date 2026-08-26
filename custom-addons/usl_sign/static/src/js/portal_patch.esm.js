/** @odoo-module **/
/* global Event */

import {patch} from "@web/core/utils/patch";
import {registry} from "@web/core/registry";
import {Component, useState} from "@odoo/owl";
import {Dialog} from "@web/core/dialog/dialog";
import {SignatureDialog} from "@web/core/signature/signature_dialog";
import {_t} from "@web/core/l10n/translation";
import {renderToString} from "@web/core/utils/render";
import {useService} from "@web/core/utils/hooks";
import {SignOcaPdfPortal} from "@sign_oca/components/sign_oca_pdf_portal/sign_oca_pdf_portal.esm";

class DeclineDocumentDialog extends Component {
    static template = "usl_sign.DeclineDocumentDialog";
    static components = {Dialog};
    static props = {close: Function, confirm: Function};

    setup() {
        this.state = useState({reason: "", busy: false});
    }

    updateReason(event) {
        this.state.reason = event.target.value;
    }

    async confirm() {
        const reason = this.state.reason.trim();
        if (!reason || this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            await this.props.confirm(reason);
            this.props.close();
        } finally {
            this.state.busy = false;
        }
    }
}

class InitialsDialog extends SignatureDialog {
    static template = "usl_sign.InitialsDialog";
}

const signatureField = registry.category("sign_oca").get("signature");
const textField = registry.category("sign_oca").get("text");
const checkField = registry.category("sign_oca").get("check");

const HTML_INPUT_TYPE_BY_KIND = {
    date: "date",
    email: "email",
    phone: "tel",
};

const AUTOCOMPLETE_BY_KIND = {
    signer_name: "name",
    email: "email",
    phone: "tel",
    company: "organization",
    role: "organization-title",
};

function generateWithOcaRoleCompatibility(item, generate) {
    const hadRole = Object.prototype.hasOwnProperty.call(item, "role");
    const previousRole = item.role;
    // OCA's text and checkbox QWeb templates compare `item.role`, while the
    // public signer payload and the field generators use `item.role_id`.
    // Supply the compatibility value only while OCA renders the field.
    item.role = item.role_id;
    try {
        return generate();
    } finally {
        if (hadRole) {
            item.role = previousRole;
        } else {
            delete item.role;
        }
    }
}

patch(textField, {
    generate(parent, item, signatureItem) {
        if (item.role_id === parent.info.role_id && !item.value) {
            if (item.kind === "date") {
                const now = new Date();
                item.value = new Date(now.getTime() - now.getTimezoneOffset() * 60000)
                    .toISOString()
                    .slice(0, 10);
            } else if (item.default_value && parent.info.partner[item.default_value]) {
                item.value = parent.info.partner[item.default_value];
            }
        }
        const input = generateWithOcaRoleCompatibility(item, () =>
            $(
                renderToString("sign_oca.sign_iframe_field_text", {
                    item,
                    role_id: parent.info.role_id,
                })
            )[0]
        );
        if (item.role_id !== parent.info.role_id || input.tagName !== "INPUT") {
            return input;
        }
        const htmlInputType = HTML_INPUT_TYPE_BY_KIND[item.kind] || "text";
        input.type = htmlInputType;
        input.required = Boolean(item.required);
        if (AUTOCOMPLETE_BY_KIND[item.kind]) {
            input.autocomplete = AUTOCOMPLETE_BY_KIND[item.kind];
        }
        if (item.kind === "phone") {
            input.inputMode = "tel";
        }
        input.setAttribute(
            "aria-label",
            item.name || (item.kind === "date" ? "Signing date" : "Signing field")
        );
        // Chromium may clear the live value when an already-rendered text
        // input is converted to a date control. Reapply the canonical value.
        input.value = item.value || "";
        input.tabIndex = Number(item.tabindex) || 0;
        const keepFieldFocus = (event) => {
            event.stopPropagation();
            input.focus();
        };
        // PDF.js owns the surrounding page and otherwise consumes trusted
        // pointer events before the embedded control becomes the active field.
        input.addEventListener("pointerdown", keepFieldFocus);
        input.addEventListener("click", keepFieldFocus);
        signatureItem[0].addEventListener("focus_signature", () => input.focus());
        // Keep the live value in the submission payload, but do not ask OCA to
        // recalculate while the user is typing: that replaces the iframe input.
        input.addEventListener("input", (event) => {
            item.value = event.currentTarget.value;
        });
        input.addEventListener("change", (event) => {
            item.value = event.currentTarget.value;
            parent.checkFilledAll();
        });
        input.addEventListener("keydown", (event) => {
            if (event.key !== "Tab") {
                return;
            }
            event.preventDefault();
            const nextItem = Object.values(parent.info.items)
                .filter(
                    (candidate) =>
                        candidate.tabindex > item.tabindex &&
                        candidate.role_id === parent.info.role_id
                )
                .sort((left, right) => left.tabindex - right.tabindex)[0];
            input.blur();
            parent.items?.[nextItem?.id]?.dispatchEvent(new Event("focus_signature"));
        });
        return input;
    },
});

patch(checkField, {
    generate(parent, item) {
        return generateWithOcaRoleCompatibility(item, () =>
            super.generate(...arguments)
        );
    },
});

patch(signatureField, {
    generate(parent, item, signatureItem) {
        const input = $(
            renderToString("sign_oca.sign_iframe_field_signature", {item})
        )[0];
        if (item.role_id === parent.info.role_id) {
            const openDialog = () => {
                const initials = item.kind === "initials";
                parent.dialogService.add(initials ? InitialsDialog : SignatureDialog, {
                    nameAndSignatureProps: {
                        fontColor: "DarkBlue",
                        ...(initials ? {signatureType: "initial"} : {}),
                    },
                    defaultName: parent.info.partner.name,
                    uploadSignature: (data) =>
                        this.uploadSignature(parent, item, signatureItem, data),
                });
            };
            signatureItem[0].addEventListener("focus_signature", openDialog);
            input.addEventListener("click", (event) => {
                event.preventDefault();
                event.stopPropagation();
                openDialog();
            });
            input.setAttribute("role", "button");
            input.setAttribute("tabindex", item.tabindex || 0);
            input.setAttribute("aria-label", `Add ${item.name || "signature"}`);
            input.addEventListener("keydown", (event) => {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    openDialog();
                    return;
                }
                if (event.key !== "Tab") {
                    return;
                }
                event.preventDefault();
                const nextItem = Object.values(parent.info.items)
                    .filter(
                        (candidate) =>
                            candidate.tabindex > item.tabindex &&
                            candidate.role_id === parent.info.role_id
                    )
                    .sort((left, right) => left.tabindex - right.tabindex)[0];
                input.blur();
                if (nextItem && parent.items?.[nextItem.id]) {
                    parent.items[nextItem.id].dispatchEvent(new Event("focus_signature"));
                }
            });
        }
        return input;
    },
});

patch(SignOcaPdfPortal.prototype, {
    setup() {
        super.setup(...arguments);
        this.dialog = useService("dialog");
    },

    checkToSign() {
        super.checkToSign(...arguments);
        this._syncConsentState();
    },

    checkSignItemsCompletion() {
        return Object.values(this.info.items)
            .filter(
                (item) =>
                    item.role_id === this.info.role_id &&
                    !registry.category("sign_oca").get(item.field_type).check(item)
            )
            .sort((left, right) => left.tabindex - right.tabindex)
            .map((item) => ({data: item, el: this.items[item.id]}))
            .filter(({el}) => Boolean(el));
    },

    _syncConsentState() {
        const consent = document.getElementById("usl_sign_consent");
        const button = document.getElementById("sign_oca_button");
        if (!consent || !button) {
            return;
        }
        const isSubmitting = button.dataset.submitting === "true";
        button.disabled = !this.to_sign_update || !consent.checked || isSubmitting;
        consent.closest(".usl_sign_consent_choice")?.classList.toggle(
            "is-checked",
            consent.checked
        );
    },

    _onConsentChanged(event) {
        if (event.currentTarget.checked) {
            document.getElementById("usl_sign_consent_error")?.classList.add("d-none");
        }
        this._syncConsentState();
    },

    _setSubmissionState(button, {busy, label, complete = false}) {
        const consent = document.getElementById("usl_sign_consent");
        const spinner = document.getElementById("usl_sign_submission_spinner");
        const buttonLabel = document.getElementById("usl_sign_submission_label");
        const status = document.getElementById("usl_sign_submission_status");
        button.dataset.submitting = busy ? "true" : "false";
        button.setAttribute("aria-busy", busy ? "true" : "false");
        spinner?.classList.toggle("d-none", !busy || complete);
        if (buttonLabel) {
            buttonLabel.textContent = label;
        }
        if (consent) {
            consent.disabled = busy;
        }
        status?.classList.toggle("d-none", !busy);
        if (complete && status) {
            status.querySelector("span").textContent =
                _t("Signature saved. Opening the result…");
            status.querySelector("i")?.classList.replace("fa-lock", "fa-check-circle");
        }
        this._syncConsentState();
    },

    postIframeFields() {
        super.postIframeFields(...arguments);
        const iframeDocument = this.iframe.el.contentDocument;
        if (!iframeDocument.getElementById("usl-sign-portal-viewer-style")) {
            const style = iframeDocument.createElement("style");
            style.id = "usl-sign-portal-viewer-style";
            style.textContent = `
                #editorModeButtons, #printButton, #downloadButton, #secondaryPrint,
                #secondaryDownload, #viewBookmark, #openFile, #sidebarToggleButton,
                #viewFindButton, #secondaryToolbarToggle { display: none !important; }
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
        const button = ev.currentTarget;
        if (button.dataset.submitting === "true") {
            return;
        }
        const invalidInput = this.iframe.el.contentDocument.querySelector(
            ".o_sign_oca_field input:invalid"
        );
        if (invalidInput) {
            invalidInput.focus();
            invalidInput.reportValidity();
            return;
        }
        const consent = document.getElementById("usl_sign_consent");
        const error = document.getElementById("usl_sign_consent_error");
        if (!consent?.checked) {
            error?.classList.remove("d-none");
            consent?.focus();
            return;
        }
        error?.classList.add("d-none");
        const submissionError = document.getElementById("usl_sign_submission_error");
        submissionError?.classList.add("d-none");
        this._setSubmissionState(button, {busy: true, label: _t("Finalizing…")});
        try {
            const position = await this.getLocation();
            const action = await this.rpc(
                `/sign_oca/sign/${this.signer_id}/${this.access_token}`,
                {
                    items: this.info.items,
                    document_sha256: this.info.document_sha256,
                    consent: true,
                    latitude: position?.coords?.latitude,
                    longitude: position?.coords?.longitude,
                }
            );
            this._setSubmissionState(button, {
                busy: true,
                complete: true,
                label: _t("Signed"),
            });
            window.location = action.type === "ir.actions.act_url" ? action.url : window.location;
        } catch (rpcError) {
            if (submissionError) {
                submissionError.textContent =
                    rpcError?.data?.message ||
                    _t(
                        "The signature could not be submitted. Reload the document and try again."
                    );
                submissionError.classList.remove("d-none");
                submissionError.focus();
            }
            this._setSubmissionState(button, {
                busy: false,
                label: _t("Confirm and sign"),
            });
        }
    },

    _onClickDecline() {
        this.dialog.add(DeclineDocumentDialog, {
            confirm: async (reason) => {
                await this.rpc(`/sign/decline/${this.signer_id}/${this.access_token}`, {
                    reason,
                });
                window.location = "/sign/result/declined";
            },
        });
    },
});
