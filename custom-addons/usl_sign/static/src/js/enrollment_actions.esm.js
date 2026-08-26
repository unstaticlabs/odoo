/** @odoo-module **/

import {browser} from "@web/core/browser/browser";
import {_t} from "@web/core/l10n/translation";
import {registry} from "@web/core/registry";

export async function copyText(text) {
    if (browser.navigator.clipboard?.writeText) {
        try {
            await browser.navigator.clipboard.writeText(text);
            return;
        } catch {
            // Private HTTP QA origins may expose the API but still reject it.
            // Fall back to a synchronous selection-based copy below.
        }
    }
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand("copy");
    textarea.remove();
    if (!copied) {
        throw new Error("Clipboard copy was rejected");
    }
}

async function copySetupLink(env, action) {
    try {
        await copyText(action.params.url);
        env.services.notification.add(_t("Setup link copied to your clipboard."), {
            type: "success",
        });
    } catch {
        env.services.notification.add(
            _t("Your browser blocked automatic copying. Use the copy button in the dialog."),
            {type: "warning"}
        );
    }
    return action.params.next;
}

registry.category("actions").add("usl_sign.copy_setup_link", copySetupLink);
