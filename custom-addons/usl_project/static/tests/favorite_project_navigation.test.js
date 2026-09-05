import { beforeEach, expect, test } from "@odoo/hoot";
import { click } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

import { registry } from "@web/core/registry";
import {
    clickSave,
    contains,
    getService,
    mockService,
    mountView,
    onRpc,
    toggleActionMenu,
    toggleMenuItem,
} from "@web/../tests/web_test_helpers";
import {
    defineProjectModels,
    ProjectProject,
} from "@project/../tests/project_models";

defineProjectModels();

beforeEach(() => {
    ProjectProject._records = [
        {
            id: 1,
            name: "Customer rollout",
            allow_milestones: false,
            allow_task_dependencies: false,
            allow_recurring_tasks: false,
        },
    ];
    mockService("menu", {
        reload() {
            expect.step("menu_reload");
        },
    });
});

test("an autosaved favorite refreshes the Projects menu", async () => {
    onRpc("project.project", "web_save", () => expect.step("web_save"));
    await mountView({
        resModel: "project.project",
        type: "kanban",
        arch: `
            <kanban>
                <template>
                    <t t-name="card">
                        <field name="is_favorite" widget="project_is_favorite"/>
                        <field name="name"/>
                    </t>
                </template>
            </kanban>
        `,
    });

    await click("div[name=is_favorite] .o_favorite");
    await animationFrame();

    expect.verifySteps(["web_save", "menu_reload"]);
});

test("a readonly favorite does not save or refresh the menu", async () => {
    onRpc("project.project", "web_save", () => expect.step("web_save"));
    await mountView({
        resModel: "project.project",
        type: "kanban",
        arch: `
            <kanban>
                <template>
                    <t t-name="card">
                        <field name="is_favorite" widget="project_is_favorite" readonly="1"/>
                        <field name="name"/>
                    </t>
                </template>
            </kanban>
        `,
    });

    await click("div[name=is_favorite] .o_favorite");
    await animationFrame();

    expect.verifySteps([]);
});

test("a favorite changed in the project form refreshes the menu after save", async () => {
    onRpc("project.project", "check_features_enabled", () => ({
        allow_milestones: false,
        allow_task_dependencies: false,
        allow_recurring_tasks: false,
    }));
    onRpc("has_group", () => true);
    onRpc("project.project", "web_save", () => expect.step("web_save"));
    await mountView({
        resModel: "project.project",
        resId: 1,
        type: "form",
        arch: `
            <form js_class="project_project_form">
                <field name="name"/>
                <field name="is_favorite" widget="project_is_favorite" options="{'autosave': False}"/>
            </form>
        `,
    });

    await click("div[name=is_favorite] .o_favorite");
    await animationFrame();
    expect.verifySteps([]);
    await clickSave();

    expect.verifySteps(["web_save", "menu_reload"]);
});

test("renaming a project refreshes a possible favorite menu label", async () => {
    onRpc("project.project", "check_features_enabled", () => ({
        allow_milestones: false,
        allow_task_dependencies: false,
        allow_recurring_tasks: false,
    }));
    onRpc("has_group", () => true);
    onRpc("project.project", "web_save", () => expect.step("web_save"));
    await mountView({
        resModel: "project.project",
        resId: 1,
        type: "form",
        arch: `
            <form js_class="project_project_form">
                <field name="name"/>
                <field name="is_favorite"/>
            </form>
        `,
    });

    await contains("div[name=name] input").edit("Renamed customer rollout");
    await clickSave();

    expect.verifySteps(["web_save", "menu_reload"]);
});

test("archiving a project refreshes a favorite that must leave the menu", async () => {
    ProjectProject._records[0].active = true;
    onRpc("project.project", "check_features_enabled", () => ({
        allow_milestones: false,
        allow_task_dependencies: false,
        allow_recurring_tasks: false,
    }));
    onRpc("has_group", () => true);
    onRpc("project.project", "action_archive", () => expect.step("action_archive"));
    onRpc("project.project", "action_unarchive", () => expect.step("action_unarchive"));
    await mountView({
        resModel: "project.project",
        resId: 1,
        type: "form",
        actionMenus: {},
        arch: `
            <form js_class="project_project_form">
                <field name="name"/>
                <field name="active"/>
                <field name="is_favorite"/>
            </form>
        `,
    });

    await toggleActionMenu();
    await toggleMenuItem("Archive");
    await contains(".modal-footer .btn-primary").click();
    await toggleActionMenu();
    expect(`.o-dropdown--menu span:contains(Unarchive)`).toHaveCount(1);

    await toggleMenuItem("UnArchive");

    expect.verifySteps([
        "action_archive",
        "menu_reload",
        "action_unarchive",
        "menu_reload",
    ]);
});

test("converting a project to a template refreshes a favorite that must leave the menu", async () => {
    onRpc("project.project", "action_create_template_from_project", () =>
        expect.step("action_create_template_from_project")
    );
    await mountView({
        resModel: "project.project",
        type: "kanban",
        arch: `
            <kanban>
                <template>
                    <t t-name="card"><field name="name"/></t>
                </template>
            </kanban>
        `,
    });

    await getService("orm").call("project.project", "action_create_template_from_project", [[1]]);

    expect.verifySteps(["action_create_template_from_project", "menu_reload"]);
});

test("the favorite client action opens the exact project", async () => {
    const openedActions = [];
    const next = { type: "ir.actions.act_window_close" };
    const result = await registry.category("actions").get("project_top_menu_overview")(
        {
            services: {
                action: {
                    async doAction(action) {
                        openedActions.push(action);
                    },
                },
            },
        },
        { res_id: 73, next }
    );

    expect(openedActions).toEqual([
        {
            type: "ir.actions.act_window",
            res_model: "project.project",
            views: [[false, "form"]],
            res_id: 73,
        },
    ]);
    expect(result).toBe(next);
});
