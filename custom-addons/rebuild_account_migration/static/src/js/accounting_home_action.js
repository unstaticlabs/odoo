import { registry } from "@web/core/registry";

registry.category("actions").add("rebuild_accounting_home", async (env, clientAction) => {
    const accountingApp = env.services.menu
        .getApps()
        .find((app) => app.actionID === clientAction.id);
    if (accountingApp) {
        env.services.menu.setCurrentMenu(accountingApp);
    }
    const action = await env.services.orm.call(
        "rebuild.account.overview",
        "action_open_accounting_home",
        [],
    );
    return env.services.action.doAction(action, {
        stackPosition: "replaceCurrentAction",
    });
});
