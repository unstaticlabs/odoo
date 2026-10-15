import { ProjectTaskControlPanel } from "@project/views/project_task_control_panel/project_task_control_panel";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { onWillStart } from "@odoo/owl";

export async function setProjectTaskDisplayName({ context, config, orm }) {
    const milestoneId = Number(context.default_milestone_id || 0);
    const projectId = Number(context.default_project_id || 0);
    const model = milestoneId ? "project.milestone" : "project.project";
    const recordId = milestoneId || projectId;
    if (!Number.isInteger(recordId) || recordId <= 0) {
        return;
    }
    try {
        const [record] = await orm.read(model, [recordId], ["display_name"]);
        if (record?.display_name) {
            config.setDisplayName(record.display_name);
        }
    } catch {
        // Keep the native title when the milestone or project is no longer readable.
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
