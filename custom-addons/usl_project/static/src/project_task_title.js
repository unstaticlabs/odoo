import { ProjectTaskControlPanel } from "@project/views/project_task_control_panel/project_task_control_panel";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { onWillStart } from "@odoo/owl";

export async function setProjectTaskDisplayName({ context, config, orm }) {
    const projectId = Number(context.default_project_id || 0);
    if (!Number.isInteger(projectId) || projectId <= 0) {
        return;
    }
    try {
        const [project] = await orm.read("project.project", [projectId], ["display_name"]);
        if (project?.display_name) {
            config.setDisplayName(project.display_name);
        }
    } catch {
        // Keep the native action title when the project disappeared or is no longer readable.
    }
}

patch(ProjectTaskControlPanel.prototype, {
    setup() {
        super.setup(...arguments);
        const orm = useService("orm");
        onWillStart(() =>
            setProjectTaskDisplayName({
                context: this.env.searchModel.globalContext,
                config: this.env.config,
                orm,
            })
        );
    },
});
