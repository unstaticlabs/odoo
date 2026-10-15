import { expect, test } from "@odoo/hoot";
import { advanceTime, animationFrame, Deferred } from "@odoo/hoot-mock";
import { contains, mountView, onRpc, mockService, getService } from "@web/../tests/web_test_helpers";
import { defineProjectModels } from "@project/../tests/project_models";

defineProjectModels();

const view = {
    resModel: "project.project", type: "list", actionMenus: {},
    arch: '<list js_class="project_project_list"><field name="name"/></list>',
    searchViewArch: '<search><field name="name" string="Project"/></search>',
};
async function mountProjects() {
    await mountView(view);
    if (getService("ui").isSmall) {
        await contains(".o_control_panel button:has(.fa-search)").click();
    }
}

const direct = { groups: [{ key: "tasks", label: "Tasks", records: [{ model: "project.task", id: 5, title: "Launch checklist", excerpt: "Indigo bridge instructions" }] }] };

test("native Projects input offers direct task matches beside ordinary project facets", async () => {
    onRpc("usl.home.service", "search_records", ({ args, kwargs }) => {
        expect(args).toEqual(["Indigo bridge"]);
        expect(kwargs.categories).toHaveLength(1);
        expect(["tasks", "projects"]).toInclude(kwargs.categories[0]);
        return direct;
    });
    await mountProjects();
    await contains(".o_searchview_input").edit("Indigo bridge");
    await advanceTime(250);
    await animationFrame();
    expect('[id="usl-content-project.task-5"]').toHaveText("Tasks: Launch checklist\nIndigo bridge instructions");
    expect('.o_searchview_autocomplete').toHaveText(/Search.*Project.*Indigo bridge/s);
});

test("clicking a direct suggestion opens its authorized native record", async () => {
    onRpc("usl.home.service", "search_records", () => direct);
    onRpc("usl.home.service", "get_search_result_action", ({ args }) => {
        expect(args).toEqual(["project.task", 5]);
        return { type: "ir.actions.act_window", res_model: "project.task", res_id: 5 };
    });
    mockService("action", { doAction: (action) => expect.step(`${action.res_model}:${action.res_id}`) });
    await mountProjects();
    await contains(".o_searchview_input").edit("Indigo bridge");
    await advanceTime(250);
    await animationFrame();
    await contains('[id="usl-content-project.task-5"]').click();
    expect.verifySteps(["project.task:5"]);
});

test("clearing native Projects input cannot restore an older direct response", async () => {
    const pending = new Deferred();
    onRpc("usl.home.service", "search_records", () => pending);
    await mountProjects();
    await contains(".o_searchview_input").edit("Indigo bridge");
    await advanceTime(250);
    await contains(".o_searchview_input").edit("");
    await advanceTime(20);
    pending.resolve(direct);
    await animationFrame();
    expect('[id="usl-content-project.task-5"]').toHaveCount(0);
});

test("direct task results appear while project content lookup is still pending", async () => {
    const pending = new Deferred();
    onRpc("usl.home.service", "search_records", ({ kwargs }) =>
        kwargs.categories.includes("projects") ? pending : direct);
    await mountProjects();
    await contains(".o_searchview_input").edit("Amber partial result");
    await advanceTime(250);
    await animationFrame();
    expect('[id="usl-content-project.task-5"]').toHaveCount(1);
    pending.resolve({ groups: [] });
    await animationFrame();
});
