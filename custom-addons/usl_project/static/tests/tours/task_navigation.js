import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("usl_task_navigation_milestone", {
    steps: () => [
        {
            trigger: ".o_kanban_record .o_usl_task_reference",
            run() {
                const references = [...document.querySelectorAll(".o_kanban_record .o_usl_task_reference")];
                if (references.length !== 2 || references.some((node) => !/^#\s*\d+$/.test(node.textContent.trim()))) {
                    throw new Error("Each saved task must show its identifier on the milestone board");
                }
                if (!document.title.includes("[QA Navigation] Launch readiness")) {
                    throw new Error("The browser title must identify the milestone");
                }
            },
        },
        { trigger: ".o_breadcrumb:contains('[QA Navigation] Launch readiness')" },
        { trigger: "button.o_switch_view.o_list", run: "click" },
        { trigger: ".o_list_view .o_data_row:contains('[QA Navigation] Overdue')" },
        { trigger: ".o_breadcrumb:contains('[QA Navigation] Launch readiness')" },
        { trigger: "button.o_switch_view.o_kanban", run: "click" },
        { trigger: ".o_kanban_record:contains('[QA Navigation] Due soon') .o_usl_task_reference" },
    ],
});
