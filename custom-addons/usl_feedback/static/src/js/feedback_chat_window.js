import { ChatHub } from "@mail/core/common/chat_hub";
import { _t } from "@web/core/l10n/translation";
import { router } from "@web/core/browser/router";
import { browser } from "@web/core/browser/browser";
import { getFirstAndLastTabableElements, useActiveElement } from "@web/core/ui/ui_service";
import { localization } from "@web/core/l10n/localization";
import { getActiveHotkey } from "@web/core/hotkeys/hotkey_service";
import { registry } from "@web/core/registry";
import { useAutofocus, useBackButton, useBus, useService } from "@web/core/utils/hooks";
import { isEventHandled } from "@web/core/utils/misc";
import { patch } from "@web/core/utils/patch";
import { useSubEnv } from "@web/owl2/utils";

import { Component, reactive, useState } from "@odoo/owl";

import { FeedbackPanel, feedbackPageContext } from "./feedback_messaging_menu";
import { captureFeedbackPagePreview } from "./feedback_page_preview";


// Keep the native palette; feedback is the first suggested command.
registry.category("command_categories").add("usl_feedback", {}, { sequence: 5 });

const FOCUS_EVENT = "usl-feedback-chat-window:focus";

export class FeedbackChatWindowService {
    constructor(env, services) {
        this.env = env;
        this.store = services["mail.store"];
        this.action = services.action;
        this.mode = "closed";
        this.pageContext = false;
        this.screenshot = false;
        this.captureState = "idle";
        this.captureId = 0;
        this.navigation = [];
    }

    get isClosed() {
        return this.mode === "closed";
    }

    open(payload) {
        if (this.isClosed && payload) {
            this.pageContext = payload.pageContext;
            this.screenshot = payload.screenshot;
            this.captureState = payload.screenshot ? "ready" : payload.captureState || "idle";
        }
        this.reserveNativeWindowSpace();
        this.mode = "open";
        browser.setTimeout(() => this.env.bus.trigger(FOCUS_EVENT), 0);
    }

    recordNavigation(state = router.current) {
        // Session-local technical identifiers only: never URLs, search terms,
        // record titles or arbitrary action context.
        const controller = this.action?.currentController;
        const action = Number(controller?.action?.id || state?.action);
        const candidateModel = controller?.props?.resModel || state?.model;
        const model = typeof candidateModel === "string" && /^[a-z][a-z0-9_.]{0,127}$/.test(candidateModel)
            ? candidateModel : false;
        const entry = {
            action_id: Number.isSafeInteger(action) && action > 0 ? action : false,
            model,
        };
        if (!(entry.action_id || entry.model)) {
            return;
        }
        if (JSON.stringify(this.navigation.at(-1)) !== JSON.stringify(entry)) {
            this.navigation.push(entry);
            this.navigation = this.navigation.slice(-5);
        }
    }

    openFeedback() {
        if (!this.isClosed) {
            this.open();
            return;
        }
        this.captureCurrentPage();
    }

    captureCurrentPage() {
        this.recordNavigation();
        const pageContext = {
            ...feedbackPageContext(this.action.currentController),
            navigation: this.navigation.map((entry) => ({ ...entry })),
        };
        const captureId = this.beginCapture(pageContext);
        browser.requestAnimationFrame(() => {
            browser.setTimeout(async () => {
                try {
                    this.completeCapture(captureId, await captureFeedbackPagePreview());
                } catch {
                    this.failCapture(captureId);
                }
            }, 0);
        });
        return pageContext;
    }

    fold() {
        this.mode = "folded";
    }

    beginCapture(pageContext) {
        this.clearScreenshot();
        this.captureId += 1;
        this.pageContext = pageContext;
        this.captureState = "preparing";
        this.open();
        return this.captureId;
    }

    completeCapture(captureId, screenshot) {
        if (captureId !== this.captureId || this.isClosed) {
            screenshot.release();
            return false;
        }
        this.screenshot = screenshot;
        this.captureState = "ready";
        return true;
    }

    failCapture(captureId) {
        if (captureId !== this.captureId || this.isClosed) {
            return false;
        }
        this.captureState = "error";
        return true;
    }

    clearScreenshot() {
        this.captureId += 1;
        this.screenshot?.release?.();
        this.screenshot = false;
        this.captureState = "idle";
    }

    cancelCapture() {
        this.captureId += 1;
        this.clearScreenshot();
    }

    close() {
        this.cancelCapture();
        this.mode = "closed";
        this.pageContext = false;
    }

