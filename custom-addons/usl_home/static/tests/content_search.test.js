import { expect, test } from "@odoo/hoot";
import { Deferred, animationFrame, advanceTime } from "@odoo/hoot-mock";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { contains, mockService, mountWithCleanup, onRpc } from "@web/../tests/web_test_helpers";
import { queryAllTexts } from "@odoo/hoot-dom";
import { router, startRouter } from "@web/core/browser/router";
import { HomeContentSearch, highlightParts } from "../src/home/content_search";

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
    expect(".o_usl_result_title").toHaveText("Launch checklist");
    expect(".o_usl_result_excerpt").toHaveText("Amber lantern specification");
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
    expect("#usl-home-search-result-project-task-6").toHaveText(/Updated task 6\s+Amber/);
    expect("#usl-home-search-result-project-task-6").toHaveAttribute("aria-selected", "true");
});

test("task results remain available while other business categories are pending", async () => {
    const pending = new Deferred();
    onRpc("usl.home.service", "search_records", ({ kwargs }) =>
        kwargs.categories?.includes("tasks") ? local("Partial task result") : pending);
    onRpc("usl.home.service", "search_documents", () => documents);
    await mountSearch("Amber");
    await animationFrame();
    expect(".o_usl_search_result").toHaveText(/Partial task result\s+Amber lantern specification/);
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

test("chatter source names its author and date, marks the terms and opens in the web client", async () => {
    startRouter();
    const result = local("Prepare the Atelier Ambre visit");
    Object.assign(result.groups[0].records[0], {
        excerpt: "… bring the ivory Linen prototype ready for the visit …",
        source: {
            kind: "message", message_kind: "message", id: 41, field: "body", label: "Chatter message",
            author_name: "Camille Aurore", date: "2026-11-27 09:30:00",
        },
    });
    onRpc("usl.home.service", "search_records", () => result);
    onRpc("usl.home.service", "search_documents", () => documents);
    onRpc("usl.home.service", "get_search_result_action", ({ args }) => {
        expect(args).toEqual(["project.task", 5, 41, "body"]);
        return { type: "ir.actions.act_window", res_model: "project.task", res_id: 5, usl_highlight_message_id: 41 };
    });
    mockService("action", {
        doAction: (action) => expect.step(`${action.res_model}:${action.res_id}:${router.current.highlight_message_id}:${action.usl_highlight_message_id}`),
    });
    await mountSearch("prototype linen");
    await animationFrame();
    expect(".o_usl_result_source").toHaveText(/Camille Aurore · .*2026/);
    expect(".o_usl_result_excerpt").toHaveText("“… bring the ivory Linen prototype ready for the visit …”");
    expect(".o_usl_result_match").toHaveCount(2);
    expect(queryAllTexts(".o_usl_result_match")).toEqual(["Linen", "prototype"]);
    await contains("#usl-home-content-search").press("ArrowDown");
    await contains("#usl-home-content-search").press("Enter");
    expect.verifySteps(["project.task:5:41:undefined"]);
});

test("notes and subjects are labelled differently from messages", async () => {
    const result = local("Atelier Ambre visit");
    result.groups[0].records[0].source = { kind: "message", message_kind: "note", id: 41, field: "body", label: "Chatter message", author_name: "Camille Aurore", date: "" };
    onRpc("usl.home.service", "search_records", () => result);
    onRpc("usl.home.service", "search_documents", () => documents);
    await mountSearch("prototype");
    await animationFrame();
    expect(".o_usl_result_source").toHaveText("Note · Camille Aurore");
});

test("highlight marks accented and mixed-case words without producing markup", () => {
    expect(highlightParts("Préparer le sémaphore", "semaphore PREPARER")).toEqual([
        { text: "Préparer", match: true }, { text: " le ", match: false }, { text: "sémaphore", match: true },
    ]);
    expect(highlightParts("<b>x</b> amber", "amber")).toEqual([{ text: "<b>x</b> ", match: false }, { text: "amber", match: true }]);
    expect(highlightParts("nothing", "")).toEqual([{ text: "nothing", match: false }]);
});

test("source excerpts remain plain text", async () => {
    const result = local("Atelier Ambre visit");
    result.groups[0].records[0].excerpt = '<img src=x onerror="window.sourceLeak=true">';
    result.groups[0].records[0].source = { kind: "message", id: 41, field: "body", label: "Chatter message" };
    onRpc("usl.home.service", "search_records", () => result);
    onRpc("usl.home.service", "search_documents", () => documents);
    await mountSearch("prototype");
    await animationFrame();
    expect(".o_usl_search_result img").toHaveCount(0);
    expect(".o_usl_search_result").toHaveText(/<img src=x/);
});

test("withdrawn message source does not open and offers refresh guidance", async () => {
    const result = local("Atelier Ambre visit");
    result.groups[0].records[0].source = { kind: "message", id: 41, field: "body", label: "Chatter message" };
    onRpc("usl.home.service", "search_records", () => result);
    onRpc("usl.home.service", "search_documents", () => documents);
    mockService("orm", {
        call(model, method, ...args) {
            if (model === "usl.home.service" && method === "get_search_result_action") {
                return Promise.reject(new Error("Source withdrawn"));
            }
            return super.call(model, method, ...args);
        },
    });
    mockService("action", { doAction: () => { throw Error("Unexpected navigation"); } });
    mockService("notification", { add: (message) => expect.step(message) });
    await mountSearch("prototype");
    await animationFrame();
    await contains(".o_usl_search_result").click();
    expect.verifySteps(["This record could not be opened. Refresh the search and try again."]);
    expect(".o_usl_search_result").toHaveCount(1);
});

const meaningGroup = (...records) => ({ status: "ok", group: { key: "task_meaning", label: "Related by subject", records } });
const meaningTask = (id, title) => ({ model: "project.task", id, title, excerpt: `${title} description` });

test("meaning search starts with the keyword search, in parallel, for two words", async () => {
    const keywords = new Deferred();
    const meaning = new Deferred();
    onRpc("usl.home.service", "search_records", ({ kwargs }) => {
        expect.step(`keyword:${kwargs.categories?.join(",") || "others"}`);
        return kwargs.categories?.includes("tasks") ? keywords : { groups: [] };
    });
    onRpc("usl.home.service", "search_documents", () => documents);
    onRpc("project.task", "search_by_meaning", ({ args }) => {
        expect.step(`meaning:${args[0]}`);
        return meaning;
    });
    await mountSearch("fix sound");
    await animationFrame();
    // Both are in flight before either answered.
    expect.verifySteps(["keyword:tasks,projects", "keyword:others", "meaning:fix sound"]);
    keywords.resolve(local("Exact task"));
    await animationFrame();
    expect(".o_usl_search_result").toHaveCount(1);
    expect(".o_usl_search_skeleton").toHaveText(/Looking for related tasks/);
    meaning.resolve(meaningGroup(meaningTask(7, "Replace recording microphone")));
    await animationFrame();
    expect(".o_usl_search_skeleton").toHaveCount(0);
    expect(queryAllTexts(".o_usl_search_result .o_usl_result_title")).toEqual(["Exact task", "Replace recording microphone Similar topic"]);
});

test("related tasks are appended under a quiet subheader without moving the rows above", async () => {
    const meaning = new Deferred();
    onRpc("usl.home.service", "search_records", () => local("Exact task"));
    onRpc("usl.home.service", "search_documents", () => documents);
    onRpc("project.task", "search_by_meaning", () => meaning);
    await mountSearch("fix sound");
    await animationFrame();
    await contains("#usl-home-content-search").press("ArrowDown");
    expect("#usl-home-search-result-project-task-5").toHaveAttribute("aria-selected", "true");
    meaning.resolve(meaningGroup(meaningTask(5, "Exact task"), meaningTask(7, "Replace recording microphone"), meaningTask(8, "Studio audio check")));
    await animationFrame();
    // The duplicate is removed, the keyword row stays first and selected.
    expect(queryAllTexts('[data-category="tasks"] .o_usl_result_title')).toEqual([
        "Exact task", "Replace recording microphone Similar topic", "Studio audio check Similar topic",
    ]);
    expect(".o_usl_search_subgroup").toHaveText("Related by subject");
    expect(".o_usl_search_subgroup").toHaveCount(1);
    expect("#usl-home-search-result-project-task-5").toHaveAttribute("aria-selected", "true");
    expect('[data-category="tasks"] .o_usl_search_group_title').toHaveText(/^tasks$/i);
});

test("related rows fill the group to five and never fewer than two", async () => {
    const records = [1, 2, 3, 4, 5].map((id) => ({ model: "project.task", id, title: `Keyword ${id}`, excerpt: "" }));
    const few = records.slice(0, 2);
    let keywordRows = few;
    onRpc("usl.home.service", "search_records", () => ({ groups: [{ key: "tasks", label: "Tasks", offset: 0, has_more: false, records: keywordRows }] }));
    onRpc("usl.home.service", "search_documents", () => documents);
    onRpc("project.task", "search_by_meaning", () => meaningGroup(...[11, 12, 13, 14, 15].map((id) => meaningTask(id, `Meaning ${id}`))));
    await mountSearch("fix sound");
    await animationFrame();
    expect('[data-category="tasks"] .o_usl_search_result').toHaveCount(5);
    expect(queryAllTexts(".o_usl_result_badge")).toEqual(["Similar topic", "Similar topic", "Similar topic"]);
    keywordRows = records;
    await contains("#usl-home-content-search").edit("fix sound now");
    await advanceTime(250);
    await animationFrame();
    expect('[data-category="tasks"] .o_usl_search_result').toHaveCount(7);
    expect(queryAllTexts(".o_usl_result_badge")).toEqual(["Similar topic", "Similar topic"]);
});

test("without keyword tasks the group is about the subject", async () => {
    onRpc("usl.home.service", "search_records", () => ({ groups: [] }));
    onRpc("usl.home.service", "search_documents", () => documents);
    onRpc("project.task", "search_by_meaning", () => meaningGroup(meaningTask(7, "Replace recording microphone")));
    await mountSearch("the studio is silent");
    await animationFrame();
    expect('[data-category="tasks"] .o_usl_search_group_title').toHaveText(/^tasks about this$/i);
    expect(".o_usl_search_subgroup").toHaveCount(0);
    expect(".o_usl_search_result").toHaveCount(1);
    expect(".o_usl_search_empty").toHaveCount(0);
});

test("an unreachable model is silent and keeps the keyword rows", async () => {
    onRpc("usl.home.service", "search_records", () => local("Exact task"));
    onRpc("usl.home.service", "search_documents", () => documents);
    onRpc("project.task", "search_by_meaning", () => ({ status: "unavailable", group: { key: "task_meaning", records: [] } }));
    await mountSearch("fix sound");
    await animationFrame();
    expect(".o_usl_search_result").toHaveCount(1);
    expect(".o_usl_search_skeleton").toHaveCount(0);
    expect('[role="alert"]').toHaveCount(0);
    expect(".o_usl_content_results").not.toHaveText(/unavailable|Find tasks about this/);
});

test("a failing meaning call is silent too", async () => {
    onRpc("usl.home.service", "search_records", () => local("Exact task"));
    onRpc("usl.home.service", "search_documents", () => documents);
    mockService("orm", {
        call(model, method, ...args) {
            if (model === "project.task" && method === "search_by_meaning") {
                return Promise.reject(new Error("model down"));
            }
            return super.call(model, method, ...args);
        },
    });
    await mountSearch("fix sound");
    await animationFrame();
    expect(".o_usl_search_result").toHaveCount(1);
    expect(".o_usl_search_skeleton").toHaveCount(0);
    expect('[role="alert"]').toHaveCount(0);
});

test("a deployment without meaning search is asked once and one short word is never sent", async () => {
    onRpc("usl.home.service", "search_records", () => local("Exact task"));
    onRpc("usl.home.service", "search_documents", () => documents);
    onRpc("project.task", "search_by_meaning", ({ args }) => {
        expect.step(`meaning:${args[0]}`);
        return { status: "disabled", group: { key: "task_meaning", records: [] } };
    });
    await mountSearch("amber");
    await animationFrame();
    expect.verifySteps([]);
    await contains("#usl-home-content-search").edit("amber lantern");
    await advanceTime(250);
    await animationFrame();
    expect.verifySteps(["meaning:amber lantern"]);
    await contains("#usl-home-content-search").edit("another amber lantern");
    await advanceTime(250);
    await animationFrame();
    expect.verifySteps([]);
});

test("the empty state waits for every stream, then names the company", async () => {
    const meaning = new Deferred();
    onRpc("usl.home.service", "search_records", () => ({ groups: [] }));
    onRpc("usl.home.service", "search_documents", () => documents);
    onRpc("project.task", "search_by_meaning", () => meaning);
    await mountSearch("zzz qqq");
    await animationFrame();
    expect(".o_usl_search_empty").toHaveCount(0);
    meaning.resolve({ status: "empty", group: { key: "task_meaning", records: [] } });
    await animationFrame();
    expect(".o_usl_search_empty").toHaveText(/Nothing matches “zzz qqq” in .+\. Try fewer words or another spelling\./);
});
