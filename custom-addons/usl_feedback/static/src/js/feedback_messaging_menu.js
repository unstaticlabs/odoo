import "@mail/chatter/web/chatter_patch";

import { Chatter } from "@mail/chatter/web_portal_project/chatter";
import { Composer } from "@mail/core/common/composer";
import { MessagingMenu } from "@mail/core/public_web/messaging_menu";
import { Thread } from "@mail/core/common/thread";
import { browser } from "@web/core/browser/browser";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";

import {
    Component,
    markup,
    onWillStart,
    onWillUnmount,
    onWillUpdateProps,
    useState,
} from "@odoo/owl";

import { captureFeedbackPagePreview } from "./feedback_page_preview";


function positiveInteger(value) {
    const normalized = Number(value);
    return Number.isSafeInteger(normalized) && normalized > 0 ? normalized : false;
}

export function feedbackPageContext(actionController, viewport = browser.visualViewport) {
    const props = actionController?.props || {};
    const action = actionController?.action || {};
    return {
        action_id: positiveInteger(action.id || props.actionId),
        model: typeof props.resModel === "string" ? props.resModel : false,
        res_id: positiveInteger(props.resId),
        viewport_width: positiveInteger(Math.round(viewport?.width || browser.innerWidth)),
        viewport_height: positiveInteger(Math.round(viewport?.height || browser.innerHeight)),
    };
}

function fileAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = reject;
        reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
        reader.readAsDataURL(file);
    });
}

export class FeedbackChatter extends Chatter {
    static template = "usl_feedback.FeedbackChatter";
    static components = { Composer, Thread };
    static props = [
        "agentActivity",
        "agentState",
        "busy",
        "composer",
        "onConfirm",
        "onMessagePosted",
        "onOpenTask",
        "onRetry",
        "placeholder",
        "task",
        "threadId",
        "threadModel",
    ];

    async onPostCallback() {
        await super.onPostCallback(...arguments);
        await this.props.onMessagePosted();
    }
}

export class FeedbackPanel extends Component {
    static template = "usl_feedback.FeedbackPanel";
    static components = { Dropdown, DropdownItem, FeedbackChatter };
    static props = ["captureState?", "clearScreenshot?", "close", "pageContext", "screenshot?"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.state = useState({
            phase: "loading",
            draftId: false,
            contextAvailable: false,
            includeContext: false,
            message: "",
            screenshotSelected: Boolean(this.props.screenshot),
            screenshotAttachmentId: false,
            attachments: [],
            recent: [],
            task: false,
            error: false,
            busy: false,
            progressStep: 0,
        });
        this.pollTimer = false;
        this.progressTimer = false;
        onWillStart(() => this.startDraft());
        onWillUnmount(() => {
            browser.clearTimeout(this.pollTimer);
            browser.clearTimeout(this.progressTimer);
        });
        onWillUpdateProps((nextProps) => {
            if (nextProps.screenshot && nextProps.screenshot !== this.props.screenshot) {
                this.state.screenshotSelected = true;
            } else if (!nextProps.screenshot && !this.state.screenshotAttachmentId) {
                this.state.screenshotSelected = false;
            }
        });
    }

    async startDraft() {
        this.state.error = false;
        try {
            const result = await this.orm.call(
                "usl.feedback.submission",
                "feedback_start",
                [this.props.pageContext]
            );
            Object.assign(this.state, {
                phase: "draft",
                draftId: result.draft_id,
                contextAvailable: result.context_available,
                includeContext: result.include_page_context,
                recent: result.recent,
            });
        } catch (error) {
            this.state.phase = "start_error";
            this.showError(error);
        }
    }

    async retryStartDraft() {
        this.state.phase = "loading";
        await this.startDraft();
    }

