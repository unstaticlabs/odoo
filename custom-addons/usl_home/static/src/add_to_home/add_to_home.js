import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { STATIC_ACTIONS_GROUP_NUMBER } from "@web/search/action_menus/action_menus";
import { Component } from "@odoo/owl";

const cogMenuRegistry = registry.category("cogMenu");

export class AddToHome extends Component {
    static template = "usl_home.AddToHome";
    static components = { DropdownItem };
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
    }

    async addToHome() {
        const config = this.env.config;
        const payload = {
            action_id: config.actionId,
            menu_id: config.menuId || false,
            name: config.getDisplayName(),
            view_mode: config.viewType,
            res_model: config.resModel || false,
            res_id: this.env.model?.root?.resId || false,
            domain: [],
            context: {},
            group_by: [],
            order_by: [],
        };
        if (this.env.searchModel) {
            const { domain, globalContext } = this.env.searchModel;
            const { context, groupBys, orderBy } = this.env.searchModel.getPreFavoriteValues();
            payload.domain = domain;
            payload.context = {
                ...Object.fromEntries(
                    Object.entries(globalContext).filter(
                        ([key]) => !key.startsWith("search_default_")
                    )
                ),
                ...context,
            };
            payload.group_by = groupBys;
            payload.order_by = orderBy;
        }
        try {
            await this.orm.call("usl.home.favorite", "add_current_destination", [payload]);
            this.notification.add(_t("“%s” was added to Home.", payload.name), {
                type: "success",
            });
        } catch (error) {
            this.notification.add(error.data?.message || _t("This destination could not be added."), {
                type: "danger",
            });
        }
    }
}

export const addToHomeItem = {
    Component: AddToHome,
    groupNumber: STATIC_ACTIONS_GROUP_NUMBER,
    isDisplayed: ({ config }) =>
        config.actionType === "ir.actions.act_window" &&
        Boolean(config.actionId) &&
        config.actionId !== "usl_home.action_usl_home",
};

cogMenuRegistry.add("usl-add-to-home", addToHomeItem, { sequence: 20 });
