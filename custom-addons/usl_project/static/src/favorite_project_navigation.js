import { registry } from "@web/core/registry";
import { rpcBus } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";

import { projectIsFavoriteField } from "@project/components/project_is_favorite/project_is_favorite_field";
import { showProjectForm } from "@project/actions/client_actions";
import { ProjectProjectFormController } from "@project/views/project_form/project_project_form_controller";

import { patch } from "@web/core/utils/patch";

const FAVORITE_MENU_FIELDS = new Set([
    "is_favorite",
    "name",
    "active",
    "sequence",
    "is_template",
    "company_id",
    "privacy_visibility",
]);

const FAVORITE_MENU_MUTATION_METHODS = new Set([
    "action_archive",
    "action_unarchive",
    "action_create_template_from_project",
    "action_undo_convert_to_template",
    "create_template_from_project_undo_callback",
]);

registry.category("services").add("uslProjectFavoriteMenuRefresh", {
    dependencies: ["menu"],
    start(_env, { menu }) {
        rpcBus.addEventListener("RPC:RESPONSE", ({ detail }) => {
            const { model, method } = detail.data.params;
            if (
                !detail.error &&
                model === "project.project" &&
                FAVORITE_MENU_MUTATION_METHODS.has(method)
            ) {
                menu.reload();
            }
        });
    },
});

class UslProjectIsFavoriteField extends projectIsFavoriteField.component {
    setup() {
        super.setup();
        this.menu = useService("menu");
    }

    async update() {
        if (this.props.readonly) {
            return;
        }
        await super.update();
        if (this.props.autosave) {
            await this.menu.reload();
        }
    }
}

registry.category("fields").add(
    "project_is_favorite",
    {
        ...projectIsFavoriteField,
        component: UslProjectIsFavoriteField,
    },
    { force: true }
);

patch(ProjectProjectFormController.prototype, {
    setup() {
        super.setup(...arguments);
        this.uslProjectMenu = useService("menu");
    },

    async onRecordSaved(record, changes) {
        await super.onRecordSaved(...arguments);
        if (Object.keys(changes).some((fieldName) => FAVORITE_MENU_FIELDS.has(fieldName))) {
            await this.uslProjectMenu.reload();
        }
    },
});

registry.category("actions").add(
    "project_top_menu_overview",
    async (env, action) => {
        await showProjectForm(env, {
            model: "project.project",
            recordId: action.res_id,
        });
        return action.next;
    },
    { force: true }
);
