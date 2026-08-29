import { expect, test } from "@odoo/hoot";
import { patchWithCleanup } from "@web/../tests/web_test_helpers";

import { browser } from "@web/core/browser/browser";
import { user } from "@web/core/user";
import {
    feedbackPageContext,
    myFeedbackItem,
    sendFeedbackItem,
} from "../src/js/feedback_user_menu";

test("desktop feedback action sends only typed page context", async () => {
    patchWithCleanup(user, { isInternalUser: true });
    patchWithCleanup(browser, {
        visualViewport: { width: 1439.6, height: 899.5 },
    });
    const calls = [];
    const env = {
        services: {
            action: {
                currentController: {
                    action: { id: 42 },
                    props: {
                        actionId: 99,
                        resModel: "project.task",
                        resId: 17,
                    },
                },
                doAction(action, options) {
                    calls.push({ action, options });
                },
            },
        },
    };

    const item = sendFeedbackItem(env);
    expect(item.hide).toBe(false);
    expect(item.description).not.toBe(undefined);
    await item.callback();
    expect(calls).toEqual([
        {
            action: "usl_feedback.action_feedback_submission",
            options: {
                additionalContext: {
                    default_source_action_id: 42,
                    default_source_model_name: "project.task",
                    default_source_record_id: 17,
                    default_viewport_width: 1440,
                    default_viewport_height: 900,
                },
            },
        },
    ]);
    expect(JSON.stringify(calls)).not.toMatch(/location|query|token|fragment|hash/i);
});

test.tags("mobile");
test("mobile and empty screens keep safe context defaults", () => {
    patchWithCleanup(browser, {
        visualViewport: { width: 390, height: 844 },
    });
    expect(feedbackPageContext(undefined)).toEqual({
        default_source_action_id: false,
        default_source_model_name: false,
        default_source_record_id: false,
        default_viewport_width: 390,
        default_viewport_height: 844,
    });
});

test("My feedback uses the bounded reporter action", async () => {
    patchWithCleanup(user, { isInternalUser: true });
    const calls = [];
    const item = myFeedbackItem({
        services: { action: { doAction: (action) => calls.push(action) } },
    });
    await item.callback();
    expect(item.description).not.toBe(undefined);
    expect(calls).toEqual(["usl_feedback.action_my_feedback"]);
});
