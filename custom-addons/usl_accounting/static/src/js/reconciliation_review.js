import { Component, useState } from "@odoo/owl";
import { onWillRender } from "@web/owl2/utils";
import { Domain } from "@web/core/domain";
import { DomainSelector } from "@web/core/domain_selector/domain_selector";
import { deserializeDate, deserializeDateTime, formatDate, formatDateTime } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";
import { usePopover } from "@web/core/popover/popover_hook";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ListRenderer } from "@web/views/list/list_renderer";
import { formatMonetary } from "@web/views/fields/formatters";
import { listView } from "@web/views/list/list_view";

function formatTimestamp(value) {
    const date = deserializeDateTime(value);
    return `${formatDateTime(date)} ${date.offsetNameShort}`;
}

export class ReconciliationReviewDetails extends Component {
    static template = "usl_accounting.ReconciliationReviewDetails";
    static components = { DomainSelector };
    static props = ["status", "onReview", "onDismiss", "close"];
    setup() {
        this.state = useState({ busy: false });
    }
    get domain() {
        return Domain.and([this.props.status.domain, this.props.status.group_domain]).toString();
    }
    formatTimestamp = formatTimestamp;
    formatValuationDate(value) { return formatDate(deserializeDate(value)); }
    formatAmount(value, currencyId) {
        return value === null ? "—" : formatMonetary(value, { currencyId, noSymbol: true });
    }
    async perform(callback) {
        if (this.state.busy) return;
        this.state.busy = true;
        try {
            await callback();
            this.props.close();
        } finally {
            this.state.busy = false;
        }
    }
    review() { return this.perform(this.props.onReview); }
    dismiss() { return this.perform(this.props.onDismiss); }
}

export class ReconciliationReviewMark extends Component {
    static template = "usl_accounting.ReconciliationReviewMark";
    static props = ["status?", "loading", "error?", "onReview", "onDismiss", "onRetry"];
    setup() {
        this.popover = usePopover(ReconciliationReviewDetails, { position: "bottom" });
    }
    get label() {
        if (this.props.error) return _t("Review unavailable. Retry");
        if (this.props.loading) return _t("Loading shared review");
        const stateLabel = {
            unreviewed: _t("Remaining not reviewed"),
            reviewed: _t("Remaining reviewed"),
            changed: _t("Remaining changed since review"),
            acknowledged: _t("Remaining change acknowledged"),
        }[this.props.status?.state] || _t("Remaining not reviewed");
        const status = this.props.status;
        return status?.reviewer ? `${stateLabel} — ${status.reviewer}, ${formatTimestamp(status.reviewed_at)}. ` + status.amounts.map(
            (amount) => _t("Reviewed: %(baseline)s; current: %(current)s; change: %(delta)s %(currency)s", {
                ...amount, ...Object.fromEntries(["baseline", "current", "delta"].map((key) => [key, amount[key] === null ? "—" : formatMonetary(amount[key], { currencyId: amount.currency_id, noSymbol: true })])),
            })
        ).join("; ") : stateLabel;
    }
    get iconClass() {
        if (this.props.error) return "fa fa-refresh text-danger";
        return {
            reviewed: "fa fa-check-circle text-success",
            changed: "fa fa-exclamation-triangle text-warning",
            acknowledged: "fa fa-minus-circle text-muted",
        }[this.props.status?.state] || "fa fa-check-circle-o text-muted";
    }
    open(event) {
        if (this.props.error) return this.props.onRetry();
        if (this.props.loading || !this.props.status) return;
        if (this.popover.isOpen) return this.popover.close();
        this.popover.open(event.currentTarget, {
            status: this.props.status, onReview: this.props.onReview, onDismiss: this.props.onDismiss,
        });
    }
}

/** Stable identifiers only. Native group domains/aggregates remain untouched. */
export function reviewGroups(list, path = [], result = []) {
    if (!list.isGrouped) return result;
    for (const group of list.groups) {
        const field = list.groupBy[0];
        if (!["account_id", "partner_id", "date:month"].includes(field)) continue;
        const groupPath = [...path, [field, group.serverValue]];
        if (groupPath.some(([name]) => name === "account_id")) {
            result.push({ group, path: groupPath });
        }
        if (!group.isFolded) reviewGroups(group.list, groupPath, result);
    }
    return result;
}

export class ReconciliationReviewRenderer extends ListRenderer {
    static groupRowTemplate = "usl_accounting.ReconciliationReviewGroupRow";
    static components = { ...ListRenderer.components, ReconciliationReviewMark };
    setup() {
        super.setup();
        this.reviewState = useState({ statuses: {}, loading: false, error: false });
        this.reviewGeneration = 0;
        this.reviewSignature = null;
        onWillRender(() => {
            const groups = reviewGroups(this.props.list);
            this.reviewEntries = groups;
            this.reviewEntryById = new Map(groups.map((entry) => [entry.group.id, entry]));
            const signature = JSON.stringify([this.props.list.domain, this.props.list.context, groups.map(({ group, path }) => [group.id, path, group.aggregates])]);
            if (signature !== this.reviewSignature) {
                this.reviewSignature = signature;
                // Never mutate reactive state during the native render itself.
                Promise.resolve().then(() => this.loadReviews());
            }
        });
    }
    groupReview(group) {
        return this.reviewState.statuses[group.id];
    }
    supportsReview(group) {
        return this.reviewEntryById?.has(group.id);
    }
    async loadReviews() {
        const generation = ++this.reviewGeneration;
        const groups = reviewGroups(this.props.list);
        if (!groups.length) return;
        this.reviewState.loading = true;
        this.reviewState.error = false;
        try {
            const batches = [];
            for (let index = 0; index < groups.length; index += 200) {
                batches.push(groups.slice(index, index + 200));
            }
            const domain = this.props.list.domain;
            const context = this.props.list.context;
            const statuses = (await Promise.all(batches.map((batch) => this.orm.call(
                "account.reconciliation.review", "get_review_status", [domain, batch.map(({ path }) => path)], { context }
            )))).flat();
            if (generation === this.reviewGeneration) {
                this.reviewState.statuses = Object.fromEntries(groups.map(({ group }, index) => [group.id, statuses[index]]));
            }
        } catch {
            if (generation === this.reviewGeneration) this.reviewState.error = true;
        } finally {
            if (generation === this.reviewGeneration) this.reviewState.loading = false;
        }
    }
    async changeReview(group, dismiss) {
        const entry = reviewGroups(this.props.list).find((item) => item.group === group);
        const status = this.groupReview(group);
        if (!entry || !status) return;
        this.reviewState.loading = true;
        try {
            const result = await this.orm.call("account.reconciliation.review", dismiss ? "dismiss_variance" : "mark_reviewed", [
                this.props.list.domain, entry.path, status.revision, status.observed_balance,
            ], { context: this.props.list.context });
            this.reviewState.statuses[group.id] = result;
        } catch (error) {
            this.notificationService.add(error.data?.message || _t("The review could not be saved. Reload the group and try again."), { type: "warning" });
            await this.loadReviews();
            return false;
        } finally {
            this.reviewState.loading = false;
        }
    }
}

registry.category("views").add("usl_reconciliation_review", { ...listView, Renderer: ReconciliationReviewRenderer });
