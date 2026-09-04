import { beforeEach, expect, test } from "@odoo/hoot";
import { click } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

import { registry } from "@web/core/registry";
import {
    clickSave,
    contains,
    mockService,
    mountView,
    onRpc,
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
    onRpc("project.project", "web_save", () => expect.step("web_save"));
    await mountView({
        resModel: "project.project",
        resId: 1,
        type: "form",
        arch: `
            <form js_class="project_project_form">
                <field name="name"/>
                <field name="active"/>
                <field name="is_favorite"/>
            </form>
        `,
    });

    await contains("div[name=active] input").click();
    await clickSave();

    expect.verifySteps(["web_save", "menu_reload"]);
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
