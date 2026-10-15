import { beforeEach, expect, test } from "@odoo/hoot";
import { animationFrame, advanceTime, runAllTimers } from "@odoo/hoot-mock";
import { contains, defineActions, getService, mountWithCleanup, onRpc } from "@web/../tests/web_test_helpers";
import { defineProjectModels, ProjectTask } from "@project/../tests/project_models";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { WebClient } from "@web/webclient/webclient";
import { UslHome } from "../src/home/home";

defineProjectModels();
defineActions([{ id: 9001, xml_id: "usl_home.qa_home_action", type: "ir.actions.client", name: "Home", tag: "usl_home.Home", path: "usl-home" }]);

let searches;
beforeEach(() => {
    searches = [];
    ProjectTask._views = { form: '<form><field name="name"/></form>' };
    onRpc("usl.home.service", "get_configuration", () => ({ layout: { order: [], hidden: [] }, available_widgets: [], favorites: [], available_destinations: [], active_company: { id: 1, name: "QA Company" }, company_scope: { combined: false, label: "QA Company" } }));
    onRpc("usl.home.service", "search_records", ({ args }) => {
        searches.push(args[0]);
        return { groups: [{ key: "tasks", label: "Tasks", offset: 0, has_more: false, records: [{ model: "project.task", id: 1, title: "Regular task 1", excerpt: "Amber lantern specification" }] }] };
    });
    onRpc("usl.home.service", "search_documents", () => ({ group: { key: "documents", records: [] } }));
    onRpc("usl.home.service", "get_search_result_action", () => ({ type: "ir.actions.act_window", res_model: "project.task", res_id: 1, views: [[false, "form"]] }));
});

async function openResult() {
    await mountWithCleanup(WebClient);
    await getService("action").doAction(9001);
    await contains("#usl-home-content-search").edit("Amber lantern");
    await advanceTime(250);
    await animationFrame();
    await contains(".o_usl_search_result").click();
    await runAllTimers();
    expect(".o_form_view").toHaveCount(1);
    expect('.o_field_widget[name="name"] input').toHaveValue("Regular task 1");
}

async function browserBack() {
    browser.history.back();
    await animationFrame();
}

test("native browser Back uses the unique Home route and requests fresh content", async () => {
    const nativeHome = registry.category("actions").get("home");
    await openResult();
    const before = searches.length;
    await browserBack();
    expect("#usl-home-content-search").toHaveValue("Amber lantern");
    expect(searches.length).toBeGreaterThan(before);
    expect(browser.location.href).not.toInclude("Amber");
    expect(registry.category("actions").get("home")).toBe(nativeHome);
    expect(getService("action").currentController.Component).toBe(UslHome);
});

test("cleared query stays empty across native Forward and Back", async () => {
    await openResult();
    await browserBack();
    await contains('button[aria-label="Clear search"]').click();
    const before = searches.length;
    browser.history.forward();
    await animationFrame();
    expect(".o_form_view").toHaveCount(1);
    await browserBack();
    expect("#usl-home-content-search").toHaveValue("");
    expect(".o_usl_search_result").toHaveCount(0);
    expect(searches.length).toBe(before);
});
