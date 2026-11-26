import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { usePopover } from "@web/core/popover/popover_hook";
import { registry } from "@web/core/registry";
import { ProjectTaskKanbanHeader } from "@project/views/project_task_kanban/project_task_kanban_header";
import { groupValue } from "../task_swimlanes/task_swimlane_matrix";

const reasonLabels = {
    reopened: _t("Task reopened"),
    unblocked: _t("Last blocker resolved"),
    subtasks_closed: _t("Subtasks closed"),
    overdue: _t("Deadline overdue"),
    due_today: _t("Due today"),
    due_soon: _t("Deadline approaching"),
    start_reached: _t("Scheduled start reached"),
    status_changed: _t("Status changed"),
    project_stage_changed: _t("Project stage changed"),
};

function activatePopoverButton(event) {
    if (event.key === "Enter" || event.key === " ") {
        // The Kanban handles Space globally for card selection.
        event.preventDefault();
        event.stopPropagation();
        if (!event.repeat) {
            event.currentTarget.click();
        }
    }
}

const personalAxes = new Set(["personal_stage_type_id", "personal_stage_type_ids"]);

export function taskBoard(groupBy, context) {
    if (groupBy.some((field) => personalAxes.has(field))) {
        return { personal: true };
    }
    if (context.default_project_id && groupBy.includes("stage_id")) {
        return { projectId: context.default_project_id };
    }
    return null;
}

class ReactivationHelp extends Component {
    static template = "usl_project.ReactivationHelp";
    static props = { help: Object, close: Function };

    get summary() {
        const { kind, names, personal } = this.props.help;
        const quoted = names.map((name) => _t('“%s”', name)).join(", ");
        if (kind === "later") {
            return _t("An open, unarchived task returns to %s in any of the following cases.", quoted);
        }
        return personal
            ? _t("Tasks assigned to you start here on your personal board. Tasks in %s return here when a reminder is triggered.", quoted)
            : _t("New tasks start here when no stage is specified. Tasks in %s return here when a reminder is triggered.", quoted);
    }
}

export class ReactivationKanbanHeader extends ProjectTaskKanbanHeader {
    static template = "usl_project.ReactivationKanbanHeader";

    setup() {
        super.setup();
        this.reactivationPopover = usePopover(ReactivationHelp, { position: "bottom" });
    }

    get reactivationHelp() {
        const { list, group } = this.props;
        const board = taskBoard(list.groupBy, list.context);
        const field = group.groupByField.name;
        if (!board || (board.personal ? !personalAxes.has(field) : field !== "stage_id")) {
            return null;
        }
        return list.model.reactivationColumns?.get(groupValue(group));
    }

    get reactivationHelpLabel() {
        return _t("About %s", this.props.group.displayName);
    }

    activateFromKeyboard = activatePopoverButton;

    openReactivationHelp(event) {
        this.reactivationPopover.open(event.currentTarget, { help: this.reactivationHelp });
    }
}

class ReturnReasons extends Component {
    static template = "usl_project.ReturnReasons";
    static props = { reasons: Array, close: Function };
}

class ReturnBadge extends Component {
    static template = "usl_project.ReturnBadge";
    static props = { record: Object, "*": true };

    setup() {
        this.reasonsPopover = usePopover(ReturnReasons, { position: "bottom-start" });
    }

    get reasons() {
        const { model, data, context } = this.props.record;
        if (model.useSampleModel) {
            return [];
        }
        const board = taskBoard(model.root?.groupBy || [], context);
        const reasons = board?.personal ? data.usl_personal_return_badge
            : (board?.projectId ? data.usl_project_return_badge : false);
        return Array.isArray(reasons) ? reasons.map((reason) => reasonLabels[reason]).filter(Boolean) : [];
    }

    get reasonsLabel() {
        return _t("Reasons for return");
    }

    activateFromKeyboard = activatePopoverButton;

    openReasons(event) {
        this.reasonsPopover.open(event.currentTarget, { reasons: this.reasons });
    }
}

registry.category("view_widgets").add("usl_task_return_badge", {
    component: ReturnBadge,
    fieldDependencies: [
        { name: "usl_project_return_badge", type: "json" },
        { name: "usl_personal_return_badge", type: "json" },
    ],
});
