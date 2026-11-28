import { expect, test } from "@odoo/hoot";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { contains, defineModels, fields, models, mountView, onRpc } from "@web/../tests/web_test_helpers";
import "../src/task_waiting/waiting";

class WaitingProject extends models.Model {
    _name = "waiting.project";
    name = fields.Char();
    _records = [{ id: 1, name: "Synthetic project" }];
}
class WaitingTask extends models.Model {
    _name = "project.task";
    name = fields.Char();
    project_id = fields.Many2one({ relation: "waiting.project" });
    state = fields.Selection({
        selection: [
            ["01_in_progress", "In Progress"],
            ["02_changes_requested", "Changes Requested"],
            ["03_approved", "Approved"],
            ["04_waiting_normal", "Waiting"],
            ["1_done", "Done"],
            ["1_canceled", "Cancelled"],
        ],
    });
    usl_waiting_reason = fields.Char();
    _records = [
        { id: 1, name: "Synthetic task", project_id: 1, state: "01_in_progress" },
        { id: 2, name: "Synthetic waiting task", project_id: 1, state: "04_waiting_normal", usl_waiting_reason: "Supplier quote" },
        { id: 3, name: "Synthetic dependency task", project_id: 1, state: "04_waiting_normal" },
    ];
}
defineMailModels();
defineModels([WaitingProject, WaitingTask]);

const formArch = `<form><sheet>
    <field name="name"/>
    <field name="state" widget="project_task_state_selection" options="{'usl_offer_waiting': true}"/>
    <field name="usl_waiting_reason" widget="usl_waiting_reason" invisible="state != '04_waiting_normal'" required="state == '04_waiting_normal'"/>
</sheet></form>`;

async function openStateMenu() {
    await contains(".o_field_widget[name=state] button.dropdown-toggle").click();
}

test("choosing Waiting shows the reason field, focuses it and saves nothing yet", async () => {
    onRpc("project.task", "web_save", () => {
        throw new Error("Choosing Waiting must not save before the reason is typed");
    });
    await mountView({ type: "form", resModel: "project.task", resId: 1, arch: formArch });
    expect(".o_field_widget[name=usl_waiting_reason]").toHaveCount(0);
    await openStateMenu();
    await contains(".o-dropdown--menu .dropdown-item:contains(Waiting)").click();
    expect(".o_field_widget[name=usl_waiting_reason] input").toHaveCount(1);
    expect(".o_field_widget[name=usl_waiting_reason] input").toBeFocused();
});

test("an unsaved Waiting choice can be undone with In Progress", async () => {
    await mountView({ type: "form", resModel: "project.task", resId: 1, arch: formArch });
    await openStateMenu();
    await contains(".o-dropdown--menu .dropdown-item:contains(Waiting)").click();
    await openStateMenu();
    expect(".o-dropdown--menu .dropdown-item:contains(In Progress)").toHaveCount(1);
    await contains(".o-dropdown--menu .dropdown-item:contains(In Progress)").click();
    expect(".o_field_widget[name=usl_waiting_reason]").toHaveCount(0);
});

test("a Waiting task without a reason cannot be saved", async () => {
    onRpc("project.task", "web_save", () => {
        throw new Error("A reasonless Waiting task must not be saved");
    });
    await mountView({ type: "form", resModel: "project.task", resId: 1, arch: formArch });
    await openStateMenu();
    await contains(".o-dropdown--menu .dropdown-item:contains(Waiting)").click();
    await contains(".o_form_button_save").click();
    expect(".o_field_widget[name=usl_waiting_reason]").toHaveClass("o_field_invalid");
});

test("In Progress is offered while a reason is set, and only then", async () => {
    await mountView({ type: "form", resModel: "project.task", resId: 2, arch: formArch });
    await openStateMenu();
    expect(".o-dropdown--menu .dropdown-item:contains(In Progress)").toHaveCount(1);
    expect(".o-dropdown--menu .dropdown-item:contains(Waiting)").toHaveCount(0);
});

test("a task waiting only on dependencies keeps the native menu", async () => {
    await mountView({ type: "form", resModel: "project.task", resId: 3, arch: formArch });
    await openStateMenu();
    expect(".o-dropdown--menu .dropdown-item:contains(In Progress)").toHaveCount(0);
});

test("Kanban and list menus never offer Waiting", async () => {
    await mountView({
        type: "list",
        resModel: "project.task",
        arch: `<list><field name="name"/><field name="state" widget="project_task_state_selection"/></list>`,
    });
    await contains(".o_data_row:eq(0) .o_field_widget[name=state] button").click();
    expect(".o-dropdown--menu .dropdown-item:contains(Waiting)").toHaveCount(0);
});

test("a form without the reason field never offers Waiting", async () => {
    await mountView({
        type: "form",
        resModel: "project.task",
        resId: 1,
        arch: `<form><sheet><field name="name"/><field name="state" widget="project_task_state_selection"/></sheet></form>`,
    });
    await openStateMenu();
    expect(".o-dropdown--menu .dropdown-item:contains(Waiting)").toHaveCount(0);
});

test("Waiting, a typed reason, then In Progress saves no reason", async () => {
    let saved;
    onRpc("project.task", "web_save", ({ args }) => {
        saved = args[1];
    });
    await mountView({ type: "form", resModel: "project.task", resId: 1, arch: formArch });
    await openStateMenu();
    await contains(".o-dropdown--menu .dropdown-item:contains(Waiting)").click();
    await contains(".o_field_widget[name=usl_waiting_reason] input").edit("Typed before resuming");
    await openStateMenu();
    await contains(".o-dropdown--menu .dropdown-item:contains(In Progress)").click();
    expect(saved.state).toBe("01_in_progress");
    expect(Boolean(saved.usl_waiting_reason)).toBe(false);
});
