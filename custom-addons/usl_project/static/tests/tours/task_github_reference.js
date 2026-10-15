import { registry } from "@web/core/registry";

const issueUrl = "https://github.com/example/demo/issues/42";
const pullUrl = "https://github.com/example/demo/pull/43";

registry.category("web_tour.tours").add("usl_task_github_reference_edit", {
    steps: () => [
        { trigger: ".o_form_view .o_field_widget[name='github_url'] input", run: `edit ${issueUrl}` },
        { trigger: "[property-name='qa_sources'] input", run: "click" },
        { trigger: ".o_form_button_save", run: "click" },
        { trigger: ".o_form_view:has(.o_form_status_indicator_buttons.invisible)" },
        { trigger: ".o_field_widget[name='github_url'] input", run: "click" },
        {
            trigger: ".o_field_widget[name='github_url'] a",
            run() {
                const link = document.querySelector(".o_field_widget[name='github_url'] a");
                if (link.href !== issueUrl || link.target !== "_blank") {
                    throw new Error("Native URL control must open the complete canonical reference");
                }
            },
        },
        { trigger: ".o_form_view", expectUnloadPage: true, run() { location.reload(); } },
        {
            trigger: ".o_field_widget[name='github_url'] input",
            run() {
                if (document.querySelector(".o_field_widget[name='github_url'] input").value !== issueUrl) {
                    throw new Error("Saved canonical reference lost after reload");
                }
            },
        },
    ],
});

registry.category("web_tour.tours").add("usl_task_github_reference_legacy", {
    steps: () => [
        {
            trigger: "[property-name='qa_sources'] input",
            run() {
                if (document.querySelector("[property-name='qa_github']")) {
                    throw new Error("Verified canonical duplicate must not compete with the editable URL");
                }
            },
        },
        { trigger: "[property-name='qa_sources'] input", run: "edit Updated synthetic notes" },
        { trigger: ".o_field_widget[name='github_url'] input", run: "click" },
        { trigger: ".o_form_button_save", run: "click" },
        { trigger: ".o_form_view:has(.o_form_status_indicator_buttons.invisible)" },
        { trigger: ".o_field_widget[name='github_url'] input", run: `edit ${pullUrl}` },
        { trigger: "[property-name='qa_sources'] input", run: "click" },
        {
            trigger: ".o_form_view:has([property-name='qa_github'])",
            run() { document.querySelector("[property-name='qa_github'] .o_field_property_label").scrollIntoView({ block: "center" }); },
        },
        {
            trigger: "[property-name='qa_github'] .o_field_property_label",
            run() {
                const legacy = document.querySelector("[property-name='qa_github']");
                if (legacy.querySelector("input") || !legacy.textContent.includes("Legacy GitHub") || !legacy.textContent.includes(issueUrl)) {
                    throw new Error("A different legacy value must remain visible readonly for comparison");
                }
            },
        },
        { trigger: ".o_form_button_save", run: "click" },
        { trigger: ".o_form_view:has(.o_form_status_indicator_buttons.invisible)" },
        { trigger: ".o_form_view", expectUnloadPage: true, run() { location.reload(); } },
        {
            trigger: "[property-name='qa_github'] .o_field_property_label",
            run() {
                if (document.querySelector("[property-name='qa_sources'] input").value !== "Updated synthetic notes") {
                    throw new Error("Editing another property must persist through reload");
                }
            },
        },
    ],
});
