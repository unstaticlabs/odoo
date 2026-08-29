import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";

function positiveInteger(value) {
    const normalized = Number(value);
    return Number.isSafeInteger(normalized) && normalized > 0 ? normalized : false;
}

export function feedbackPageContext(actionController, viewport = browser.visualViewport) {
    const props = actionController?.props || {};
    const action = actionController?.action || {};
    const modelName = typeof props.resModel === "string" ? props.resModel : false;
    return {
        default_source_action_id: positiveInteger(action.id || props.actionId),
        default_source_model_name: modelName,
        default_source_record_id: positiveInteger(props.resId),
        default_viewport_width: positiveInteger(
            Math.round(viewport?.width || browser.innerWidth)
        ),
        default_viewport_height: positiveInteger(
            Math.round(viewport?.height || browser.innerHeight)
        ),
    };
}

export function sendFeedbackItem(env) {
    return {
        type: "item",
        id: "usl_send_feedback",
        description: _t("Send feedback"),
        hide: !user.isInternalUser,
        callback: () =>
            env.services.action.doAction("usl_feedback.action_feedback_submission", {
                additionalContext: feedbackPageContext(
                    env.services.action.currentController
                ),
            }),
        sequence: 15,
    };
}

export function myFeedbackItem(env) {
    return {
        type: "item",
        id: "usl_my_feedback",
        description: _t("My feedback"),
        hide: !user.isInternalUser,
        callback: () => env.services.action.doAction("usl_feedback.action_my_feedback"),
        sequence: 16,
    };
}

registry
    .category("user_menuitems")
    .add("usl_send_feedback", sendFeedbackItem)
    .add("usl_my_feedback", myFeedbackItem);

