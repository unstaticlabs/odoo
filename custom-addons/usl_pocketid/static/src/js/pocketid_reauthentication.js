/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";

function wait(delay) {
    return new Promise((resolve) => setTimeout(resolve, delay));
}

/**
 * Complete a fresh Pocket ID authentication in a same-origin popup.
 *
 * @returns {Promise<string|false>} an error message, or false on success
 */
export async function confirmWithPocketID() {
    const popup = window.open(
        "/usl/pocketid/reauth/start",
        "usl_pocketid_reauth",
        "popup=yes,width=520,height=720",
    );
    if (!popup) {
        return _t("Allow the Pocket ID confirmation window and try again.");
    }
    const deadline = Date.now() + 5 * 60 * 1000;
    while (!popup.closed && Date.now() < deadline) {
        await wait(250);
        try {
            if (popup.location.pathname === "/usl/pocketid/reauth/complete") {
                const error = new URLSearchParams(popup.location.search).get("error");
                popup.close();
                return error || false;
            }
        } catch {
            // The popup is on the Pocket ID origin until its callback completes.
        }
    }
    if (!popup.closed) {
        popup.close();
    }
    return _t("Pocket ID confirmation was not completed.");
}
