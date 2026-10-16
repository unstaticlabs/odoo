import { Component, onMounted, onWillUnmount, onWillUpdateProps, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { useSetupAction } from "@web/search/action_hook";
import { _t } from "@web/core/l10n/translation";

export class HomeContentSearch extends Component {
    static template = "usl_home.ContentSearch";
    static props = { query: { type: String, optional: true }, revision: Number };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.input = useRef("input");
        this.state = useState({ query: this.props.query || "", groups: [], documents: null, loading: false, documentsLoading: false, error: false, documentError: false, selected: null });
        this.generation = 0;
        this.requests = new Set();
        useSetupAction({
            getLocalState: () => ({ contentSearchQuery: this.state.query }),
            getGlobalState: () => ({ contentSearchQuery: this.state.query }),
        });
        onMounted(() => this.search());
        onWillUpdateProps((props) => {
            if (props.revision !== this.props.revision) {
                this.search();
            }
        });
        onWillUnmount(() => {
            clearTimeout(this.timer);
            this.generation++;
            this.cancelRequests();
        });
    }

    get groups() {
        return [...this.state.groups, ...(this.state.documents ? [this.state.documents] : [])];
    }

    get results() {
        return this.groups.flatMap((group) => group.records);
    }

    get selectedRecord() {
        return this.results.find((record) => this.resultKey(record) === this.state.selected);
    }

    resultKey(record) {
        return `${record.model}:${record.id}`;
    }

    mergeRecords(previous, next) {
        const records = new Map(previous.map((record) => [this.resultKey(record), record]));
        for (const record of next) {
            records.set(this.resultKey(record), record);
        }
        return [...records.values()];
    }

    resultDomId(record) {
        return `usl-home-search-result-${record.model.replaceAll(".", "-")}-${record.id}`;
    }

    get empty() {
        return !this.state.loading && !this.state.documentsLoading && !this.state.error && !this.state.documentError && !this.results.length;
    }

    inputChanged(event) {
        this.state.query = event.target.value;
        this.generation++;
        this.cancelRequests();
        this.state.groups = [];
        this.state.documents = null;
        this.state.selected = null;
        this.state.error = false;
        this.state.documentError = false;
        this.state.loading = Boolean(this.state.query.trim());
        this.state.documentsLoading = Boolean(this.state.query.trim());
        clearTimeout(this.timer);
        this.timer = setTimeout(() => this.search(), 250);
    }

    clear() {
        this.state.query = "";
        clearTimeout(this.timer);
        this.search();
        this.input.el.focus();
    }

    cancelRequests() {
        for (const request of this.requests) {
            request.abort?.();
        }
        this.requests.clear();
    }

    async request(request) {
        this.requests.add(request);
        try {
            return await request;
        } finally {
            this.requests.delete(request);
        }
    }

    async search() {
        clearTimeout(this.timer);
        const generation = ++this.generation;
        this.cancelRequests();
        const query = this.state.query.trim();
        Object.assign(this.state, { groups: [], documents: null, selected: null, error: false, documentError: false, loading: Boolean(query), documentsLoading: Boolean(query) });
        if (!query) {
            return;
        }
        // Tasks/Projects appear without waiting for unrelated business apps;
        // only these two local batches and Documents can be pending at once.
        let remaining = 2;
        const loadRecords = async (kwargs) => {
            try {
                const result = await this.request(this.orm.call("usl.home.service", "search_records", [query], kwargs));
                if (generation === this.generation) {
                    const groups = new Map(this.state.groups.map((group) => [group.key, group]));
                    for (const group of result.groups) {
                        groups.set(group.key, group);
                    }
                    this.state.groups = [...groups.values()].sort((left, right) =>
                        ({ tasks: 0, projects: 1 }[left.key] ?? 2) -
                        ({ tasks: 0, projects: 1 }[right.key] ?? 2));
                }
            } catch {
                if (generation === this.generation) {
                    this.state.error = true;
                }
            } finally {
                if (generation === this.generation && !--remaining) {
                    this.state.loading = false;
                }
            }
        };
        await Promise.allSettled([
            loadRecords({ categories: ["tasks", "projects"] }),
            loadRecords({ exclude_categories: ["tasks", "projects"] }),
            this.loadDocuments(query, generation),
        ]);
    }

    async loadDocuments(query = this.state.query.trim(), generation = this.generation, offset = 0) {
        this.state.documentsLoading = true;
        this.state.documentError = false;
        try {
            const { group } = await this.request(this.orm.call("usl.home.service", "search_documents", [query], { offset }));
            if (generation === this.generation) {
                this.state.documentError = Boolean(group.unavailable);
                if (offset && this.state.documents) {
                    group.records = this.mergeRecords(this.state.documents.records, group.records);
                }
                this.state.documents = group;
            }
        } catch {
            if (generation === this.generation) {
                this.state.documentError = true;
            }
        } finally {
            if (generation === this.generation) {
                this.state.documentsLoading = false;
            }
        }
    }

    async more(group) {
        if (group.loading) { return; }
        group.loading = true;
        if (group.key === "documents") {
            return this.loadDocuments(this.state.query.trim(), this.generation, group.offset + 5);
        }
        const generation = this.generation;
        const offset = group.offset + 5;
        try {
            const { groups } = await this.request(this.orm.call("usl.home.service", "search_records", [this.state.query.trim()], { categories: [group.key], offset }));
            if (generation === this.generation && groups[0]) {
                const next = groups[0];
                next.records = this.mergeRecords(group.records, next.records);
                this.state.groups = this.state.groups.map((old) => old.key === group.key ? next : old);
            }
        } catch {
            if (generation === this.generation) {
                this.notification.add(_t("More results could not be loaded. Try again."), { type: "warning" });
            }
        } finally {
            group.loading = false;
        }
    }

    async open(record) {
        try {
            const action = await this.orm.call("usl.home.service", "get_search_result_action", [record.model, record.id]);
            await this.action.doAction(action);
        } catch {
            this.notification.add(_t("This record could not be opened. Refresh the search and try again."), { type: "warning" });
        }
    }

    keydown(event) {
        if (["ArrowDown", "ArrowUp"].includes(event.key) && this.results.length) {
            event.preventDefault();
            const direction = event.key === "ArrowDown" ? 1 : -1;
            const records = this.results;
            const current = records.findIndex((record) => this.resultKey(record) === this.state.selected);
            const next = current < 0
                ? (direction > 0 ? 0 : records.length - 1)
                : (current + direction + records.length) % records.length;
            this.state.selected = this.resultKey(records[next]);
        } else if (event.key === "Enter" && this.selectedRecord) {
            event.preventDefault();
            this.open(this.selectedRecord);
        } else if (event.key === "Enter") {
            event.preventDefault();
            this.search();
        } else if (event.key === "Escape") {
            event.preventDefault();
            this.clear();
        }
    }

    continueDocuments() {
        return this.action.doAction("usl_documents.action_documents_workspace", {
            additionalContext: { search_default_all_text: this.state.query.trim() },
        });
    }
}
