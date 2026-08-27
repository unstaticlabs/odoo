/** @odoo-module **/
/* global Event */

import {patch} from "@web/core/utils/patch";
import {registry} from "@web/core/registry";
import {Component, onWillUnmount, useState} from "@odoo/owl";
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
    static props = {
        ...SignatureDialog.props,
        autoFillDefault: {type: Boolean, optional: true},
    };

    setup() {
        super.setup();
        this.adoption = useState({autoFill: this.props.autoFillDefault ?? true});
    }

    onAutoFillChanged(event) {
        this.adoption.autoFill = event.currentTarget.checked;
    }

    onClickConfirm() {
        this.props.uploadSignature({
            name: this.signature.name,
            signatureImage: this.signature.getSignatureImage(),
            autoFill: this.adoption.autoFill,
        });
        this.props.close();
    }
}

class PersonalSignatureDialog extends InitialsDialog {
    static template = "usl_sign.PersonalSignatureDialog";
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

const LOCATION_MESSAGES = {
    idle: _t("Location has not been requested yet."),
    requesting: _t("Waiting for your location choice…"),
    granted: _t("Approximate browser location will be included in the protected proof."),
    refused: _t("Location was declined. You can still sign."),
    unavailable: _t("Location is unavailable. You can still sign."),
    unsupported: _t("This browser does not provide location. You can still sign."),
    timeout: _t("Location did not respond in time. You can still sign."),
};

function browserContext() {
    const hints = navigator.userAgentData;
    return {
        user_agent: navigator.userAgent || "",
        platform: navigator.platform || "",
        language: navigator.language || "",
        languages: Array.from(navigator.languages || []).slice(0, 20),
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "",
        hardware_concurrency: navigator.hardwareConcurrency || 0,
        device_memory: navigator.deviceMemory || 0,
        max_touch_points: navigator.maxTouchPoints || 0,
        screen: {
            width: window.screen?.width || 0,
            height: window.screen?.height || 0,
            color_depth: window.screen?.colorDepth || 0,
            pixel_ratio: window.devicePixelRatio || 1,
        },
        viewport: {
            width: window.innerWidth || 0,
            height: window.innerHeight || 0,
        },
        client_hints: hints
            ? {
                  brands: Array.from(hints.brands || []).slice(0, 10),
                  mobile: Boolean(hints.mobile),
                  platform: hints.platform || "",
              }
            : {},
    };
}

class FieldGuide {
    constructor(parent) {
        this.parent = parent;
        this.viewer = parent.iframe.el.contentDocument.getElementById("viewerContainer");
        this.currentId = null;
        this.navigationToken = 0;
        this.cancelNavigation = this.cancelNavigation.bind(this);
        this.refresh = this.refresh.bind(this);
        this.handleResize = this.handleResize.bind(this);
        this.viewer?.addEventListener("wheel", this.cancelNavigation, {passive: true});
        this.viewer?.addEventListener("touchstart", this.cancelNavigation, {passive: true});
        this.viewer?.addEventListener("pointerdown", this.cancelNavigation, {passive: true});
        window.addEventListener("resize", this.handleResize, {passive: true});
        this.refresh();
    }

    destroy() {
        this.viewer?.removeEventListener("wheel", this.cancelNavigation);
        this.viewer?.removeEventListener("touchstart", this.cancelNavigation);
        this.viewer?.removeEventListener("pointerdown", this.cancelNavigation);
        window.removeEventListener("resize", this.handleResize);
    }

    cancelNavigation() {
        this.navigationToken += 1;
        if (this.viewer) {
            this.viewer.scrollTo({top: this.viewer.scrollTop, behavior: "auto"});
        }
    }

    handleResize() {
        this.cancelNavigation();
        this.refresh();
    }

    incompleteIds() {
        return Object.values(this.parent.info.items)
            .filter(
                (item) =>
                    item.required &&
                    item.role_id === this.parent.info.role_id &&
                    !registry.category("sign_oca").get(item.field_type).check(item)
            )
            .sort(
                (left, right) =>
                    (Number(left.tabindex) || 0) - (Number(right.tabindex) || 0) ||
                    (Number(left.page) || 0) - (Number(right.page) || 0) ||
                    (Number(left.position_y) || 0) - (Number(right.position_y) || 0) ||
                    (Number(left.position_x) || 0) - (Number(right.position_x) || 0) ||
                    Number(left.id) - Number(right.id)
            )
            .map((item) => String(item.id));
    }

    refresh() {
        const ids = this.incompleteIds();
        if (this.currentId && !ids.includes(this.currentId)) {
            this.currentId = null;
        }
        this.parent.uslGuide.remaining = ids.length;
        this.parent.uslGuide.current = this.currentId
            ? ids.indexOf(this.currentId) + 1
            : 0;
        return ids;
    }

