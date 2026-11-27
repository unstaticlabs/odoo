import { expect, test } from "@odoo/hoot";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { contains, defineModels, fields, models, mountView, onRpc } from "@web/../tests/web_test_helpers";
import "../src/task_waiting/waiting";

class WaitingProject extends models.Model {
    _name = "waiting.project";
    name = fields.Char();
    _records = [{ id: 1, name: "Synthetic project" }];
}
class WaitingCause extends models.Model {
    _name = "project.task.waiting.reason";
    task_id = fields.Many2one({ relation: "project.task" });
    kind = fields.Selection({ selection: [["external", "External cause"], ["reference", "Blocking record"]] });
    reason = fields.Text();
    target_model = fields.Char({ readonly: true });
    target_id = fields.Many2oneReference({ model_field: "target_model", readonly: true });
    lifted = fields.Boolean();
}
class WaitingTask extends models.Model {
    _name = "project.task";
    name = fields.Char();
    project_id = fields.Many2one({ relation: "waiting.project" });
    state = fields.Selection({ selection: [["01_in_progress", "In Progress"], ["02_changes_requested", "Changes Requested"],
        ["03_approved", "Approved"], ["04_waiting_normal", "Waiting"], ["1_done", "Done"], ["1_canceled", "Cancelled"]] });
    usl_waiting_details = fields.Json();
    usl_waiting_reason_ids = fields.One2many({ relation: "project.task.waiting.reason", relation_field: "task_id" });
    _records = [{ id: 1, name: "Synthetic task", project_id: 1, state: "01_in_progress", usl_waiting_reason_ids: [] },
        { id: 7, name: "Synthetic checklist", project_id: 1, state: "01_in_progress", usl_waiting_reason_ids: [] }];
}
defineMailModels();
defineModels([WaitingProject, WaitingCause, WaitingTask]);

const relation = `<field name="usl_waiting_reason_ids" invisible="1"><list><field name="kind"/><field name="reason"/>
    <field name="target_model" force_save="1"/><field name="target_id" force_save="1"/><field name="lifted"/></list></field>`;
async function mountTask() {
    onRpc("project.task", "get_waiting_reference_models", () => [{ model: "project.task", label: "Task" }]);
    onRpc("project.task", "get_waiting_details", () => ({ schema: "usl-task-waiting/v1", tasks: [{ id: 1, causes: [] }] }));
    onRpc("project.task", "search_waiting_references", () => [{ model: "project.task", id: 7, display_name: "Synthetic checklist" }]);
    await mountView({ type: "form", resModel: "project.task", resId: 1,
        arch: `<form><sheet><field name="name"/><field name="project_id"/>
            <field name="state" widget="project_task_state_selection"/>
            <widget name="usl_waiting_causes"/>${relation}</sheet></form>` });
}
async function chooseWaiting() {
    await contains(".o_field_widget[name=state] button.dropdown-toggle").click();
    await contains(".o-dropdown--menu .dropdown-item:contains(Waiting)").click();
}

test("Waiting is available on a personal task; Cancel never saves", async () => {
    WaitingTask._records[0].project_id = false;
    onRpc("project.task", "web_save", () => { throw new Error("Cancel must not save"); });
    await mountTask();
    await chooseWaiting();
    expect(".modal-footer .btn-primary").toHaveProperty("disabled", true);
    await contains("#usl_waiting_external").edit("Synthetic external answer");
    await contains(".modal-footer .btn-secondary").click();
    expect(".modal").toHaveCount(0);
    await contains(".o_field_widget[name=state] button.dropdown-toggle").click();
    expect(".o-dropdown--menu .dropdown-item[aria-checked=true]").toHaveText("In Progress");
});

test("external cause and other pending form edits use one native save", async () => {
    onRpc("project.task", "web_save", ({ args }) => {
        expect.step("one native save");
        expect(args[1].name).toBe("Updated synthetic task");
        expect(args[1].state).toBe("04_waiting_normal");
        expect(args[1].usl_waiting_reason_ids).toHaveLength(1);
        expect(args[1].usl_waiting_reason_ids[0][2].kind).toBe("external");
        expect(args[1].usl_waiting_reason_ids[0][2].reason).toBe("Awaiting a synthetic answer");
    });
    await mountTask();
    await contains(".o_field_widget[name=name] input").edit("Updated synthetic task");
    await chooseWaiting();
    await contains("#usl_waiting_external").edit("Awaiting a synthetic answer");
    await contains(".modal-footer .btn-primary").click();
    expect(".modal").toHaveCount(0);
    expect.verifySteps(["one native save"]);
});

