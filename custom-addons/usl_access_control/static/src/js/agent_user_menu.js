/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";

registry.category("user_menuitems").add("usl_my_agents", (env) => ({
    type: "item",
    id: "usl_my_agents",
    description: _t("My Agents"),
    sequence: 55,
    callback: async () => {
        const action = await env.services.orm.call("usl.agent", "action_my_agents");
        await env.services.action.doAction(action);
    },
}));