    move(direction) {
        const ids = this.refresh();
        if (!ids.length) {
            this.currentId = null;
            return;
        }
        const currentIndex = ids.indexOf(this.currentId);
        const nextIndex =
            currentIndex < 0
                ? direction > 0
                    ? 0
                    : ids.length - 1
                : (currentIndex + direction + ids.length) % ids.length;
        this.currentId = ids[nextIndex];
        this.parent.uslGuide.current = nextIndex + 1;
        this.focus(this.currentId);
    }

    focus(itemId) {
        const field = this.parent.items[itemId];
        if (!field?.isConnected) {
            this.refresh();
            return;
        }
        const token = ++this.navigationToken;
        field.classList.add("usl_sign_field_target");
        field.scrollIntoView({behavior: "smooth", block: "center", inline: "center"});
        window.setTimeout(() => {
            field.classList.remove("usl_sign_field_target");
            if (token !== this.navigationToken || !field.isConnected) {
                return;
            }
            field
                .querySelector('input, button, [role="button"], [tabindex]')
                ?.focus({preventScroll: true});
        }, 350);
    }
}

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
        // Keep the submission, guide and confirmation state current on every
        // edit. OCA's completion check only updates state; it does not replace
        // the live iframe control.
        input.addEventListener("input", (event) => {
            item.value = event.currentTarget.value;
            parent.checkFilledAll();
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
            renderToString("sign_oca.sign_iframe_field_signature", {
                item,
                placeholder: item.kind === "initials" ? _t("Add initials") : _t("Add signature"),
            })
        )[0];
        if (item.role_id === parent.info.role_id) {
            const openDialog = () => {
                const initials = item.kind === "initials";
                const preferenceKind = initials ? "initials" : "signature";
                const preferences = (parent.uslSigningPreferences ||= {
                    name: parent.info.partner.name,
                    autoFill: true,
                    initials: null,
                    signature: null,
                });
                const saved = preferences[preferenceKind];
                if (!item.value && saved?.autoFill) {
                    this.uploadSignature(parent, item, signatureItem, saved);
                    return;
                }
                parent.dialogService.add(initials ? InitialsDialog : PersonalSignatureDialog, {
                    nameAndSignatureProps: {
                        fontColor: "DarkBlue",
                        ...(initials ? {signatureType: "initial"} : {}),
                    },
                    defaultName: preferences.name,
                    autoFillDefault: preferences.autoFill,
                    uploadSignature: (data) => {
                        const autoFill = data.autoFill ?? preferences.autoFill ?? true;
                        preferences.name = data.name || preferences.name;
                        preferences.autoFill = autoFill;
                        preferences[preferenceKind] = {
                            name: preferences.name,
                            signatureImage: data.signatureImage,
                            autoFill,
                        };
                        this.uploadSignature(parent, item, signatureItem, data);
                        if (autoFill) {
                            for (const candidate of Object.values(parent.info.items)) {
                                if (
                                    candidate.id === item.id ||
                                    candidate.value ||
                                    candidate.role_id !== parent.info.role_id ||
                                    candidate.kind !== item.kind
                                ) {
                                    continue;
                                }
                                this.uploadSignature(
                                    parent,
                                    candidate,
                                    $(parent.items[candidate.id] || []),
                                    data
                                );
                            }
                        }
                    },
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
        this.uslGuide = useState({remaining: 0, current: 0});
        this.uslLocation = useState({status: "idle", payload: {status: "unavailable"}});
        this.uslLocationPromise = null;
        onWillUnmount(() => this.uslFieldGuide?.destroy());
    },

    checkToSign() {
        this.to_sign = this.to_sign_update;
        $(this.signOcaFooter.el).show();
        this._syncConsentState();
        this.uslFieldGuide?.refresh();
    },

    checkSignItemsCompletion() {
        return Object.values(this.info.items)
            .filter(
                (item) =>
                    item.required &&
                    item.role_id === this.info.role_id &&
                    !registry.category("sign_oca").get(item.field_type).check(item)
            )
            .sort((left, right) => left.tabindex - right.tabindex)
            .map((item) => ({data: item, el: this.items[item.id]}))
            .filter(({el}) => Boolean(el));
    },

    checkFilledAll() {
        super.checkFilledAll(...arguments);
        this.uslFieldGuide?.refresh();
    },

    _syncConsentState() {
        const consent = document.getElementById("usl_sign_consent");
        const button = document.getElementById("sign_oca_button");
        if (!consent || !button) {
            return;
        }
        const isSubmitting = button.dataset.submitting === "true";
        const locationPending = ["idle", "requesting"].includes(this.uslLocation.status);
        button.disabled =
            !this.to_sign_update || !consent.checked || isSubmitting || locationPending;
        consent.closest(".usl_sign_consent_choice")?.classList.toggle(
            "is-checked",
            consent.checked
        );
    },

    async _onConsentChanged(event) {
        if (event.currentTarget.checked) {
            document.getElementById("usl_sign_consent_error")?.classList.add("d-none");
            await this._requestLocationOnce();
        }
        this._syncConsentState();
    },

    async _requestLocationOnce() {
        if (this.uslLocationPromise) {
            return this.uslLocationPromise;
        }
        if (!navigator.geolocation) {
            this.uslLocation.status = "unsupported";
            this.uslLocation.payload = {status: "unsupported"};
            return this.uslLocation.payload;
        }
        this.uslLocation.status = "requesting";
        this._syncConsentState();
        this.uslLocationPromise = new Promise((resolve) => {
            navigator.geolocation.getCurrentPosition(
                (position) =>
                    resolve({
                        status: "granted",
                        latitude: position.coords.latitude,
                        longitude: position.coords.longitude,
                        accuracy: position.coords.accuracy,
                    }),
                (error) => {
                    const statuses = {
                        [error.PERMISSION_DENIED]: "refused",
                        [error.POSITION_UNAVAILABLE]: "unavailable",
                        [error.TIMEOUT]: "timeout",
                    };
                    resolve({status: statuses[error.code] || "unavailable"});
                },
                {enableHighAccuracy: false, maximumAge: 0, timeout: 8000}
            );
        }).then((payload) => {
            this.uslLocation.payload = payload;
            this.uslLocation.status = payload.status;
            this._syncConsentState();
            return payload;
        });
        return this.uslLocationPromise;
    },

    get locationMessage() {
        return LOCATION_MESSAGES[this.uslLocation.status] || LOCATION_MESSAGES.unavailable;
    },

    previousIncompleteField() {
        this.uslFieldGuide?.move(-1);
    },

    nextIncompleteField() {
        this.uslFieldGuide?.move(1);
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
                .o_sign_oca_field {
                    box-sizing: border-box;
                    min-height: 2rem;
                    overflow: visible;
                    z-index: 20;
                    background: rgba(255, 244, 204, .96) !important;
                    border: 2px solid #9b6b00;
                    border-radius: .35rem;
                    box-shadow: 0 .15rem .45rem rgba(0, 0, 0, .16);
                }
                .o_sign_oca_field:focus-within,
                .o_sign_oca_field:hover {
                    border-color: #714b67;
                    box-shadow: 0 .2rem .65rem rgba(113, 75, 103, .28);
                }
                .o_sign_oca_field input,
                .o_sign_oca_field [role="button"] {
                    box-sizing: border-box;
                    min-width: 100%;
                    min-height: 100%;
                    padding: .25rem .4rem;
                    color: #211a1f;
                    background: transparent;
                    border: 0;
                    font: 600 13px/1.25 system-ui, sans-serif;
                }
                .o_sign_oca_field [role="button"] {
                    display: grid;
                    place-items: center;
                    cursor: pointer;
                }
                .o_sign_oca_field img { object-fit: contain; }
                .usl_sign_field_target { outline: 3px solid #714b67 !important; outline-offset: 3px; }
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
        this.uslFieldGuide?.refresh();
    },

    navigate() {
        const iframeDocument = this.iframe.el.contentDocument;
        iframeDocument.querySelector(".o_sign_sign_item_navigator")?.remove();
        iframeDocument.querySelector(".o_sign_sign_item_navline")?.remove();
        this.uslFieldGuide?.destroy();
        this.uslFieldGuide = new FieldGuide(this);
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
            const location = await this._requestLocationOnce();
            const context = browserContext();
            if (this.info.requested_trust === "strong_personal") {
                if (typeof window.uslStrongSign !== "function") {
                    throw new Error("Strong signing is unavailable.");
                }
                await window.uslStrongSign({
                    button,
                    items: this.info.items,
                    documentSha256: this.info.document_sha256,
                    location,
                    browserContext: context,
                });
                return;
            }
            const action = await this.rpc(
                `/sign_oca/sign/${this.signer_id}/${this.access_token}`,
                {
                    items: this.info.items,
                    document_sha256: this.info.document_sha256,
                    consent: true,
                    location,
                    browser_context: context,
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
                    rpcError?.message ||
                    _t(
                        "The signature could not be submitted. Reload the document and try again."
                    );
                submissionError.classList.remove("d-none");
                submissionError.focus();
            }
            this._setSubmissionState(button, {
                busy: false,
                label: _t("Sign"),
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
