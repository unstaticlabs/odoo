import { Component, onWillStart, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { router } from "@web/core/browser/router";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { withPortableActionAlias } from "./navigation_state";

export class WorkspaceLoader extends Component {
    static template = "usl_canonical_navigation.WorkspaceLoader";
    static props = { ...standardActionServiceProps };

    setup() {
        this.action = useService("action");
        this.navigation = useService("canonical_navigation");
        this.state = useState({ status: "loading" });
        onWillStart(async () => {
            const result = await this.navigation.loadWorkspace(router.current.ws);
            if (result.status !== "ok") {
                this.state.status = "unavailable";
                return;
            }
            const state = result.state;
            const target = state.action || {
                type: "ir.actions.act_window",
                res_model: state.model,
                res_id: state.res_id || undefined,
                views: [[false, state.res_id ? "form" : state.view_type || "list"]],
            };
            await withPortableActionAlias(() =>
                this.action.doAction(target, {
                    additionalContext: {},
                    props: state.res_id ? { resId: state.res_id } : undefined,
                    viewType: state.view_type,
                })
            );
            this.state.status = "loaded";
        });
    }

    get title() {
        return _t("Workspace unavailable");
    }
}

export class NavigationUnavailable extends Component {
    static template = "usl_canonical_navigation.NavigationUnavailable";
    static props = { ...standardActionServiceProps };

    setup() {
        this.action = useService("action");
    }

    goHome() {
        this.action.doAction("menu", { clearBreadcrumbs: true });
    }
}

registry
    .category("actions")
    .add("usl_canonical_navigation.workspace_loader", WorkspaceLoader)
    .add("usl_canonical_navigation.unavailable", NavigationUnavailable);
