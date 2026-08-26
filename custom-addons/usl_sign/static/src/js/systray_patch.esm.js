/** @odoo-module **/
/* global document */

import {patch} from "@web/core/utils/patch";
import {SignerMenuView} from "@sign_oca/js/systray_service.esm";

patch(SignerMenuView.prototype, {
    async onClickFilterButton() {
        document.body.click();
        const action = await this.orm.call("res.users", "action_open_usl_sign_requests", []);
        return this.action.doAction(action, {clearBreadcrumbs: true});
    },
});
