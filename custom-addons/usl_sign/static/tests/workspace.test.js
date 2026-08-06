import {afterEach, beforeEach, expect, test} from "@odoo/hoot";
import {tick} from "@odoo/hoot-mock";
import {defineMailModels} from "@mail/../tests/mail_test_helpers";
import {
    contains,
    mockService,
    mountWithCleanup,
    onRpc,
} from "@web/../tests/web_test_helpers";
import {translatedTerms, translationLoaded} from "@web/core/l10n/translation";

import {SignLanding, SignLibrary} from "../src/js/workspace.esm";

defineMailModels();

const EMPTY_SECTIONS = {
    sign_now: {count: 0, items: []},
    decide: {count: 0, items: []},
    prepare: {count: 0, items: []},
    issues: {count: 0, items: []},
    waiting: {count: 0, items: []},
    completed: {count: 0, items: []},
};

let translationsWereLoaded;

beforeEach(() => {
    translationsWereLoaded = translatedTerms[translationLoaded];
    translatedTerms[translationLoaded] = true;
});

afterEach(() => {
    translatedTerms[translationLoaded] = translationsWereLoaded;
});

test("landing renders the six journeys and routes only the selected action", async () => {
    const actions = [];
    mockService("action", {
        async doAction(action, options) {
            actions.push({action, options});
        },
    });
    onRpc("usl.sign.workspace", "get_landing", () => ({
        can_start: true,
        sections: {
            ...EMPTY_SECTIONS,
            sign_now: {
                count: 1,
                items: [
                    {
                        id: 17,
                        model: "sign.oca.request.signer",
                        title: "Routine Agreement",
                        subtitle: "Sent by Valentin",
                        trust: "Standard",
                        status: "To sign",
                        progress: "0 of 1",
                        due: "2026-08-12",
                        next_step: "Review and sign",
                        action: {
                            type: "record",
                            model: "sign.oca.request.signer",
                            id: 17,
                        },
                    },
                ],
            },
        },
    }));

    await mountWithCleanup(SignLanding);

    expect(".usl_sign_work_card").toHaveCount(6);
    expect("section:nth-child(1) .usl_sign_work_card").toHaveText(/Sign now/);
    expect("section:nth-child(2) .usl_sign_work_card").toHaveText(/Decide/);
    expect("section:nth-child(3) .usl_sign_work_card").toHaveText(/Prepare and send/);
    expect("section:nth-child(4) .usl_sign_work_card").toHaveText(/Resolve issues/);
    expect("section:nth-child(5) .usl_sign_work_card").toHaveText(/Waiting on others/);
    expect("section:nth-child(6) .usl_sign_work_card").toHaveText(/Recently completed/);
    expect(".list-group-item-action").toHaveText(/Routine Agreement/);
    expect(".list-group-item-action").toHaveText(/Next:\s*Review and sign/);

    await contains("header .btn-primary").click();
    await contains(".list-group-item-action").click();

    expect(actions).toEqual([
        {action: "usl_sign.sign_start_action", options: undefined},
        {
            action: {
                type: "ir.actions.act_window",
                res_model: "sign.oca.request.signer",
                res_id: 17,
                views: [[false, "form"]],
                target: "current",
            },
            options: undefined,
        },
    ]);
});

test("landing failure is explicit and retry restores the workspace", async () => {
    let attempts = 0;
    onRpc("usl.sign.workspace", "get_landing", () => {
        attempts++;
        if (attempts === 1) {
            throw new Error("Synthetic workspace outage");
        }
        return {can_start: false, sections: EMPTY_SECTIONS};
    });

    await mountWithCleanup(SignLanding);
    expect(".alert-danger").toHaveText(/could not be loaded/);

    await contains(".alert-danger button").click();
    await tick();

    expect(attempts).toBe(2);
    expect(".alert-danger").toHaveCount(0);
    expect(".usl_sign_work_card").toHaveCount(6);
    expect("header .btn-primary").toHaveCount(0);
});

