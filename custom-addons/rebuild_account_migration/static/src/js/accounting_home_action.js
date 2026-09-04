import { onWillRender } from "@web/owl2/utils";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { ButtonBox } from "@web/views/form/button_box/button_box";

export function updateButtonBoxLayout(buttonBox, defaultMaximum) {
    const classNames = buttonBox.props.class.split(/\s+/);
    const maximum =
        !buttonBox.env.isSmall && classNames.includes("o_usl_overview_shortcuts")
            ? 8
            : defaultMaximum;
    const allVisibleButtons = Object.entries(buttonBox.props.slots)
        .filter(([_, slot]) => buttonBox.isSlotVisible(slot))
        .map(([slotName]) => slotName);
    if (allVisibleButtons.length <= maximum) {
        buttonBox.visibleButtons = allVisibleButtons;
        buttonBox.additionalButtons = [];
        buttonBox.isFull = allVisibleButtons.length === maximum;
    } else {
        const splitIndex = Math.max(maximum - 1, 0);
        buttonBox.visibleButtons = allVisibleButtons.slice(0, splitIndex);
        buttonBox.additionalButtons = allVisibleButtons.slice(splitIndex);
        buttonBox.isFull = true;
    }
}

patch(ButtonBox.prototype, {
    setup() {
        const ui = useService("ui");
        onWillRender(() => {
            const maximum = [0, 0, 7, 4, 5, 8][ui.size] ?? 8;
            updateButtonBoxLayout(this, maximum);
        });
    },
});

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

registry.category("actions").add("rebuild_accounting_hygiene", async (env) => {
    const action = await env.services.orm.call(
        "rebuild.account.overview",
        "action_open_current_company_hygiene",
        [],
    );
    return env.services.action.doAction(action, {
        stackPosition: "replaceCurrentAction",
    });
});
