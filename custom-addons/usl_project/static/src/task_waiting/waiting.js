import { Component, onWillStart, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { AutoComplete } from "@web/core/autocomplete/autocomplete";
import { SelectMenu } from "@web/core/select_menu/select_menu";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ProjectTaskStateSelection, projectTaskStateSelection } from "@project/components/project_task_state_selection/project_task_state_selection";

const WAITING = "04_waiting_normal";

export class WaitingDialog extends Component {
    static template = "usl_project.WaitingDialog";
    static components = { Dialog, AutoComplete, SelectMenu };
    static props = { close: Function, record: Object, apply: Function, entering: { type: Boolean, optional: true } };
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ text: "", model: "project.task", references: [], models: [], causes: [], lifted: [], busy: false, error: "" });
        onWillStart(async () => {
            const models = await this.orm.call("project.task", "get_waiting_reference_models", []);
            this.state.models = models.map(({ model, label }) => ({ value: model, label }));
            if (this.props.record.resId) {
                const response = await this.orm.call("project.task", "get_waiting_details", [[this.props.record.resId]]);
                this.state.causes = response.tasks[0].causes.filter((cause) => !cause.lifted && cause.is_blocking !== false);
            }
        });
    }
    get title() { return this.props.entering ? _t("Why is this task waiting?") : _t("Waiting causes"); }
    get sources() {
        return [{ options: async (query) => {
            const records = await this.orm.call("project.task", "search_waiting_references", [], { model: this.state.model, query });
            return records.map((record) => ({ label: record.display_name, onSelect: () => this.addReference(record) }));
        } }];
    }
    addReference(record) {
        const existing = this.state.causes.some((cause) => !this.state.lifted.includes(cause.id)
            && cause.reference?.model === record.model && cause.reference.id === record.id);
        if (!existing && !this.state.references.some((item) => item.model === record.model && item.id === record.id)) {
            this.state.references.push(record);
        }
    }
    selectModel(value) { this.state.model = value || "project.task"; }
    removeReference(index) { this.state.references.splice(index, 1); }
    lift(cause) { if (cause.id && !this.state.lifted.includes(cause.id)) { this.state.lifted.push(cause.id); } }
    async open(cause) {
        if (!cause.reference || this.state.busy) { return; }
        // Existing native dependencies may not have a separate owned cause.
        // Fresh native record authority is checked again by get_formview_action.
        const action = cause.id
            ? await this.orm.call("project.task", "action_open_waiting_reference", [[this.props.record.resId]], { cause_id: cause.id })
            : await this.orm.call(cause.reference.model, "get_formview_action", [[cause.reference.id]]);
        this.props.close();
        await this.action.doAction(action);
    }
    get canApply() {
        return !this.state.error && Boolean(this.state.text.trim() || this.state.references.length || this.state.lifted.length);
    }
    async save() {
        if (this.state.busy || !this.canApply) { return; }
        this.state.busy = true;
        this.state.error = "";
        const commands = this.state.lifted.map((id) => [1, id, { lifted: true }]);
        if (this.state.text.trim()) { commands.push([0, 0, { kind: "external", reason: this.state.text.trim() }]); }
        for (const { model, id } of this.state.references) { commands.push([0, 0, { kind: "reference", target_model: model, target_id: id }]); }
        try {
            await this.props.apply(commands, Boolean(this.state.text.trim() || this.state.references.length));
            this.props.close();
        } catch {
            this.state.error = _t("Not saved. Check the task’s unsaved changes and refresh before trying again; linked records may no longer be available.");
        } finally { this.state.busy = false; }
    }
}

async function updateCauses(record, commands, entering, orm, notification) {
    const values = { usl_waiting_reason_ids: commands, ...(entering ? { state: WAITING } : {}) };
    if (record.activeFields.usl_waiting_reason_ids?.related?.fields?.kind) {
        // A form keeps its pending edits and causes in one native save.
        const saved = await record.update(values, { save: true });
        if (saved === false) { throw new Error("Native Task save did not complete."); }
        return;
    }
    // Native object-button behavior for cards/lists without a relational
    // editing subview: save any unrelated pending edit first, then perform
    // exactly one ordinary Task.write for the Waiting action.
    if (!record.resId || record.dirty) {
        if (await record.save() === false) { throw new Error("Native Task save did not complete."); }
    }
    await orm.write("project.task", [record.resId], values);
    try {
        await record.load();
    } catch {
        // The write already succeeded. Do not misreport a failed refresh as a
        // failed mutation or invite replay after access changes.
        notification.add(_t("The task was updated, but its details could not be refreshed. Reopen the task to check its current access."), { type: "warning" });
    }
}

export class ExplainedTaskState extends ProjectTaskStateSelection {
    static template = "usl_project.ExplainedTaskState";
    setup() { super.setup(); this.dialog = useService("dialog"); this.orm = useService("orm"); this.notification = useService("notification"); }
    get options() {
        const options = super.options;
        if (this.props.record.data[this.props.name] === WAITING && this.props.record.data.usl_waiting_details?.has_open_causes === false) {
            options.unshift(this.props.record.fields[this.props.name].selection.find(([value]) => value === "01_in_progress"));
        }
        if (this.props.record.data[this.props.name] !== WAITING) { options.splice(Math.max(0, options.length - 2), 0, [WAITING, _t("Waiting")]); }
        return options;
    }
    get isToggleMode() { return this.props.viewType === "form" ? false : super.isToggleMode; }
    async updateRecord(value) {
        if (value !== WAITING) { return super.updateRecord(value); }
        this.dialog.add(WaitingDialog, { record: this.props.record, entering: true,
            apply: (commands, entering) => updateCauses(this.props.record, commands, entering, this.orm, this.notification) });
    }
}
registry.category("fields").add("project_task_state_selection", {
    ...projectTaskStateSelection, component: ExplainedTaskState,
    additionalClasses: [...(projectTaskStateSelection.additionalClasses || []), "o_usl_task_state"],
    fieldDependencies: [...projectTaskStateSelection.fieldDependencies, { name: "usl_waiting_details", type: "json" }],
}, { force: true });

export class WaitingCauses extends Component {
    static template = "usl_project.WaitingCauses";
    static props = { record: Object, compact: { type: Boolean, optional: true }, "*": true };
    setup() { this.dialog = useService("dialog"); this.orm = useService("orm"); this.notification = useService("notification"); }
    get details() { return this.props.record.data.usl_waiting_details; }
    get causes() { return this.details?.causes.filter((cause) => !cause.lifted && cause.is_blocking !== false) || []; }
    get visibleCauses() { return this.props.compact ? this.causes.slice(0, 2) : this.causes; }
    manage() {
        this.dialog.add(WaitingDialog, { record: this.props.record,
            apply: (commands, entering) => updateCauses(this.props.record, commands, entering, this.orm, this.notification) });
    }
}
registry.category("view_widgets").add("usl_waiting_causes", {
    component: WaitingCauses,
    fieldDependencies: [{ name: "usl_waiting_details", type: "json" }],
    extractProps: ({ attrs }) => ({ compact: attrs.compact === "1" }),
});