    reserveNativeWindowSpace() {
        const chatHub = this.store.chatHub;
        while (chatHub.opened.length >= chatHub.maxOpened) {
            chatHub.opened.at(-1)?.fold();
        }
    }
}

export const feedbackChatWindowService = {
    dependencies: ["mail.store", "action", "command"],
    start(env, services) {
        const service = reactive(new FeedbackChatWindowService(env, services));
        service.recordNavigation();
        env.bus.addEventListener("ACTION_MANAGER:UI-UPDATED", () => service.recordNavigation());
        services.command.add(_t("Send product feedback"), () => service.openFeedback(), {
            category: "usl_feedback",
            global: true,
        });
        browser.addEventListener("pagehide", () => service.cancelCapture());
        browser.addEventListener("popstate", () => service.cancelCapture());
        return service;
    },
};
registry.category("services").add("usl_feedback.chat_window", feedbackChatWindowService);

export class FeedbackChatWindow extends Component {
    static components = { FeedbackPanel };
    static props = ["hidden?", "right?"];
    static template = "usl_feedback.FeedbackChatWindow";

    setup() {
        super.setup();
        useSubEnv({ inChatWindow: true });
        this.feedbackChatWindow = useService("usl_feedback.chat_window");
        this.ui = useService("ui");
        this.focusState = useState({ isActive: false });
        useBus(this.ui.bus, "active-element-changed", ({ detail }) => {
            this.focusState.isActive = detail === this.rootRef.el;
        });
        useActiveElement("root");
        this.rootRef = useAutofocus({ refName: "root", mobile: true });
        useBackButton(() => this.close());
        useBus(this.env.bus, FOCUS_EVENT, () => {
            const root = this.rootRef.el;
            if (root && this.ui.activeElement !== root) {
                // A folded window stays mounted beneath dialogs opened later.
                // Move its existing native activation to the top without duplicates.
                this.restoreReactivatedFocus = true;
                this.focusToRestore = this.ui.activeElement?.contains(document.activeElement)
                    && !document.activeElement.closest(".o_command_palette")
                    ? document.activeElement : undefined;
                this.ui.deactivateElement(root);
                this.ui.activateElement(root);
            }
            (root?.querySelector("#usl_feedback_message") || root)?.focus();
        });
    }

    get attClass() {
        return {
            "o-usl-FeedbackChatWindow-active": this.focusState.isActive,
            "d-none": this.props.hidden,
            "w-100 h-100 o-mobile": this.ui.isSmall,
            "o-rounded-bubble border border-dark o-border-opacity-15 mb-2": !this.ui.isSmall,
        };
    }

    get style() {
        const offsetFrom = localization.direction === "rtl" ? "left" : "right";
        const oppositeFrom = offsetFrom === "right" ? "left" : "right";
        const visibleOffset = this.ui.isSmall ? 0 : this.props.right;
        return `${offsetFrom}: ${visibleOffset}px; ${oppositeFrom}: auto;`;
    }

    onKeydown(ev) {
        if (ev.target.closest(".o-dropdown") || ev.target.closest(".o-dropdown--menu")) {
            return;
        }
        ev.stopPropagation();
        if (
            getActiveHotkey(ev) === "escape" &&
            !isEventHandled(ev, "NavigableList.close") &&
            !isEventHandled(ev, "Composer.discard")
        ) {
            this.close();
        }
    }

    restoreNativeFocus() {
        this.ui.deactivateElement(this.rootRef.el);
        const container = this.ui.activeElement;
        let focusTarget = this.focusToRestore;
        if (!focusTarget?.isConnected || !container?.contains(focusTarget)) {
            focusTarget = container?.isConnected
                ? getFirstAndLastTabableElements(container)[0] || container : undefined;
        }
        focusTarget?.focus();
    }

    fold() {
        this.restoreNativeFocus();
        this.feedbackChatWindow.fold();
    }

    close() {
        if (this.restoreReactivatedFocus) {
            this.restoreNativeFocus();
        }
        this.feedbackChatWindow.close();
    }
}

export class FeedbackChatBubble extends Component {
    static props = [];
    static template = "usl_feedback.FeedbackChatBubble";

    setup() {
        super.setup();
        this.feedbackChatWindow = useService("usl_feedback.chat_window");
    }
}

ChatHub.components = {
    ...ChatHub.components,
    FeedbackChatBubble,
    FeedbackChatWindow,
};

patch(ChatHub.prototype, {
    setup() {
        super.setup(...arguments);
        this.feedbackChatWindow = useService("usl_feedback.chat_window");
    },
});
