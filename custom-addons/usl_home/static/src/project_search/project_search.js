import { onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { SearchBar } from "@web/search/search_bar/search_bar";
import { _t } from "@web/core/l10n/translation";
import { projectProjectKanbanView, projectProjectKanbanGroupStageView } from "@project/views/project_project_kanban/project_task_kanban_view";
import { projectProjectListView } from "@project/views/project_project_list/project_project_list_view";

export class ProjectsContentSearchBar extends SearchBar {
    static template = "usl_home.ProjectsContentSearchBar";

    setup() {
        super.setup();
        this.action = useService("action");
        this.notification = useService("notification");
        this.contentGeneration = 0;
        onWillUnmount(() => {
            this.contentGeneration++;
            clearTimeout(this.contentTimer);
        });
    }

    async computeState(options = {}) {
        const generation = ++this.contentGeneration;
        clearTimeout(this.contentTimer);
        await super.computeState(options);
        const query = this.state.query.trim();
        if (!query || generation !== this.contentGeneration) {
            return;
        }
        this.contentTimer = setTimeout(async () => {
            try {
                const { groups } = await this.orm.call("usl.home.service", "search_records", [query], { categories: ["tasks", "projects"] });
                if (generation !== this.contentGeneration) {
                    return;
                }
                const items = groups.flatMap((group) => group.records.map((record) => ({
                    id: `usl-content-${record.model}-${record.id}`,
                    title: record.title, record,
                    label: `${group.label}: ${record.title}`,
                })));
                this.items.unshift(...items);
            } catch {
                // Native project filters remain usable if direct lookup fails.
                if (generation === this.contentGeneration) {
                    this.notification.add(_t("Direct task matches could not be loaded. Project filters remain available."), { type: "warning" });
                }
            }
        }, 250);
    }

    getDropdownNavigation() {
        const options = super.getDropdownNavigation();
        options.onItemActivated = (item) => {
            const custom = this.items.find((entry) => entry.record && entry.id === item.id);
            this.lastActiveItemId = custom ? custom.id : Number.parseInt(item.id, 10);
        };
        return options;
    }

    async selectItem(item) {
        if (!item.record) {
            return super.selectItem(item);
        }
        try {
            const action = await this.orm.call("usl.home.service", "get_search_result_action", [item.record.model, item.record.id]);
            this.inputDropdownState.close();
            await this.action.doAction(action);
        } catch {
            this.notification.add(_t("This record could not be opened. Try searching again."), { type: "warning" });
        }
    }
}

// Preserve each native Project controller/model/renderer and replace only its
// SearchBar component. Other apps and the command palette are unchanged.
for (const [key, view] of [
    ["project_project_kanban", projectProjectKanbanView],
    ["project_project_kanban_group_stage", projectProjectKanbanGroupStageView],
    ["project_project_list", projectProjectListView],
]) {
    class ContentSearchController extends view.Controller {
        static components = { ...view.Controller.components, SearchBar: ProjectsContentSearchBar };
    }
    registry.category("views").add(key, { ...view, Controller: ContentSearchController }, { force: true });
}