    async uploadScreenshot() {
        const screenshot = this.props.screenshot;
        if (!screenshot || this.state.screenshotAttachmentId) {
            return;
        }
        const attachment = await this.orm.call(
            "usl.feedback.submission",
            "feedback_add_attachment",
            [
                [this.state.draftId],
                screenshot.name,
                screenshot.mimetype,
                await fileAsBase64(screenshot.blob),
                true,
            ]
        );
        this.state.screenshotAttachmentId = attachment.id;
    }

    async toggleScreenshot() {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        this.state.screenshotSelected = !this.state.screenshotSelected;
        try {
            if (!this.state.screenshotSelected && this.state.screenshotAttachmentId) {
                await this.orm.call(
                    "usl.feedback.submission",
                    "feedback_remove_attachment",
                    [[this.state.draftId], this.state.screenshotAttachmentId]
                );
                this.state.screenshotAttachmentId = false;
            }
            if (!this.state.screenshotSelected) {
                this.props.clearScreenshot?.();
            }
        } catch (error) {
            this.state.screenshotSelected = !this.state.screenshotSelected;
            this.showError(error);
        } finally {
            this.state.busy = false;
        }
    }

    async onFilesSelected(event) {
        const screenshotCount = this.props.screenshot && this.state.screenshotSelected ? 1 : 0;
        const files = [...event.target.files].slice(
            0,
            10 - this.state.attachments.length - screenshotCount
        );
        this.state.busy = true;
        try {
            for (const file of files) {
                const attachment = await this.orm.call(
                    "usl.feedback.submission",
                    "feedback_add_attachment",
                    [
                        [this.state.draftId],
                        file.name,
                        file.type || "application/octet-stream",
                        await fileAsBase64(file),
                        false,
                    ]
                );
                this.state.attachments.push(attachment);
            }
        } catch (error) {
            this.showError(error);
        } finally {
            this.state.busy = false;
            event.target.value = "";
        }
    }

    async removeAttachment(attachment) {
        try {
            await this.orm.call(
                "usl.feedback.submission",
                "feedback_remove_attachment",
                [[this.state.draftId], attachment.id]
            );
            this.state.attachments = this.state.attachments.filter(
                (item) => item.id !== attachment.id
            );
        } catch (error) {
            this.showError(error);
        }
    }

    async submitInitial() {
        if (!this.state.message.trim() || this.state.busy) {
            return;
        }
        this.state.busy = true;
        this.state.error = false;
        try {
            if (this.props.screenshot && this.state.screenshotSelected) {
                await this.uploadScreenshot();
            }
            const task = await this.orm.call(
                "usl.feedback.submission",
                "feedback_submit_initial",
                [[this.state.draftId], this.state.message, this.state.includeContext]
            );
            this.state.task = task;
            this.state.phase = "conversation";
            this.state.screenshotSelected = false;
            this.state.screenshotAttachmentId = false;
            this.props.clearScreenshot?.();
            this.startAgentProgress();
            this.schedulePoll(0);
            if (task.context_omitted) {
                this.notification.add(
                    _t("Page details were left out because you no longer have access to this record."),
                    { type: "warning" }
                );
            }
        } catch (error) {
            this.showError(error);
        } finally {
            this.state.busy = false;
        }
    }

    async resumeTask(task) {
        this.state.task = task;
        this.state.phase = "conversation";
        this.startAgentProgress();
        this.schedulePoll(0);
    }

    showConversation() {
        if (!this.state.task) {
            return;
        }
        this.state.phase = "conversation";
        this.startAgentProgress();
        if (["queued", "processing"].includes(this.state.task.agent_state)) {
            this.schedulePoll(0);
        }
    }

    async showRecent() {
        browser.clearTimeout(this.pollTimer);
        this.stopAgentProgress();
        this.state.error = false;
        this.state.busy = true;
        try {
            this.state.recent = await this.orm.call("project.task", "feedback_recent", []);
            this.state.phase = "recent";
        } catch (error) {
            this.showError(error);
        } finally {
            this.state.busy = false;
        }
    }

