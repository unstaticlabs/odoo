import { beforeEach, expect, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import {
    contains,
    mockService,
    mountWithCleanup,
    onRpc,
} from "@web/../tests/web_test_helpers";
import { UslHome } from "../src/home/home";

defineMailModels();

const configuration = {
    layout: {
        version: 1,
        order: ["activities", "my_tasks", "favorites", "ai_pipelines", "accounting"],
        hidden: [],
    },
    available_widgets: ["activities", "my_tasks", "favorites", "ai_pipelines", "accounting"],
    active_company: { id: 1, name: "Unstatic Labs" },
    company_scope: {
        mode: "single",
        combined: false,
        active_company: { id: 1, name: "Unstatic Labs" },
        companies: [{ id: 1, name: "Unstatic Labs" }],
        label: "Unstatic Labs",
    },
    favorites: [
        {
            id: 9,
            name: "My Tasks",
            available: true,
            kind: "provider",
            kind_label: "Project",
            icon: "tasks",
            company_name: false,
        },
        {
            id: 10,
            name: "A deliberately longer project destination",
            available: true,
            kind: "provider",
            kind_label: "Project",
            icon: "project",
            company_name: "Unstatic Labs",
        },
    ],
    available_destinations: [],
};

beforeEach(() => {
    onRpc("usl.home.service", "get_configuration", () => configuration);
    onRpc("usl.home.service", "get_activities", () => ({
        today: "2026-08-28",
        items: [
            {
                id: 1,
                summary: "Review launch readiness",
                activity_type: "To-Do",
                record_name: "Launch checklist",
                model_name: "Task",
                res_model: "project.task",
                res_id: 44,
                deadline: "2026-08-28",
                bucket: "today",
            },
            {
                id: 2,
                summary: "A much longer activity title that must not move the due-date column",
                activity_type: "To-Do",
                record_name: "Production cutover",
                model_name: "Task",
                res_model: "project.task",
                res_id: 45,
                deadline: "2026-08-27",
                bucket: "overdue",
            },
        ],
    }));
    onRpc("usl.home.service", "get_my_tasks", () => ({
        stages: [{ id: 1, name: "In Progress", count: 3 }],
        signals: { overdue: 1, due_soon: 2, waiting: 1, changes_requested: 0 },
    }));
    onRpc("usl.home.service", "get_ai_attention", () => ({ items: [] }));
    onRpc("usl.home.service", "get_accounting_alerts", () => ({
        scope: configuration.company_scope,
        company: { id: 1, name: "Unstatic Labs" },
        alerts: [{
            key: "bank",
            label: "Bank items to review",
            count: 2,
            status: "review",
            companies: [{ id: 1, name: "Unstatic Labs", count: 2 }],
        }],
    }));
    onRpc("usl.home.service", "save_layout", ({ args }) => args[0]);
});

test("renders the complete native Home hierarchy from independent providers", async () => {
    await mountWithCleanup(UslHome);
    await animationFrame();
    expect("main.o_usl_home").toHaveAttribute("aria-label", "Home");
    expect(".o_usl_home_header h1").toHaveCount(0);
    expect(".o_usl_home_widget").toHaveCount(5);
    expect(".o_usl_home_widget[data-widget='activities']").toHaveText(/Review launch readiness/);
    expect(".o_usl_home_widget[data-widget='my_tasks']").toHaveText(/In Progress/);
    expect(".o_usl_home_widget[data-widget='favorites']").toHaveText(/My Tasks/);
    expect(".o_usl_home_favorite_main").toHaveText(/Project/);
    expect(".o_usl_home_favorite_icon .fa-check-square-o").toHaveCount(1);
    expect(".o_usl_home_widget[data-widget='ai_pipelines']").toHaveText(/No AI pipeline work needs you/);
    expect(".o_usl_home_widget[data-widget='accounting']").toHaveText(/Bank items to review/);
});

test("multi-company mode labels combined widgets and accounting contributions", async () => {
    const companyScope = {
        mode: "multi",
        combined: true,
        active_company: { id: 1, name: "Unstatic Labs" },
        companies: [
            { id: 1, name: "Unstatic Labs" },
            { id: 2, name: "USL MEDIA" },
        ],
        label: "Combined across 2 selected companies",
    };
    onRpc("usl.home.service", "get_configuration", () => ({
        ...configuration,
        company_scope: companyScope,
    }));
    onRpc("usl.home.service", "get_accounting_alerts", () => ({
        scope: companyScope,
        company: false,
        alerts: [{
            key: "bank",
            label: "Bank items to review",
            count: 5,
            status: "review",
            companies: [
                { id: 1, name: "Unstatic Labs", count: 2 },
                { id: 2, name: "USL MEDIA", count: 3 },
            ],
        }],
    }));

    await mountWithCleanup(UslHome);
    await animationFrame();

    expect(".o_usl_home_scope").toHaveText(/Combined across 2 selected companies/);
    expect(".o_usl_home_widget[data-widget='my_tasks'] header").toHaveText(
        /Combined across 2 selected companies/
    );
    expect(".o_usl_home_widget[data-widget='accounting']").toHaveText(
        /Unstatic Labs: 2 · USL MEDIA: 3/
    );
});

test("every task metric opens its exact filtered action", async () => {
    const requests = [];
    const actions = [];
    const actionOptions = [];
    onRpc("usl.home.service", "get_my_tasks_action", ({ args }) => {
        requests.push(args);
        return {
            type: "ir.actions.act_window",
            name: `My Tasks — ${args[1]}`,
            res_model: "project.task",
            domain: [["user_ids", "in", 5]],
            usl_home_filter: {
                description: `${args[1]}`,
                domain: [[args[0], "=", args[1]]],
                is_default: true,
            },
        };
    });
    mockService("action", {
        doAction(action, options) {
            actions.push(action);
            actionOptions.push(options);
        },
    });

    await mountWithCleanup(UslHome);
    await animationFrame();

    expect(".o_usl_home_task_signals button").toHaveCount(4);
    expect(".o_usl_home_stage_list button").toHaveCount(1);
    expect(".o_usl_home_task_signals button[data-signal='overdue']").toHaveAttribute(
        "aria-label",
        "Open Overdue tasks (1)"
    );

    await contains(".o_usl_home_task_signals button[data-signal='overdue']").click();
    await contains(".o_usl_home_stage_list button").click();

    expect(requests).toEqual([
        ["signal", "overdue"],
        ["stage", 1],
    ]);
    expect(actions).toHaveLength(2);
    expect(actions[0].domain).toEqual([["user_ids", "in", 5]]);
    expect(actions[1].domain).toEqual([["user_ids", "in", 5]]);
    expect(actions[0].usl_home_filter).toBe(undefined);
    expect(actions[1].usl_home_filter).toBe(undefined);
    expect(actionOptions[0].props.dynamicFilters).toEqual([
        {
            description: "overdue",
            domain: [["signal", "=", "overdue"]],
            is_default: true,
        },
    ]);
    expect(actionOptions[1].props.dynamicFilters).toEqual([
        {
            description: "1",
            domain: [["stage", "=", 1]],
            is_default: true,
        },
    ]);
});

test("widget visibility is saved without affecting other cards", async () => {
    await mountWithCleanup(UslHome);
    await animationFrame();
    await contains("button", { text: "Customize" }).click();
    await contains("label", { text: "AI Pipelines" }).click();
    await animationFrame();
    expect(".o_usl_home_widget[data-widget='ai_pipelines']").toHaveCount(0);
    expect(".o_usl_home_widget[data-widget='activities']").toHaveCount(1);
});

test("dense destination rows keep icons, content, and status on shared tracks", async () => {
    await mountWithCleanup(UslHome);
    await animationFrame();

    const activityRows = [...document.querySelectorAll(".o_usl_home_attention_list button")];
    const favoriteRows = [...document.querySelectorAll(".o_usl_home_favorite_open")];
    expect(activityRows).toHaveLength(2);
    expect(favoriteRows).toHaveLength(2);
    expect(activityRows.every((row) => getComputedStyle(row).display === "grid")).toBe(true);
    expect(favoriteRows.every((row) => getComputedStyle(row).display === "grid")).toBe(true);

    const activityContentStarts = activityRows.map((row) =>
        Math.round(row.querySelector(".o_usl_home_item_main").getBoundingClientRect().left)
    );
    expect(new Set(activityContentStarts).size).toBe(1);

    for (const row of favoriteRows) {
        const iconRect = row.querySelector(".o_usl_home_favorite_icon").getBoundingClientRect();
        expect(Math.round(iconRect.width)).toBe(32);
        expect(Math.round(iconRect.height)).toBe(32);
    }
});
