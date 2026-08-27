import {afterEach, beforeEach, expect, test} from "@odoo/hoot";
import {tick} from "@odoo/hoot-mock";
import {defineMailModels} from "@mail/../tests/mail_test_helpers";
import {contains, mockService, mountWithCleanup, onRpc} from "@web/../tests/web_test_helpers";
import {translatedTerms, translationLoaded} from "@web/core/l10n/translation";

import {SignLanding} from "../src/js/workspace.esm";

defineMailModels();

const EMPTY_SECTIONS = {
    sign_now: {count: 0, items: []},
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

test("landing renders the five document journeys and routes only the selected action", async () => {
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
                        signers: [
                            {
                                id: 18,
                                name: "Roger Example",
                                label: "Invited",
                                tone: "ready",
                                icon: "fa-envelope",
                            },
                        ],
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

    expect(".usl_sign_workspace.o_action").toHaveCount(1);
    expect(".usl_sign_workspace > .o_content.overflow-auto").toHaveCount(1);
    expect(".usl_sign_workspace.o_action_manager").toHaveCount(0);
    expect(".usl_sign_work_card").toHaveCount(5);
    expect("section:nth-child(1) .usl_sign_work_card").toHaveText(/Sign now/);
    expect("section:nth-child(2) .usl_sign_work_card").toHaveText(/Prepare and send/);
    expect("section:nth-child(3) .usl_sign_work_card").toHaveText(/Needs attention/);
    expect("section:nth-child(4) .usl_sign_work_card").toHaveText(/Waiting on others/);
    expect("section:nth-child(5) .usl_sign_work_card").toHaveText(/Recently completed/);
    expect(".list-group-item-action").toHaveText(/Routine Agreement/);
    expect(".list-group-item-action").toHaveText(/Next:\s*Review and sign/);
    expect(".usl_sign_signer_chip--ready").toHaveText(/Roger Example\s*Invited/);
    expect(".usl_sign_signer_chip--ready .fa-envelope").toHaveCount(1);

    await contains("header .btn-outline-primary").click();
    await contains("header .btn-primary").click();
    await contains(".list-group-item-action").click();

    expect(actions).toEqual([
        {action: "usl_sign.signature_inspector_action", options: undefined},
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
    expect(".usl_sign_work_card").toHaveCount(5);
    expect("header .btn-primary").toHaveCount(0);
});
