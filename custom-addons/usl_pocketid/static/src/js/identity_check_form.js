/** @odoo-module **/

import {
    PassKeyIdentityCheckFormController,
    PassKeyIdentityCheckFormView,
} from "@auth_passkey/views/auth_passkey_identity_check_form_view";
import { registry } from "@web/core/registry";
import { confirmWithPocketID } from "./pocketid_reauthentication";

export class PocketIDIdentityCheckFormController extends PassKeyIdentityCheckFormController {
    async beforeExecuteActionButton(clickParams) {
        if (
            clickParams.name === "run_check" &&
            this.model.root.data.auth_method === "usl_pocketid"
        ) {
            const error = await confirmWithPocketID();
            if (error) {
                this.env.services.notification.add(error, { type: "danger" });
                return false;
            }
        }
        return super.beforeExecuteActionButton(...arguments);
    }
}

export const PocketIDIdentityCheckFormView = {
    ...PassKeyIdentityCheckFormView,
    Controller: PocketIDIdentityCheckFormController,
};

registry.category("views").add(
    "usl_pocketid_identity_check_form",
    PocketIDIdentityCheckFormView,
);
