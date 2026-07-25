import { registry } from "@web/core/registry";

registry.category("actions").add("rebuild_accounting_home", async (env) => {
    const action = await env.services.orm.call(
        "rebuild.account.review.summary",
        "action_open_accounting_home",
        [],
    );
    return env.services.action.doAction(action, {
        stackPosition: "replaceCurrentAction",
    });
});
