import { Component, onMounted, onWillUnmount, onWillUpdateProps, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { useSetupAction } from "@web/search/action_hook";
import { _t } from "@web/core/l10n/translation";
import { router } from "@web/core/browser/router";
import { deserializeDateTime, formatDate } from "@web/core/l10n/dates";
import { sprintf } from "@web/core/utils/strings";
import { user } from "@web/core/user";

/** Fold one character like the server does for matching: no case, no accent. */
function foldChar(char) {
    const base = char.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
    return base.length === 1 ? base : char;
}

/**
 * Split plain text into segments, flagging the ones that match a query word.
 * The result is rendered as text nodes, never as HTML.
 */
export function highlightParts(text, query) {
    const value = String(text || "");
    const terms = [...new Set(String(query || "").split(/[^\p{L}\p{N}]+/u).filter(Boolean))]
        .map((term) => [...term].map(foldChar).join(""));
    if (!value || !terms.length) {
        return [{ text: value, match: false }];
    }
    const chars = [...value];
    const owner = [];
    let folded = "";
    chars.forEach((char, index) => {
        const piece = foldChar(char);
        folded += piece;
        owner.push(...new Array(piece.length).fill(index));
    });
    const marked = new Array(chars.length).fill(false);
    for (const term of terms) {
        for (let at = folded.indexOf(term); at >= 0; at = folded.indexOf(term, at + term.length)) {
            for (let unit = at; unit < at + term.length; unit++) {
                marked[owner[unit]] = true;
            }
        }
    }
    const parts = [];
    chars.forEach((char, index) => {
        const last = parts.at(-1);
        if (last && last.match === marked[index]) {
            last.text += char;
        } else {
            parts.push({ text: char, match: marked[index] });
        }
    });
    return parts;
}

export class HomeContentSearch extends Component {
    static template = "usl_home.ContentSearch";
    static props = { query: { type: String, optional: true }, revision: Number };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.input = useRef("input");
        this.state = useState({ query: this.props.query || "", groups: [], documents: null, loading: false, documentsLoading: false, error: false, documentError: false, selected: null });
        // Meaning-based task matches arrive beside the keyword results, never
        // behind a click. A deployment without the feature is asked only once.
        Object.assign(this.state, { meaning: [], meaningLoading: false });
        this.meaningDisabled = false;
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
        const groups = [...this.state.groups, ...(this.state.documents ? [this.state.documents] : [])];
        const shown = new Set(groups.flatMap((group) => group.records.map((record) => this.resultKey(record))));
        const related = this.state.meaning.filter((record) => !shown.has(this.resultKey(record)));
        const index = groups.findIndex((group) => group.key === "tasks");
        if (index < 0 && !related.length && !this.state.meaningLoading) {
            return groups;
        }
        // Related tasks join the Tasks group after the keyword rows, so rows already
        // on screen never move. Without keyword tasks the group is about the subject.
        const tasks = index < 0
            ? { key: "tasks", label: _t("Tasks"), offset: 0, has_more: false, records: [] }
            : groups[index];
        const merged = {
            ...tasks,
            label: !tasks.records.length && related.length ? _t("Tasks about this") : tasks.label,
            relatedStart: tasks.records.length,
            records: [...tasks.records, ...related],
        };
        if (index < 0) {
            // Where the group sorts when keyword tasks exist: ahead of the others.
            groups.unshift(merged);
        } else {
            groups[index] = merged;
        }
        return groups;
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

    excerptParts(record) {
        return highlightParts(record.excerpt, this.state.query);
    }

    sourceLabel(source) {
        const author = source.author_name || "";
        const date = source.date ? formatDate(deserializeDateTime(source.date)) : "";
        const who = [author, date].filter(Boolean).join(" · ");
        if (source.message_kind === "subject") {
            return who ? sprintf(_t("Subject of a message · %s"), who) : _t("Subject of a message");
        }
        if (source.message_kind === "note") {
            return who ? sprintf(_t("Note · %s"), who) : _t("Note");
        }
        return who || source.label;
    }

    resultDomId(record) {
        return `usl-home-search-result-${record.model.replaceAll(".", "-")}-${record.id}`;
    }

    get empty() {
        return !this.state.loading && !this.state.documentsLoading && !this.state.meaningLoading && !this.state.error && !this.state.documentError && !this.results.length;
    }

    get emptyMessage() {
        const names = (user.activeCompanies || []).map((company) => company.name).filter(Boolean);
        return names.length
            ? sprintf(_t("Nothing matches “%(query)s” in %(company)s. Try fewer words or another spelling."), { query: this.state.query.trim(), company: names.join(", ") })
            : sprintf(_t("Nothing matches “%(query)s”. Try fewer words or another spelling."), { query: this.state.query.trim() });
    }

    /** Two words, or one long enough to carry a meaning. */
    asksMeaning(query) {
        return !this.meaningDisabled && (query.split(/\s+/).filter(Boolean).length >= 2 || query.length >= 8);
    }

    inputChanged(event) {
        this.state.query = event.target.value;
        this.generation++;
        this.cancelRequests();
        this.state.groups = [];
        this.state.documents = null;
        Object.assign(this.state, { meaning: [], meaningLoading: this.asksMeaning(this.state.query.trim()) });
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
        Object.assign(this.state, { groups: [], documents: null, meaning: [], meaningLoading: Boolean(query) && this.asksMeaning(query), selected: null, error: false, documentError: false, loading: Boolean(query), documentsLoading: Boolean(query) });
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
            this.loadMeaning(query, generation),
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

    async loadMeaning(query, generation) {
        if (!this.state.meaningLoading) {
            return;
        }
        try {
            const result = await this.request(this.orm.call("project.task", "search_by_meaning", [query]));
            if (generation === this.generation) {
                this.meaningDisabled = result.status === "disabled";
                this.state.meaning = result.status === "ok" ? result.group.records : [];
            }
        } catch {
            // Keyword results are already on screen; say nothing.
        } finally {
            if (generation === this.generation) {
                this.state.meaningLoading = false;
            }
        }
    }

    async more(group) {
        const base = group.key === "documents" ? group : this.state.groups.find((old) => old.key === group.key);
        if (!base || base.loading) { return; }
        base.loading = true;
        if (group.key === "documents") {
            return this.loadDocuments(this.state.query.trim(), this.generation, group.offset + 5);
        }
        const generation = this.generation;
        const offset = base.offset + 5;
        try {
            const { groups } = await this.request(this.orm.call("usl.home.service", "search_records", [this.state.query.trim()], { categories: [group.key], offset }));
            if (generation === this.generation && groups[0]) {
                const next = groups[0];
                next.records = this.mergeRecords(base.records, next.records);
                this.state.groups = this.state.groups.map((old) => old.key === group.key ? next : old);
            }
        } catch {
            if (generation === this.generation) {
                this.notification.add(_t("More results could not be loaded. Try again."), { type: "warning" });
            }
        } finally {
            base.loading = false;
        }
    }

    async open(record) {
        try {
            const args = [record.model, record.id];
            if (record.source?.kind === "message") {
                args.push(record.source.id, record.source.field);
            }
            const action = await this.orm.call("usl.home.service", "get_search_result_action", args);
            const highlight = action.usl_highlight_message_id;
            delete action.usl_highlight_message_id;
            if (highlight) {
                // The form's chatter reads this when it mounts, as it does for a
                // /mail/message link, but the web client stays loaded.
                router.current.highlight_message_id = highlight;
            }
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
