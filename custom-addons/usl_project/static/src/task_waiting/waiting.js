import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import {
    ProjectTaskStateSelection,
    projectTaskStateSelection,
} from "@project/components/project_task_state_selection/project_task_state_selection";

const WAITING = "04_waiting_normal";
const IN_PROGRESS = "01_in_progress";

/**
 * The native status menu cannot choose Waiting, and offers no way out of it.
 * Add Waiting (form only, where the reason can be typed) and, while a reason
 * is set, In Progress, which the server turns into "clear the reason".
 */
export class WaitingTaskState extends ProjectTaskStateSelection {
    get options() {
        const options = super.options;
        const data = this.props.record.data;
        if (data[this.props.name] === WAITING) {
            if (data.usl_waiting_reason) {
                options.unshift([IN_PROGRESS, _t("In Progress")]);
            }
        } else if (this.props.viewType === "form" && !this.props.readonly) {
            options.splice(Math.max(0, options.length - 2), 0, [WAITING, _t("Waiting")]);
        }
        return options;
    }

    async updateRecord(value) {
        if (value !== WAITING || this.props.viewType !== "form") {
            return super.updateRecord(value);
        }
        // Do not save a Waiting task that has no reason yet: show the reason
        // field, focus it, and let the normal form save record both together.
        await this.props.record.update({ [this.props.name]: value });
        await new Promise((resolve) => requestAnimationFrame(resolve));
        document
            .querySelector(".o_form_view .o_field_widget[name='usl_waiting_reason'] input")
            ?.focus();
    }
}

registry.category("fields").add(
    "project_task_state_selection",
    {
        ...projectTaskStateSelection,
        component: WaitingTaskState,
        fieldDependencies: [
            ...projectTaskStateSelection.fieldDependencies,
            { name: "usl_waiting_reason", type: "char" },
        ],
    },
    { force: true },
);
