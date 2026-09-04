/** @odoo-module **/

import { CheckIdentityForm } from "@web/core/session/check_identity";
import { patch } from "@web/core/utils/patch";
import { confirmWithPocketID } from "./pocketid_reauthentication";

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
        const error = await confirmWithPocketID();
        if (error) {
            this.state.error = error;
            return;
        }
        await this.checkIdentityService.check({ type: "usl_pocketid" });
    },
});
