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
