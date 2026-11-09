import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("usl_activity_parent_task_link", {
    steps: () => [
        { trigger: ".o_form_view:has(.o_field_widget[name='summary'])" },
        { trigger: ".o_form_view button[name='action_open_document']", run: "click" },
        { trigger: ".o_form_view .o_field_widget[name='project_id']" },
        { trigger: ".o_control_panel .o_breadcrumb:contains('[QA Activity] Parent task')" },
    ],
});

registry.category("web_tour.tours").add("usl_activity_edit_dialog_from_task", {
    steps: () => [
        { trigger: ".o-mail-Activity:contains('[QA Activity] Call the printer') .o-mail-Activity-info", run: "click" },
        { trigger: ".o-mail-Activity-edit", run: "click" },
        { trigger: ".modal .o_form_view .o_field_widget[name='summary']" },
        {
            trigger: ".modal .o_form_view",
            run() {
                if (document.querySelector(".modal button[name='action_open_document']")) {
                    throw new Error("The chatter edit dialog already sits on the task; it must not offer a link back to it");
                }
            },
        },
    ],
});