    async newConversation() {
        if (this.state.phase === "draft" || (this.state.phase === "loading" && !this.state.task)) {
            return;
        }
        browser.clearTimeout(this.pollTimer);
        this.stopAgentProgress();
        this.state.phase = "loading";
        this.state.task = false;
        this.state.message = "";
        this.state.error = false;
        this.state.attachments = [];
        this.state.screenshotAttachmentId = false;
        this.state.screenshotSelected = Boolean(this.props.screenshot);
        await this.startDraft();
    }

    schedulePoll(delay = 2000) {
        browser.clearTimeout(this.pollTimer);
        this.pollTimer = browser.setTimeout(() => this.poll(), delay);
    }

    async poll() {
        if (!this.state.task) {
            return;
        }
        try {
            const task = await this.orm.call(
                "project.task",
                "feedback_poll_agent",
                [[this.state.task.id]]
            );
            this.state.task = task;
            if (!["queued", "processing"].includes(task.agent_state)) {
                this.refreshConversation(task);
            }
        } catch (error) {
            this.showError(error, false);
        }
        if (["queued", "processing"].includes(this.state.task?.agent_state)) {
            this.schedulePoll();
        } else {
            this.stopAgentProgress();
        }
    }

    refreshConversation(task = this.state.task) {
        if (task) {
            this.env.bus.trigger("MAIL:RELOAD-THREAD", {
                model: "project.task",
                id: task.id,
            });
        }
    }

    async onMessagePosted() {
        if (!this.state.task || this.state.task.withdrawn) {
            return;
        }
        try {
            this.state.task = await this.orm.call(
                "project.task",
                "feedback_conversation_state",
                [[this.state.task.id]]
            );
            if (["queued", "processing"].includes(this.state.task.agent_state)) {
                this.startAgentProgress();
                this.schedulePoll(0);
            }
        } catch (error) {
            this.showError(error, false);
        }
    }

    startAgentProgress() {
        this.stopAgentProgress();
        if (!["queued", "processing"].includes(this.state.task?.agent_state)) {
            return;
        }
        this.state.progressStep = 0;
        const advance = () => {
            if (!["queued", "processing"].includes(this.state.task?.agent_state)) {
                this.stopAgentProgress();
                return;
            }
            this.state.progressStep = Math.min(this.state.progressStep + 1, 3);
            this.progressTimer = browser.setTimeout(advance, 3500);
        };
        this.progressTimer = browser.setTimeout(advance, 2500);
    }

    stopAgentProgress() {
        browser.clearTimeout(this.progressTimer);
        this.progressTimer = false;
    }

    get agentActivity() {
        if (this.state.progressStep === 0) {
            return _t("Reading your report…");
        }
        if (this.state.progressStep === 1) {
            return this.state.task?.screenshot_attachment_id
                ? _t("Looking at the page preview…")
                : _t("Checking the details…");
        }
        if (this.state.progressStep === 2) {
            return _t("Preparing a draft…");
        }
        return _t("Still working…");
    }

    feedbackStatus(task) {
        if (task.withdrawn) {
            return [_t("Withdrawn"), _t("No further action"), "text-bg-secondary"];
        }
        const statuses = {
            queued: [_t("Agent working"), _t("Preparing a draft"), "text-bg-info"],
            processing: [_t("Agent working"), _t("Preparing a draft"), "text-bg-info"],
            waiting: [_t("Needs your reply"), _t("Reply in the chat"), "text-bg-warning"],
            error: [_t("Needs attention"), _t("Open to retry"), "text-bg-danger"],
            ready: [_t("Ready to send"), _t("Review and send"), "text-bg-success"],
            triaged: [_t("With product team"), task.stage, "text-bg-primary"],
        };
        return statuses[task.agent_state] || [task.stage, _t("Open feedback"), "text-bg-secondary"];
    }

