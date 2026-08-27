/** @odoo-module **/

import { Component } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const actionRegistry = registry.category("actions");

const workspaceActionLoader = async (env, action) => {
    await loadBundle("usl_documents.assets_workspace");
    if (actionRegistry.get("usl_documents.workspace") === workspaceActionLoader) {
        actionRegistry.add(
            "usl_documents.workspace",
            () => {
                env.services.notification.add(
                    _t("The Documents workspace could not be loaded."),
                    { type: "danger" }
                );
            },
            { force: true }
        );
    }
    return action;
};

actionRegistry.add("usl_documents.workspace", workspaceActionLoader);

export class OpenDocumentsField extends Component {
    static template = "usl_documents.OpenDocumentsField";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
    }

    get count() {
        return Number(this.props.record.data[this.props.name]) || 0;
    }

    get label() {
        return `Open ${this.count} document${this.count === 1 ? "" : "s"}`;
    }

    async open() {
        const action = await this.orm.call(
            this.props.record.resModel,
            "action_open_documents",
            [[this.props.record.resId]]
        );
        return this.action.doAction(action);
    }
}

registry.category("fields").add("usl_open_documents", {
    component: OpenDocumentsField,
    supportedTypes: ["integer"],
});
