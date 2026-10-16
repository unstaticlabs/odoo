import { expect, test } from "@odoo/hoot";
import { Deferred, animationFrame, advanceTime } from "@odoo/hoot-mock";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { contains, mockService, mountWithCleanup, onRpc } from "@web/../tests/web_test_helpers";
import { HomeContentSearch } from "../src/home/content_search";

defineMailModels();

const local = (title) => ({ groups: [{ key: "tasks", label: "Tasks", offset: 0, has_more: false, records: [{ model: "project.task", id: 5, title, excerpt: "Amber lantern specification" }] }] });
const documents = { group: { key: "documents", label: "Documents", offset: 0, has_more: false, records: [] } };

async function mountSearch(query = "") {
    return mountWithCleanup(HomeContentSearch, { props: { query, revision: 0 } });
}

test("local results remain usable while document contents are pending", async () => {
    const pending = new Deferred();
    onRpc("usl.home.service", "search_records", () => local("Launch checklist"));
    onRpc("usl.home.service", "search_documents", () => pending);
    await mountSearch("Amber");
    await animationFrame();
    expect(".o_usl_search_result").toHaveText("Launch checklist\nAmber lantern specification");
    expect(".o_usl_content_results").toHaveText(/Searching document contents/);
    pending.resolve(documents);
    await animationFrame();
    expect(".o_usl_content_results").not.toHaveText(/Searching document contents/);
});

test("stale replies cannot restore results after a query is cleared", async () => {
    const pending = new Deferred();
    onRpc("usl.home.service", "search_records", () => pending);
    onRpc("usl.home.service", "search_documents", () => documents);
    await mountSearch("Amber");
    await contains('button[aria-label="Clear search"]').click();
    pending.resolve(local("Stale checklist"));
    await animationFrame();
    expect(".o_usl_search_result").toHaveCount(0);
    expect("#usl-home-content-search").toHaveValue("");
});

test("typing shows real content and keyboard Enter opens its native destination", async () => {
    onRpc("usl.home.service", "search_records", () => local("Launch checklist"));
    onRpc("usl.home.service", "search_documents", () => documents);
    onRpc("usl.home.service", "get_search_result_action", ({ args }) => {
        expect(args).toEqual(["project.task", 5]);
        return { type: "ir.actions.act_window", res_model: "project.task", res_id: 5 };
    });
    mockService("action", { doAction: (action) => expect.step(`${action.res_model}:${action.res_id}`) });
    await mountSearch();
    await contains("#usl-home-content-search").edit("Amber");
    await advanceTime(250);
    await animationFrame();
    await contains("#usl-home-content-search").press("ArrowDown");
    expect(".o_usl_search_result").toHaveAttribute("aria-selected", "true");
    await contains("#usl-home-content-search").press("Enter");
    expect.verifySteps(["project.task:5"]);
});

test("document outage has an independent retry without removing local results", async () => {
    onRpc("usl.home.service", "search_records", () => local("Launch checklist"));
    let failed = true;
    onRpc("usl.home.service", "search_documents", () => failed ? { group: { ...documents.group, unavailable: true } } : documents);
    await mountSearch("Amber");
    await animationFrame();
    expect(".o_usl_search_result").toHaveCount(1);
    expect('[role="alert"]').toHaveText(/Document contents are temporarily unavailable/);
    failed = false;
    await contains('[role="alert"] button').click();
    expect('[role="alert"]').toHaveCount(0);
    expect(".o_usl_search_result").toHaveCount(1);
});

test("selection stays on the same record when another result stream arrives", async () => {
    const pending = new Deferred();
    onRpc("usl.home.service", "search_records", () => pending);
    onRpc("usl.home.service", "search_documents", () => ({ group: { ...documents.group, records: [{ model: "usl.document", id: 5, title: "OCR specification", excerpt: "Amber lantern OCR text" }] } }));
    onRpc("usl.home.service", "get_search_result_action", ({ args }) => {
        expect(args).toEqual(["usl.document", 5]);
        return { type: "ir.actions.act_window", res_model: args[0], res_id: args[1] };
    });
    mockService("action", { doAction: (action) => expect.step(`${action.res_model}:${action.res_id}`) });
    await mountSearch("Amber");
    await animationFrame();
    await contains("#usl-home-content-search").press("ArrowDown");
    expect('[data-category="documents"] .o_usl_search_result').toHaveAttribute("aria-selected", "true");
    pending.resolve(local("Launch checklist"));
    await animationFrame();
    expect('[data-category="documents"] .o_usl_search_result').toHaveAttribute("aria-selected", "true");
    await contains("#usl-home-content-search").press("Enter");
    expect.verifySteps(["usl.document:5"]);
});

test("live page overlaps keep one current row per record and preserve selection", async () => {
    const records = [10, 9, 8, 7, 6].map((id) => ({ model: "project.task", id, title: `Task ${id}`, excerpt: "Amber" }));
    onRpc("usl.home.service", "search_records", ({ kwargs }) => ({ groups: [{ key: "tasks", label: "Tasks", offset: kwargs.offset || 0,
        has_more: !kwargs.offset, records: kwargs.offset
            ? [6, 5, 4, 3, 2].map((id) => ({ model: "project.task", id, title: `Updated task ${id}`, excerpt: "Amber" })) : records }] }));
    onRpc("usl.home.service", "search_documents", () => documents);
    await mountSearch("Amber");
    await animationFrame();
    await contains("#usl-home-content-search").press("ArrowUp");
    await contains(".o_usl_search_group > button.btn-link").click();
    expect(".o_usl_search_result").toHaveCount(9);
    expect("#usl-home-search-result-project-task-6").toHaveText("Updated task 6\nAmber");
    expect("#usl-home-search-result-project-task-6").toHaveAttribute("aria-selected", "true");
});

test("task results remain available while other business categories are pending", async () => {
    const pending = new Deferred();
    onRpc("usl.home.service", "search_records", ({ kwargs }) =>
        kwargs.categories?.includes("tasks") ? local("Partial task result") : pending);
    onRpc("usl.home.service", "search_documents", () => documents);
    await mountSearch("Amber");
    await animationFrame();
    expect(".o_usl_search_result").toHaveText("Partial task result\nAmber lantern specification");
    expect('[role="status"]').toHaveText(/Searching records/);
    pending.resolve({ groups: [] });
    await animationFrame();
    expect(".o_usl_search_result").toHaveCount(1);
    expect('[role="status"]').toHaveCount(0);
});

test("changing and clearing a query aborts superseded native search requests", async () => {
    let started = 0;
    let aborted = 0;
    mockService("orm", {
        call(model, method, ...args) {
            if (model !== "usl.home.service" || !["search_records", "search_documents"].includes(method)) {
                return super.call(model, method, ...args);
            }
            started++;
            let reject;
            const request = new Promise((_resolve, rejectRequest) => { reject = rejectRequest; });
            request.abort = () => {
                aborted++;
                reject(new Error("Synthetic superseded request"));
            };
            return request;
        },
    });
    await mountSearch("Amber");
    expect(started).toBeGreaterThan(0);
    const firstBatch = started;
    await contains("#usl-home-content-search").edit("Cobalt");
    expect(aborted).toBe(firstBatch);
    await advanceTime(250);
    expect(started).toBe(firstBatch * 2);
    await contains('button[aria-label="Clear search"]').click();
    expect(aborted).toBe(firstBatch * 2);
    expect(".o_usl_search_result").toHaveCount(0);
    expect('[role="alert"]').toHaveCount(0);
});