    feedbackChatLabel(task) {
        const status = this.feedbackStatus(task)[0];
        return _t("Feedback #%(id)s, %(stage)s, %(status)s", {
            id: task.id,
            stage: task.stage,
            status,
        });
    }

    get canPostMessage() {
        return (
            !this.state.busy &&
            !this.state.task?.withdrawn &&
            !["queued", "processing"].includes(this.state.task?.agent_state)
        );
    }

    get composerPlaceholder() {
        if (this.state.task?.withdrawn) {
            return _t("This feedback is withdrawn.");
        }
        if (["queued", "processing"].includes(this.state.task?.agent_state)) {
            return _t("The feedback agent is replying…");
        }
        if (this.state.task?.agent_state === "triaged") {
            return _t("Message the product team…");
        }
        return _t("Reply to the feedback agent…");
    }

    async retry() {
        this.state.busy = true;
        try {
            this.state.task = await this.orm.call(
                "project.task",
                "feedback_retry_agent",
                [[this.state.task.id]]
            );
            this.startAgentProgress();
            this.schedulePoll(0);
        } catch (error) {
            this.showError(error);
        } finally {
            this.state.busy = false;
        }
    }

    async confirmTriage() {
        this.state.busy = true;
        try {
            this.state.task = await this.orm.call(
                "project.task",
                "feedback_confirm_triage",
                [[this.state.task.id]]
            );
            this.refreshConversation();
            this.stopAgentProgress();
        } catch (error) {
            this.showError(error);
        } finally {
            this.state.busy = false;
        }
    }

    confirmWithdraw() {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Withdraw feedback?"),
            body: _t("This stops the feedback process. The conversation and files will stay available."),
            confirmLabel: _t("Withdraw"),
            confirmClass: "btn-danger",
            confirm: () => this.withdrawFeedback(),
        });
    }

    async withdrawFeedback() {
        if (!this.state.task?.can_withdraw || this.state.busy) {
            return false;
        }
        this.state.busy = true;
        try {
            this.state.task = await this.orm.call(
                "project.task",
                "feedback_withdraw",
                [[this.state.task.id]]
            );
            this.refreshConversation();
            this.stopAgentProgress();
            browser.clearTimeout(this.pollTimer);
            return true;
        } catch (error) {
            this.showError(error);
            return false;
        } finally {
            this.state.busy = false;
        }
    }

    async openTask(task = this.state.task) {
        if (!task) {
            return;
        }
        await this.action.doAction({
            type: "ir.actions.act_window",
            name: task.name,
            res_model: "project.task",
            res_id: task.id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    async openBoard() {
        const action = await this.orm.call("project.project", "feedback_open_board", []);
        action.name = _t("Feedback");
        action.display_name = action.name;
        if (action.help) {
            action.help = markup(action.help);
        }
        await this.action.doAction(action);
    }

    showError(error, persistent = true) {
        const message =
            error?.data?.message ||
            error?.message ||
            _t("We couldn’t complete that action. Try again.");
        if (persistent) {
            this.state.error = message;
        } else {
            this.notification.add(message, { type: "warning" });
        }
    }
}

patch(MessagingMenu.prototype, {
    setup() {
        super.setup(...arguments);
        this.feedbackChatWindow = useService("usl_feedback.chat_window");
    },

    async onClickFeedback() {
        if (!this.feedbackChatWindow.isClosed) {
            this.feedbackChatWindow.open();
            this.dropdown.close();
            return;
        }
        const pageContext = feedbackPageContext(this.env.services.action.currentController);
        const captureId = this.feedbackChatWindow.beginCapture(pageContext);
        this.dropdown.close();
        await new Promise((resolve) => browser.requestAnimationFrame(resolve));
        try {
            const screenshot = await captureFeedbackPagePreview();
            this.feedbackChatWindow.completeCapture(captureId, screenshot);
        } catch {
            this.feedbackChatWindow.failCapture(captureId);
        }
    },
});