test("native readonly reference fields survive the relational save", async () => {
    onRpc("project.task", "web_save", ({ args }) => {
        expect.step("reference saved");
        const values = args[1].usl_waiting_reason_ids[0][2];
        expect(values.kind).toBe("reference");
        expect(values.target_model).toBe("project.task");
        expect(values.target_id).toBe(7);
    });
    await mountTask();
    await chooseWaiting();
    await contains(".modal .o-autocomplete input").edit("checklist", { confirm: false });
    await contains(".o-autocomplete--dropdown-item:contains(Synthetic checklist)").click();
    expect(".modal-body").toHaveText(/Synthetic checklist/);
    await contains(".modal-footer .btn-primary").click();
    expect(".modal").toHaveCount(0);
    expect.verifySteps(["reference saved"]);
});

test("unavailable references have no invented link or label and reader cannot change them", async () => {
    WaitingTask._records[0].state = "04_waiting_normal";
    WaitingTask._records[0].usl_waiting_details = { state: "04_waiting_normal", can_change: false,
        causes: [{ id: 17, kind: "unavailable", lifted: false, is_blocking: null, message: "The linked record is no longer accessible." }] };
    await mountTask();
    expect(".o_usl_waiting_causes").toHaveText(/The linked record is no longer accessible/);
    expect(".o_usl_waiting_causes a").toHaveCount(0);
    expect(".o_usl_waiting_causes button").toHaveCount(0);
});

test("completed tasks do not display an active Waiting summary", async () => {
    WaitingTask._records[0].state = "1_done";
    WaitingTask._records[0].usl_waiting_details = { state: "1_done", can_change: true,
        causes: [{ id: 17, kind: "external", lifted: false, is_blocking: true, reason: "Previous synthetic cause" }] };
    await mountTask();
    expect(".o_usl_waiting_causes").toHaveCount(0);
});


test("a historical Waiting task without an unresolved cause can resume normally", async () => {
    WaitingTask._records[0].state = "04_waiting_normal";
    WaitingTask._records[0].usl_waiting_details = { state: "04_waiting_normal", has_open_causes: false,
        legacy_unexplained: true, can_change: true, causes: [] };
    onRpc("project.task", "web_save", ({ args }) => {
        expect.step("native resume");
        expect(args[1].state).toBe("01_in_progress");
    });
    await mountTask();
    await contains(".o_field_widget[name=state] button.dropdown-toggle").click();
    await contains(".o-dropdown--menu .dropdown-item:contains(In Progress)").click();
    await contains(".o_field_widget[name=state] button.dropdown-toggle").click();
    expect(".o-dropdown--menu .dropdown-item[aria-checked=true]").toHaveText("In Progress");
    expect.verifySteps(["native resume"]);
});

test("a Kanban cause action needs no editing subview and uses one native write", async () => {
    onRpc("project.task", "get_waiting_reference_models", () => [{ model: "project.task", label: "Task" }]);
    onRpc("project.task", "get_waiting_details", () => ({ tasks: [{ id: 1, causes: [] }] }));
    onRpc("project.task", "write", ({ args }) => {
        expect.step("one native write");
        expect(args[1].state).toBe("04_waiting_normal");
        expect(args[1].usl_waiting_reason_ids[0][2].reason).toBe("Waiting for an external answer");
    });
    await mountView({ type: "kanban", resModel: "project.task", domain: [["id", "=", 1]],
        arch: `<kanban><templates><t t-name="card"><field name="name"/>
            <field name="state" widget="project_task_state_selection"/>
            <widget name="usl_waiting_causes" compact="1"/></t></templates></kanban>` });
    await contains(".o_field_widget[name=state] button.dropdown-toggle").click();
    await contains(".o-dropdown--menu .dropdown-item:contains(Waiting)").click();
    await contains("#usl_waiting_external").edit("Waiting for an external answer");
    await contains(".modal-footer .btn-primary").click();
    expect(".modal").toHaveCount(0);
    expect.verifySteps(["one native write"]);
});
