import { ChatHub } from "@mail/core/common/chat_hub";
import { browser } from "@web/core/browser/browser";
import { localization } from "@web/core/l10n/localization";
import { getActiveHotkey } from "@web/core/hotkeys/hotkey_service";
import { registry } from "@web/core/registry";
import { useAutofocus, useBackButton, useBus, useService } from "@web/core/utils/hooks";
import { isEventHandled } from "@web/core/utils/misc";
import { patch } from "@web/core/utils/patch";
import { useSubEnv } from "@web/owl2/utils";

import { Component, reactive } from "@odoo/owl";

import { FeedbackPanel } from "./feedback_messaging_menu";


const FOCUS_EVENT = "usl-feedback-chat-window:focus";

export class FeedbackChatWindowService {
    constructor(env, services) {
        this.env = env;
        this.store = services["mail.store"];
        this.mode = "closed";
        this.pageContext = false;
        this.screenshot = false;
        this.captureState = "idle";
        this.captureId = 0;
        this.WINDOW = 704;
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
    dependencies: ["mail.store"],
    start(env, services) {
        const service = reactive(new FeedbackChatWindowService(env, services));
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
        this.rootRef = useAutofocus({ refName: "root", mobile: true });
        useBackButton(() => this.close());
        useBus(this.env.bus, FOCUS_EVENT, () => this.rootRef.el?.focus());
    }

    get attClass() {
        return {
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

    onClickHeader(ev) {
        if (!this.ui.isSmall && !ev.target.closest("button")) {
            this.feedbackChatWindow.fold();
        }
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

    close() {
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
