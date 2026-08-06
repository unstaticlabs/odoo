/** @odoo-module **/

import { CheckIdentityForm } from "@web/core/session/check_identity";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

function wait(delay) {
    return new Promise((resolve) => setTimeout(resolve, delay));
}

patch(CheckIdentityForm.prototype, {
    setup() {
        super.setup(...arguments);
        this.authMethodTemplates.usl_pocketid = {
            form: "usl_pocketid.CheckIdentityForm",
            linkString: "usl_pocketid.CheckIdentityLink",
        };
    },

    async onSubmit(event) {
        const type = new FormData(event.target).get("type");
        if (type !== "usl_pocketid") {
            return super.onSubmit(...arguments);
        }
        event.preventDefault();
        this.state.error = false;
        const popup = window.open(
            "/usl/pocketid/reauth/start",
            "usl_pocketid_reauth",
            "popup=yes,width=520,height=720",
        );
        if (!popup) {
            this.state.error = _t("Allow the Pocket ID confirmation window and try again.");
            return;
        }
        const deadline = Date.now() + 5 * 60 * 1000;
        while (!popup.closed && Date.now() < deadline) {
            await wait(250);
            try {
                if (popup.location.pathname === "/usl/pocketid/reauth/complete") {
                    const error = new URLSearchParams(popup.location.search).get("error");
                    popup.close();
                    if (error) {
                        this.state.error = error;
                        return;
                    }
                    await this.checkIdentityService.check({ type: "usl_pocketid" });
                    return;
                }
            } catch {
                // The popup is on the Pocket ID origin until its callback completes.
            }
        }
        if (!popup.closed) {
            popup.close();
        }
        this.state.error = _t("Pocket ID confirmation was not completed.");
    },
});
