import { registry } from "@web/core/registry";

registry.category("actions").add("rebuild_accounting_home", async (env) => {
    return env.services.orm.call(
        "rebuild.account.review.summary",
        "action_open_accounting_home",
        [],
    );
});
