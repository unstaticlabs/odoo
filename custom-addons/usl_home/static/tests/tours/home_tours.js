import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("usl_home_core_journey", {
    steps: () => [
        {
            content: "The native Odoo launcher remains available",
            trigger: ".o_main_navbar .o_navbar_apps_menu",
        },
        {
            content: "Home runs inside the native action manager",
            trigger: ".o_action_manager .o_usl_home",
        },
        {
            content: "Home presents the attention hierarchy",
            trigger: ".o_usl_home_widget[data-widget='activities']",
        },
        {
            content: "Home has one favorite-destination surface",
            trigger: ".o_usl_home_widget[data-widget='favorites']",
        },
        {
            content: "Personalization is discoverable",
            trigger: ".o_usl_home_header button:contains('Customize')",
            run: "click",
        },
        {
            content: "Widget visibility and ordering controls are available",
            trigger: ".o_usl_home_customize:has(input[type='checkbox']):has(.fa-arrow-up)",
        },
    ],
});


registry.category("web_tour.tours").add("usl_home_task_metrics_saved_search", {
    steps: () => [
        { trigger: ".o_usl_home_task_signals button[data-signal='overdue']", run: "click" },
        { trigger: ".o_searchview_facet:contains('Overdue')" },
        {
            trigger: ".o_kanban_record:contains('[QA Navigation] Overdue')",
            run() {
                const cards = [...document.querySelectorAll(".o_kanban_record")];
                if (cards.length !== 1 || cards[0].textContent.includes("Due soon")) {
                    throw new Error("Home Overdue must show its counted population despite a default saved search");
                }
            },
        },
        { trigger: ".o_kanban_view", expectUnloadPage: true, run() { window.history.back(); } },
        { trigger: ".o_usl_home_task_signals button[data-signal='due']", run: "click" },
        { trigger: ".o_searchview_facet:contains('Due in 7 days')" },
        {
            trigger: ".o_kanban_record:contains('[QA Navigation] Due soon')",
            run() {
                const cards = [...document.querySelectorAll(".o_kanban_record")];
                if (cards.length !== 1 || cards[0].textContent.includes("Overdue")) {
                    throw new Error("Repeated metric navigation must use the newly requested task population");
                }
            },
        },
        { trigger: ".o_kanban_view", expectUnloadPage: true, run() { window.history.back(); } },
        { trigger: ".o_usl_home_task_signals button[data-signal='waiting']", run: "click" },
        { trigger: ".o_searchview_facet:contains('Waiting')" },
        { trigger: ".o_view_nocontent" },
    ],
});
