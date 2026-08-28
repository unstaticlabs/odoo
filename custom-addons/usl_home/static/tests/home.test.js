import { beforeEach, expect, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { contains, mountWithCleanup, onRpc } from "@web/../tests/web_test_helpers";
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
    favorites: [{ id: 9, name: "My Tasks", available: true, kind: "provider", company_name: false }],
    available_destinations: [],
};

beforeEach(() => {
    onRpc("usl.home.service", "get_configuration", () => configuration);
    onRpc("usl.home.service", "get_activities", () => ({
        today: "2026-08-28",
        items: [{
            id: 1,
            summary: "Review launch readiness",
            activity_type: "To-Do",
            record_name: "Launch checklist",
            model_name: "Task",
            res_model: "project.task",
            res_id: 44,
            deadline: "2026-08-28",
            bucket: "today",
        }],
    }));
    onRpc("usl.home.service", "get_my_tasks", () => ({
        stages: [{ id: 1, name: "In Progress", count: 3 }],
        signals: { overdue: 1, due_soon: 2, waiting: 1, changes_requested: 0 },
    }));
    onRpc("usl.home.service", "get_ai_attention", () => ({ items: [] }));
    onRpc("usl.home.service", "get_accounting_alerts", () => ({
        company: { id: 1, name: "Unstatic Labs" },
        alerts: [{ key: "bank", label: "Bank items to review", count: 2, status: "review" }],
    }));
    onRpc("usl.home.service", "save_layout", ({ args }) => args[0]);
});

test("renders the complete native Home hierarchy from independent providers", async () => {
    await mountWithCleanup(UslHome);
    await animationFrame();
    expect(".o_usl_home_widget").toHaveCount(5);
    expect(".o_usl_home_widget[data-widget='activities']").toHaveText(/Review launch readiness/);
    expect(".o_usl_home_widget[data-widget='my_tasks']").toHaveText(/In Progress/);
    expect(".o_usl_home_widget[data-widget='favorites']").toHaveText(/My Tasks/);
    expect(".o_usl_home_widget[data-widget='ai_pipelines']").toHaveText(/No AI pipeline work needs you/);
    expect(".o_usl_home_widget[data-widget='accounting']").toHaveText(/Bank items to review/);
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