test("Library defaults to templates and keeps draft and ready actions distinct", async () => {
    const calls = [];
    const actions = [];
    onRpc("usl.sign.workspace", "get_library", ({kwargs}) => {
        const {section, search, offset, limit} = kwargs;
        calls.push({section, search, offset, limit});
        return {
            total: 2,
            items: [
                {
                    id: 4,
                    title: "Routine Agreement",
                    description: "Reusable routine agreement",
                    category: "Commercial",
                    company: "USL",
                    version: 3,
                    owner: "Valentin",
                    trust: "Standard",
                    status: "Ready",
                    usage: 8,
                    ready: true,
                },
                {
                    id: 5,
                    title: "Employment Amendment",
                    description: "",
                    category: "People",
                    company: "USL",
                    version: 1,
                    owner: "Valentin",
                    trust: "Strong personal",
                    status: "Draft",
                    usage: 0,
                    ready: false,
                },
            ],
        };
    });
    mockService("action", {
        async doAction(action, options) {
            actions.push({action, options});
        },
    });

    await mountWithCleanup(SignLibrary);

    expect(".nav-link.active").toHaveText("Templates");
    expect(".usl_sign_library_card").toHaveCount(2);
    expect("article:nth-child(1) .usl_sign_library_card .btn-primary").toHaveText(
        "Use template"
    );
    expect("article:nth-child(2) .usl_sign_library_card .btn-primary").toHaveText(
        "Finish template"
    );

    await contains(".usl_sign_library_card:first-child .btn-primary").click();
    await contains("article:nth-child(2) .usl_sign_library_card .btn-primary").click();

    expect(actions[0]).toEqual({
        action: "sign_oca.sign_oca_template_generate_act_window",
        options: {
            additionalContext: {
                active_id: 4,
                active_model: "sign.oca.template",
                default_template_id: 4,
            },
        },
    });
    expect(actions[1].action).toEqual({
        type: "ir.actions.act_window",
        res_model: "sign.oca.template",
        res_id: 5,
        views: [[false, "form"]],
        target: "current",
    });
    expect(calls[0]).toEqual({section: "templates", search: "", offset: 0, limit: 24});
});

test("Library search, completed retrieval, and pagination use bounded queries", async () => {
    const calls = [];
    onRpc("usl.sign.workspace", "get_library", ({kwargs}) => {
        const {section, search, offset, limit} = kwargs;
        calls.push({section, search, offset, limit});
        if (kwargs.section === "completed") {
            return {
                total: 30,
                items: [
                    {
                        id: 31,
                        title: "Completed Routine Agreement",
                        record: "Project / Catalogue",
                        completed: "2026-08-06",
                        signers: "Roger, Prosper",
                        trust: "Standard",
                        proof: "Complete",
                        archive: "Archived",
                        final_url: "/sign/request/31/final",
                        certificate_url: "/sign/request/31/certificate",
                        dossier_url: "/sign/request/31/dossier",
                    },
                ],
            };
        }
        return {total: 0, items: []};
    });

    await mountWithCleanup(SignLibrary);
    await contains("input[type=search]").edit("routine");
    await contains(".nav-link:nth-child(2)").click();

    expect("tbody tr").toHaveCount(1);
    expect("tbody tr").toHaveText(/Completed Routine Agreement/);
    expect("a[aria-label='Download final PDF']").toHaveAttribute(
        "href",
        "/sign/request/31/final"
    );
    expect("a[aria-label='Download certificate']").toHaveCount(1);
    expect("a[aria-label='Download evidence dossier']").toHaveCount(1);
    expect("footer").toHaveText(/1–24 of 30/);

    await contains("footer button:last-child").click();

    expect(calls).toEqual([
        {section: "templates", search: "", offset: 0, limit: 24},
        {section: "templates", search: "routine", offset: 0, limit: 24},
        {section: "completed", search: "routine", offset: 0, limit: 24},
        {section: "completed", search: "routine", offset: 24, limit: 24},
    ]);
});
