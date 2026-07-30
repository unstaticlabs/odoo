/** @odoo-module **/

import {
    Component,
    onMounted,
    onWillDestroy,
    onWillStart,
    onWillUnmount,
    onWillUpdateProps,
    useRef,
    useSubEnv,
    useState,
} from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { Domain } from "@web/core/domain";
import { router } from "@web/core/browser/router";
import { loadPDFJSAssets } from "@web/core/utils/pdfjs";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useBus, useService } from "@web/core/utils/hooks";
import { SearchBar } from "@web/search/search_bar/search_bar";
import { WithSearch } from "@web/search/with_search/with_search";
import { getDefaultConfig } from "@web/views/view";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const FILTER_DEFAULTS = {
    companyId: "",
    tagIds: [],
    correspondentId: "",
    documentTypeId: "",
    dateFrom: "",
    dateTo: "",
    addedFrom: "",
    addedTo: "",
    source: "",
    confidentiality: "",
    reviewState: "",
    linkedState: "",
    linkedRecord: "",
    mappedPartnerId: "",
    paperlessId: "",
    customFieldId: "",
    customFieldValue: "",
};

for (const key of [
    "uslDocumentsWorkspace",
    "uslDocumentId",
    "uslVersionId",
    "uslDocumentsRecordContext",
    "uslDocumentsReturnRecord",
]) {
    router.hideKeyFromUrl(key);
}

export class DocumentPreview extends Component {
    static template = "usl_documents.DocumentPreview";
    static props = {
        url: String,
        versionId: { type: String, optional: true },
    };

    setup() {
        this.canvas = useRef("canvas");
        this.loadToken = 0;
        this.imageUrl = null;
        this.pdf = null;
        this.state = useState({
            loading: true,
            kind: "",
            text: "",
            error: "",
            page: 1,
            pageCount: 0,
        });
        onMounted(() => this.load(this.props));
        onWillUpdateProps((nextProps) => {
            if (nextProps.url !== this.props.url) {
                this.load(nextProps);
            }
        });
        onWillDestroy(() => this.cleanup());
    }

    cleanup() {
        this.loadToken += 1;
        if (this.imageUrl) {
            URL.revokeObjectURL(this.imageUrl);
            this.imageUrl = null;
        }
        this.pdf?.destroy();
        this.pdf = null;
    }

    async load(props) {
        this.cleanup();
        const token = this.loadToken;
        Object.assign(this.state, {
            loading: true,
            kind: "",
            text: "",
            error: "",
            page: 1,
            pageCount: 0,
        });
        try {
            const response = await browser.fetch(props.url, {
                credentials: "same-origin",
                cache: "no-store",
            });
            if (!response.ok) {
                throw new Error(`Preview request failed (${response.status}).`);
            }
            const contentType = (
                response.headers.get("Content-Type") || ""
            ).toLowerCase();
            if (contentType.includes("application/pdf")) {
                await loadPDFJSAssets();
                const pdf = await globalThis.pdfjsLib.getDocument({
                    data: await response.arrayBuffer(),
                }).promise;
                if (token !== this.loadToken) {
                    pdf.destroy();
                    return;
                }
                this.pdf = pdf;
                this.state.kind = "pdf";
                this.state.pageCount = pdf.numPages;
                browser.requestAnimationFrame(() => this.renderPdfPage());
            } else if (contentType.startsWith("image/")) {
                const imageUrl = URL.createObjectURL(await response.blob());
                if (token !== this.loadToken) {
                    URL.revokeObjectURL(imageUrl);
                    return;
                }
                this.imageUrl = imageUrl;
                this.state.kind = "image";
            } else if (
                contentType.startsWith("text/") ||
                contentType.includes("html")
            ) {
                const source = await response.text();
                if (token !== this.loadToken) {
                    return;
                }
                const parsed = new DOMParser().parseFromString(source, "text/html");
                this.state.text = parsed.body?.textContent || source;
                this.state.kind = "text";
            } else {
                throw new Error("This file format does not provide an inline preview.");
            }
        } catch (error) {
            if (token === this.loadToken) {
                this.state.error =
                    error.message || "The preview could not be displayed.";
            }
        } finally {
            if (token === this.loadToken) {
                this.state.loading = false;
            }
        }
    }

    async renderPdfPage() {
        const canvas = this.canvas.el;
        if (!canvas || !this.pdf) {
            return;
        }
        const token = this.loadToken;
        const page = await this.pdf.getPage(this.state.page);
        if (token !== this.loadToken) {
            return;
        }
        const baseViewport = page.getViewport({ scale: 1 });
        const availableWidth = Math.max(
            240,
            canvas.parentElement?.clientWidth || 680
        );
        const scale = Math.min(2, availableWidth / baseViewport.width);
        const viewport = page.getViewport({ scale });
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        await page.render({
            canvasContext: canvas.getContext("2d"),
            viewport,
        }).promise;
    }

    async previousPage() {
        if (this.state.page > 1) {
            this.state.page -= 1;
            await this.renderPdfPage();
        }
    }

    async nextPage() {
        if (this.state.page < this.state.pageCount) {
            this.state.page += 1;
            await this.renderPdfPage();
        }
    }
}

export class OpenDocumentsField extends Component {
    static template = "usl_documents.OpenDocumentsField";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
    }

    get count() {
        return Number(this.props.record.data[this.props.name]) || 0;
    }

    get label() {
        return `Open ${this.count} document${this.count === 1 ? "" : "s"}`;
    }

    async open() {
        const action = await this.orm.call(
            this.props.record.resModel,
            "action_open_documents",
            [[this.props.record.resId]]
        );
        return this.action.doAction(action);
    }
}

export class PermanentDeleteDialog extends Component {
    static template = "usl_documents.PermanentDeleteDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        documentName: String,
        confirm: Function,
    };

    setup() {
        this.state = useState({ reason: "", busy: false });
    }

    async confirm() {
        const reason = this.state.reason.trim();
        if (!reason || this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            await this.props.confirm(reason);
            this.props.close();
        } finally {
            this.state.busy = false;
        }
    }
}

export class DocumentsWorkspaceView extends Component {
    static template = "usl_documents.DocumentsWorkspaceView";
    static components = { DocumentPreview, SearchBar };
    static props = {
        ...standardActionServiceProps,
        context: { type: Object, optional: true },
        domain: { type: Array, optional: true },
        groupBy: { type: Array, optional: true },
        orderBy: { type: Array, optional: true },
        display: { type: Object, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.searchModel = this.env.searchModel;
        const params = this.props.action.params || {};
        this.recordContext =
            params.res_model && params.res_id
                ? { resModel: params.res_model, resId: Number(params.res_id) }
                : null;
        this.listScroller = useRef("documentList");
        const storagePrefix = `usl_documents.workspace.${user.userId}`;
        this.globalStorageKey = `${storagePrefix}.global`;
        this.storageKey = `${storagePrefix}.${
            this.recordContext
                ? `${this.recordContext.resModel}.${this.recordContext.resId}`
                : "global"
        }`;
        let restored = {};
        let hasRecordState = false;
        try {
            const recordState = browser.sessionStorage.getItem(this.storageKey);
            hasRecordState = Boolean(this.recordContext && recordState);
            restored = JSON.parse(
                recordState ||
                    browser.sessionStorage.getItem(this.globalStorageKey) ||
                    "{}"
            );
        } catch {
            restored = {};
        }
        const urlState = this.readUrlState();
        if (urlState.filters) {
            restored = { ...restored, ...urlState.filters };
        }
        if (this.recordContext) {
            if (!hasRecordState && !urlState.filters) {
                restored.query = "";
                for (const [key, value] of Object.entries(FILTER_DEFAULTS)) {
                    restored[key] = Array.isArray(value) ? [] : value;
                }
            }
            if (!hasRecordState && !urlState.documentId) {
                restored.selectedDocumentId = null;
                restored.selectedVersionId = null;
            }
            if (params.linked_filter) {
                restored.workspace = "all";
                restored.page = 1;
            }
            restored.linkedRecord = params.linked_filter
                ? `${this.recordContext.resModel}:${this.recordContext.resId}`
                : "";
            restored.linkedState = "";
            restored.mappedPartnerId = params.mapped_partner_id
                ? String(params.mapped_partner_id)
                : "";
        }
        if (params.initial_workspace) {
            restored.workspace = params.initial_workspace;
            restored.page = 1;
        }
        this.initialDocumentId =
            urlState.documentId || Number(restored.selectedDocumentId) || null;
        this.initialVersionId =
            urlState.versionId || this.stringValue(restored.selectedVersionId) || null;
        this.hasLocalListHistory = false;
        this.closingDetail = false;
        this.recordContextKey = this.recordContext
            ? `${this.recordContext.resModel}:${this.recordContext.resId}`
            : null;
        this.state = useState({
            loading: true,
            uploading: false,
            savingMetadata: false,
            savingView: false,
            dragged: false,
            degraded: false,
            error: "",
            query: typeof restored.query === "string" ? restored.query : "",
            searchInput: typeof restored.query === "string" ? restored.query : "",
            searchFocused: false,
            workspace:
                typeof restored.workspace === "string" ? restored.workspace : "recent",
            view: ["cards", "list"].includes(restored.view) ? restored.view : "cards",
            sort: ["recent", "ingested", "date", "title"].includes(restored.sort)
                ? restored.sort
                : "recent",
            ...this.restoreFilters(restored),
            moreFilters: false,
            savedViewName: "",
            smartViews: [],
            tags: [],
            correspondents: [],
            documentTypes: [],
            companies: [],
            customFields: [],
            linkFacets: [],
            page:
                Number.isInteger(restored.page) && restored.page > 0
                    ? restored.page
                    : 1,
            count: 0,
            pageSize: 24,
            documents: [],
            selected: null,
            selectedLoading: false,
            editingMetadata: false,
            metadataDraft: null,
            tagPickerOpen: false,
            tagQuery: "",
            tagShortcutQuery: "",
            creatingTag: false,
            operation: null,
            failedOperations: [],
            canUpload: false,
            truncated: false,
        });
        this.searchReady = false;
        this.pollingOperationIds = new Set();
        this.customFieldFilterTimer = null;
        this.closeHistoryTimer = null;
        this.closeCanonicalTimer = null;
        this.customFieldFilterApplied = Boolean(
            this.state.customFieldId && this.state.customFieldValue
        );
        this.onPopState = (event) => this.handlePopState(event);
        useBus(this.searchModel, "update", () => this.onNativeSearchUpdate());
        onWillStart(async () => {
            await this.load();
            if (this.migrateLegacyTagFilters()) {
                await this.load();
            }
            this.searchReady = true;
            if (this.initialDocumentId) {
                await this.openDocumentById(
                    this.initialDocumentId,
                    this.initialVersionId
                );
            }
        });
        onMounted(() => {
            browser.addEventListener("popstate", this.onPopState);
            this.ensureRecordReturnHistory();
            // Odoo's host router may normalize the action URL immediately after
            // mounting. Reassert the document state on the following frame so a
            // copied link and subsequent reload remain stable.
            browser.requestAnimationFrame(() => {
                this.replaceNavigationState();
                this.restoreScroll();
            });
        });
        onWillUnmount(() => {
            browser.removeEventListener("popstate", this.onPopState);
            if (this.customFieldFilterTimer) {
                browser.clearTimeout(this.customFieldFilterTimer);
            }
            if (this.closeHistoryTimer) {
                browser.clearTimeout(this.closeHistoryTimer);
            }
            if (this.closeCanonicalTimer) {
                browser.clearTimeout(this.closeCanonicalTimer);
            }
        });
    }

    readUrlState() {
        try {
            const url = new URL(browser.location.href);
            const filters = JSON.parse(url.searchParams.get("usl_filters") || "null");
            return {
                documentId: Number(url.searchParams.get("usl_document")) || null,
                versionId: url.searchParams.get("usl_version") || null,
                filters,
            };
        } catch {
            return { documentId: null, versionId: null, filters: null };
        }
    }

    restoreFilters(restored) {
        return {
            companyId: this.stringValue(restored.companyId),
            tagIds: Array.isArray(restored.tagIds)
                ? restored.tagIds.map(Number).filter(Boolean)
                : [],
            correspondentId: this.stringValue(restored.correspondentId),
            documentTypeId: this.stringValue(restored.documentTypeId),
            dateFrom: this.stringValue(restored.dateFrom),
            dateTo: this.stringValue(restored.dateTo),
            addedFrom: this.stringValue(restored.addedFrom),
            addedTo: this.stringValue(restored.addedTo),
            source: this.stringValue(restored.source),
            confidentiality: this.stringValue(restored.confidentiality),
            reviewState: this.stringValue(restored.reviewState),
            linkedState: this.stringValue(restored.linkedState),
            linkedRecord: this.stringValue(restored.linkedRecord),
            mappedPartnerId: this.stringValue(restored.mappedPartnerId),
            paperlessId: this.stringValue(restored.paperlessId),
            customFieldId: this.stringValue(restored.customFieldId),
            customFieldValue: this.stringValue(restored.customFieldValue),
        };
    }

    stringValue(value) {
        return ["string", "number"].includes(typeof value) ? String(value) : "";
    }

    get sharedViews() {
        return this.state.smartViews.filter((view) => !view.personal);
    }

    get personalViews() {
        return this.state.smartViews.filter((view) => view.personal);
    }

    get activeFilterCount() {
        return this.searchModel.facets.length;
    }

    get activeSmartView() {
        return this.state.smartViews.find(
            (view) => view.key === this.state.workspace
        );
    }

    get smartViewShortcuts() {
        return this.activeSmartView?.quick_filters || [];
    }

    get visibleTagShortcuts() {
        const selected = new Set(
            this.state.tags
                .filter((tag) => this.isTagShortcutActive(tag))
                .map((tag) => tag.id)
        );
        return [...this.state.tags]
            .sort(
                (left, right) =>
                    Number(selected.has(right.id)) - Number(selected.has(left.id)) ||
                    (right.document_count || 0) - (left.document_count || 0) ||
                    left.name.localeCompare(right.name)
            )
            .slice(0, 6);
    }

    get overflowTagShortcuts() {
        const visible = new Set(this.visibleTagShortcuts.map((tag) => tag.id));
        return this.state.tags
            .filter((tag) => !visible.has(tag.id))
            .sort(
                (left, right) =>
                    (right.document_count || 0) - (left.document_count || 0) ||
                    left.name.localeCompare(right.name)
            );
    }

    get tagShortcutResults() {
        const query = this.state.tagShortcutQuery.trim().toLocaleLowerCase();
        const candidates = query
            ? this.state.tags.filter((tag) =>
                  tag.name.toLocaleLowerCase().includes(query)
              )
            : this.overflowTagShortcuts;
        return [...candidates]
            .sort(
                (left, right) =>
                    Number(this.isTagShortcutActive(right)) -
                        Number(this.isTagShortcutActive(left)) ||
                    (right.document_count || 0) - (left.document_count || 0) ||
                    left.name.localeCompare(right.name)
            )
            .slice(0, 50);
    }

    get documentGroups() {
        const groupBys = this.searchModel.groupBy;
        if (!groupBys.length) {
            return [{ key: "all", label: "", documents: this.state.documents }];
        }
        const values = {
            company_id: (document) => document.company || "No company",
            correspondent_id: (document) =>
                document.correspondent || "No correspondent",
            document_type_id: (document) =>
                document.document_type || "No document type",
            linked_employee_id: (document) =>
                document.linked_employee?.name || "No linked employee",
            confidentiality: (document) =>
                ({
                    internal: "Internal",
                    accounting: "Accounting evidence",
                    hr: "HR restricted",
                    private: "Private",
                }[document.confidentiality] || "No privacy level"),
            review_state: (document) =>
                ({
                    needs_attention: "Needs review",
                    classified: "Classified",
                    reviewed: "Reviewed",
                }[document.review_state] || "No review status"),
            document_date: (document, interval) =>
                this.groupDateLabel(document.date, interval),
            paperless_created: (document, interval) =>
                this.groupDateLabel(document.ingested_at, interval),
        };
        const grouped = new Map();
        for (const document of this.state.documents) {
            const labels = groupBys.map((raw) => {
                const [field, interval] = raw.split(":");
                return values[field]?.(document, interval) || "Other";
            });
            const key = JSON.stringify(labels);
            if (!grouped.has(key)) {
                grouped.set(key, {
                    key,
                    label: labels.join(" · "),
                    documents: [],
                });
            }
            grouped.get(key).documents.push(document);
        }
        return [...grouped.values()];
    }

    groupDateLabel(value, interval) {
        if (!value) {
            return "No date";
        }
        const text = String(value);
        if (interval === "year") {
            return text.slice(0, 4);
        }
        if (interval === "quarter") {
            const month = Number(text.slice(5, 7)) || 1;
            return `${text.slice(0, 4)} Q${Math.ceil(month / 3)}`;
        }
        if (interval === "day") {
            return text.slice(0, 10);
        }
        return text.slice(0, 7);
    }

    get activeFacets() {
        const facets = [];
        const find = (items, id) =>
            items.find((item) => item.id === Number(id))?.name;
        for (const tagId of this.state.tagIds) {
            facets.push({
                key: `tag:${tagId}`,
                label: `Tag: ${find(this.state.tags, tagId) || tagId}`,
            });
        }
        for (const [key, label, items] of [
            ["companyId", "Company", this.state.companies],
            ["documentTypeId", "Type", this.state.documentTypes],
            ["correspondentId", "From", this.state.correspondents],
        ]) {
            if (this.state[key]) {
                facets.push({
                    key,
                    label: `${label}: ${
                        find(items, this.state[key]) || this.state[key]
                    }`,
                });
            }
        }
        if (this.state.linkedRecord) {
            const link = this.state.linkFacets.find(
                (item) => item.key === this.state.linkedRecord
            );
            facets.push({
                key: "linkedRecord",
                label: `Linked record: ${link?.label || "Current record"}`,
            });
        } else if (this.state.linkedState) {
            facets.push({
                key: "linkedState",
                label:
                    this.state.linkedState === "linked"
                        ? "Has linked record"
                        : "No linked record",
            });
        }
        if (this.state.mappedPartnerId) {
            facets.push({
                key: "mappedPartnerId",
                label: "Correspondent mapped to this Contact",
            });
        }
        if (this.state.paperlessId) {
            facets.push({
                key: "paperlessId",
                label: `Archive ID: ${this.state.paperlessId}`,
            });
        }
        if (this.state.customFieldId && this.state.customFieldValue) {
            const field = this.state.customFields.find(
                (item) => item.id === Number(this.state.customFieldId)
            );
            facets.push({
                key: "customField",
                label: `${field?.name || "Additional detail"}: ${
                    this.state.customFieldValue
                }`,
            });
        }
        for (const [key, label] of Object.entries({
            source: "Source",
            confidentiality: "Privacy",
            reviewState: "Review",
            dateFrom: "Document date from",
            dateTo: "Document date to",
            addedFrom: "Added from",
            addedTo: "Added to",
        })) {
            if (this.state[key]) {
                facets.push({ key, label: `${label}: ${this.state[key]}` });
            }
        }
        return facets;
    }

    get searchSuggestions() {
        if (!this.state.searchFocused) {
            return [];
        }
        const input = this.state.searchInput.trim();
        const match = input.match(
            /(?:^|\s)(tag|type|from|company|review|privacy|id):([^\s]*)$/i
        );
        if (!match) {
            if (input) {
                return [];
            }
            return [
                { kind: "hint", label: "Tag", hint: "tag:" },
                { kind: "hint", label: "Correspondent", hint: "from:" },
                { kind: "hint", label: "Document type", hint: "type:" },
                { kind: "hint", label: "Company", hint: "company:" },
                { kind: "hint", label: "Archive ID", hint: "id:" },
            ];
        }
        const kind = match[1].toLowerCase();
        const query = match[2].toLowerCase();
        const catalogs = {
            tag: this.state.tags,
            type: this.state.documentTypes,
            from: this.state.correspondents,
            company: this.state.companies,
            review: [
                { id: "needs_attention", name: "Needs review" },
                { id: "classified", name: "Classified" },
                { id: "reviewed", name: "Reviewed" },
            ],
            privacy: [
                { id: "internal", name: "Internal" },
                { id: "accounting", name: "Accounting" },
                { id: "hr", name: "HR restricted" },
                { id: "private", name: "Private" },
            ],
            id: [],
        };
        if (kind === "id" && /^\d+$/.test(query)) {
            return [
                {
                    kind: "id",
                    item: { id: query, name: query },
                    label: `Archive document ${query}`,
                },
            ];
        }
        return (catalogs[kind] || [])
            .filter((item) => item.name.toLowerCase().includes(query))
            .slice(0, 10)
            .map((item) => ({ kind, item, label: item.name }));
    }

    get tagPickerResults() {
        const selected = new Set(
            (this.state.selected?.tags || []).map((tag) => tag.id)
        );
        const query = this.state.tagQuery.trim().toLowerCase();
        return this.state.tags
            .filter(
                (tag) =>
                    !selected.has(tag.id) &&
                    (!query || tag.name.toLowerCase().includes(query))
            )
            .slice(0, 20);
    }

    get currentVersion() {
        return this.state.selected?.versions?.find((version) => version.is_current);
    }

    get earlierVersions() {
        return (this.state.selected?.versions || []).filter(
            (version) => !version.is_current
        );
    }

    get customFieldInputType() {
        const field = this.state.customFields.find(
            (item) => String(item.id) === String(this.state.customFieldId)
        );
        return field?.data_type === "date" ? "date" : "text";
    }

    documentPreviewUrl(document) {
        const current = document?.versions?.find((version) => version.is_current);
        return (
            current?.preview_url ||
            `/usl_documents/${document.id}/preview`
        );
    }

    persistState() {
        const serialized = JSON.stringify({
            query: this.state.query,
            workspace: this.state.workspace,
            view: this.state.view,
            sort: this.state.sort,
            page: this.state.page,
            ...Object.fromEntries(
                Object.keys(FILTER_DEFAULTS).map((key) => [key, this.state[key]])
            ),
            scrollTop: this.listScroller.el?.scrollTop || 0,
            selectedDocumentId: this.state.selected?.id || null,
            selectedVersionId:
                this.state.selected?.selected_version_id || null,
        });
        browser.sessionStorage.setItem(this.storageKey, serialized);
        browser.sessionStorage.setItem(this.globalStorageKey, serialized);
    }

    navigationFilters() {
        return {
            query: this.state.query,
            workspace: this.state.workspace,
            view: this.state.view,
            sort: this.state.sort,
            page: this.state.page,
            ...Object.fromEntries(
                Object.keys(FILTER_DEFAULTS).map((key) => [key, this.state[key]])
            ),
        };
    }

    writeNavigationState(mode = "replace", documentId = null, versionId = null) {
        try {
            const url = new URL(browser.location.href);
            const setOrDelete = (key, value) =>
                value
                    ? url.searchParams.set(key, String(value))
                    : url.searchParams.delete(key);
            setOrDelete("usl_document", documentId);
            setOrDelete("usl_version", versionId);
            url.searchParams.set(
                "usl_filters",
                JSON.stringify(this.navigationFilters())
            );
            for (const key of ["domain", "groupBy", "orderBy"]) {
                url.searchParams.delete(key);
            }
            const nativeSearch = new URLSearchParams(
                this.searchModel.generateQueryString()
            );
            for (const [key, value] of nativeSearch.entries()) {
                url.searchParams.set(key, value);
            }
            const nextState = {
                ...router.urlToState(url),
                usl_document: documentId || undefined,
                usl_version: versionId || undefined,
                domain: url.searchParams.get("domain") || undefined,
                groupBy: url.searchParams.get("groupBy") || undefined,
                orderBy: url.searchParams.get("orderBy") || undefined,
                uslDocumentsWorkspace: true,
                uslDocumentId: documentId || null,
                uslVersionId: versionId || null,
                uslDocumentsRecordContext: this.recordContextKey,
                uslDocumentsReturnRecord: null,
            };
            if (mode === "push") {
                router.pushState(nextState, { sync: true });
            } else {
                router.replaceState(nextState, { sync: true });
            }
        } catch {
            // Session storage remains the fallback for older embedded browsers.
        }
    }

    replaceNavigationState() {
        this.writeNavigationState(
            "replace",
            this.state.selected?.id,
            this.state.selected?.selected_version_id
        );
    }

    ensureRecordReturnHistory() {
        if (
            !this.recordContext ||
            (browser.history.state?.uslDocumentsRecordContext ||
                browser.history.state?.nextState?.uslDocumentsRecordContext) ===
                this.recordContextKey
        ) {
            return;
        }
        try {
            const url = new URL(browser.location.href);
            const baseState = { ...(browser.history.state || {}) };
            const baseRouteState = {
                ...(baseState.nextState || router.urlToState(url)),
            };
            browser.history.replaceState(
                {
                    ...baseState,
                    nextState: {
                        ...baseRouteState,
                        uslDocumentsWorkspace: false,
                        uslDocumentsReturnRecord: {
                            resModel: this.recordContext.resModel,
                            resId: this.recordContext.resId,
                        },
                    },
                    skipRouteChange: true,
                    uslDocumentsWorkspace: false,
                    uslDocumentsReturnRecord: {
                        resModel: this.recordContext.resModel,
                        resId: this.recordContext.resId,
                    },
                },
                "",
                url
            );
            browser.history.pushState(
                {
                    ...baseState,
                    nextState: {
                        ...baseRouteState,
                        uslDocumentsWorkspace: true,
                        uslDocumentId: this.state.selected?.id || null,
                        uslVersionId:
                            this.state.selected?.selected_version_id || null,
                        uslDocumentsRecordContext: this.recordContextKey,
                        uslDocumentsReturnRecord: null,
                    },
                    skipRouteChange: true,
                    uslDocumentsWorkspace: true,
                    uslDocumentId: this.state.selected?.id || null,
                    uslVersionId:
                        this.state.selected?.selected_version_id || null,
                    uslDocumentsRecordContext: this.recordContextKey,
                    uslDocumentsReturnRecord: null,
                },
                "",
                url
            );
        } catch {
            // Odoo breadcrumbs remain available if a browser blocks History API writes.
        }
    }

    clearDetailState() {
        this.state.selected = null;
        this.state.editingMetadata = false;
        this.state.tagPickerOpen = false;
    }

    requestHistoryBack() {
        if (this.closeHistoryTimer) {
            browser.clearTimeout(this.closeHistoryTimer);
        }
        this.closeHistoryTimer = browser.setTimeout(() => {
            this.closeHistoryTimer = null;
            browser.history.back();
        }, 0);
    }

    cancelHistoryBack() {
        if (this.closeHistoryTimer) {
            browser.clearTimeout(this.closeHistoryTimer);
            this.closeHistoryTimer = null;
        }
        if (this.closeCanonicalTimer) {
            browser.clearTimeout(this.closeCanonicalTimer);
            this.closeCanonicalTimer = null;
        }
    }

    scheduleCanonicalClose() {
        if (this.closeCanonicalTimer) {
            browser.clearTimeout(this.closeCanonicalTimer);
        }
        this.closeCanonicalTimer = browser.setTimeout(() => {
            this.closeCanonicalTimer = null;
            const urlState = this.readUrlState();
            if (!this.state.selected && urlState.documentId) {
                // Odoo can leave the previous list entry carrying the URL of
                // the detail entry. Rewrite that reached entry as the list;
                // the untouched forward entry can still reopen the document.
                this.closingDetail = false;
                this.hasLocalListHistory = false;
                this.writeNavigationState("replace");
                this.persistState();
                this.restoreScroll();
            }
        }, 150);
    }

    restoreScroll() {
        let restored = {};
        try {
            restored = JSON.parse(
                browser.sessionStorage.getItem(this.storageKey) || "{}"
            );
        } catch {
            restored = {};
        }
        browser.requestAnimationFrame(() => {
            if (this.listScroller.el && Number.isFinite(restored.scrollTop)) {
                this.listScroller.el.scrollTop = restored.scrollTop;
            }
        });
    }

    async handlePopState(event) {
        const historyState = event.state || {};
        const routedState = historyState.nextState || {};
        const urlState = this.readUrlState();
        const targetsDocument = Boolean(
            Number(
                historyState.uslDocumentId ||
                    routedState.uslDocumentId ||
                    routedState.usl_document ||
                    urlState.documentId
            )
        );
        let returnRecord =
            historyState.uslDocumentsReturnRecord ||
            routedState.uslDocumentsReturnRecord;
        if (
            !returnRecord &&
            this.recordContext &&
            !this.state.selected &&
            !this.closingDetail &&
            !targetsDocument
        ) {
            // The host router may retain the record URL while replacing the
            // custom return marker with its normalized action state. At the
            // record-context list level, Back still means return to the record.
            returnRecord = this.recordContext;
        }
        if (returnRecord) {
            this.closingDetail = false;
            await this.action.doAction(
                {
                    type: "ir.actions.act_window",
                    res_model: returnRecord.resModel,
                    res_id: Number(returnRecord.resId),
                    views: [[false, "form"]],
                    target: "current",
                },
                { clearBreadcrumbs: true }
            );
            return;
        }
        const isDocumentsState =
            historyState.uslDocumentsWorkspace ||
            routedState.uslDocumentsWorkspace ||
            Boolean(urlState.filters || urlState.documentId);
        if (!isDocumentsState) {
            this.closingDetail = false;
            return;
        }
        const hasOwn = (object, key) =>
            Object.prototype.hasOwnProperty.call(object, key);
        const rawDocumentId = hasOwn(historyState, "uslDocumentId")
            ? historyState.uslDocumentId
            : hasOwn(routedState, "uslDocumentId")
              ? routedState.uslDocumentId
              : hasOwn(routedState, "usl_document")
                ? routedState.usl_document
              : urlState.documentId;
        const rawVersionId = hasOwn(historyState, "uslVersionId")
            ? historyState.uslVersionId
            : hasOwn(routedState, "uslVersionId")
              ? routedState.uslVersionId
              : hasOwn(routedState, "usl_version")
                ? routedState.usl_version
              : urlState.versionId;
        const documentId = Number(rawDocumentId) || null;
        const versionId = rawVersionId || null;
        const isDuplicateOpenEntry =
            documentId &&
            documentId === this.state.selected?.id &&
            (!versionId ||
                String(versionId) ===
                    String(this.state.selected?.selected_version_id || ""));
        if ((this.closingDetail || isDuplicateOpenEntry) && documentId) {
            // If Odoo normalized the same detail route into two adjacent
            // entries, one Back action must still close the panel. Canonicalize
            // the reached duplicate as the list entry instead of consuming a
            // second browser-history event.
            this.cancelHistoryBack();
            this.closingDetail = false;
            this.hasLocalListHistory = false;
            this.clearDetailState();
            this.writeNavigationState("replace");
            this.persistState();
            this.restoreScroll();
            return;
        }
        if (!documentId) {
            this.cancelHistoryBack();
            this.closingDetail = false;
            this.hasLocalListHistory = false;
            this.clearDetailState();
            // Odoo can preserve its own route state while retaining the URL
            // from a later detail entry. Canonicalize the list entry so reload
            // stays closed while the untouched forward entry can reopen it.
            this.writeNavigationState("replace");
            this.persistState();
            this.restoreScroll();
            return;
        }
        this.closingDetail = false;
        this.hasLocalListHistory = true;
        await this.openDocumentById(documentId, versionId);
    }

    workspaceKwargs() {
        const linkedRecordIsActive =
            this.recordContext &&
            this.domainContains(
                this.searchModel.domain,
                "linked_record_ref",
                this.recordContextKey
            );
        return {
            query: "",
            workspace: this.state.workspace,
            page: this.state.page,
            page_size: this.state.pageSize,
            sort: this.state.sort,
            search_domain: this.searchModel.domain,
            // Legacy state is migrated into the native SearchModel before the
            // second load. Never apply a second hidden tag condition.
            shortcut_tag_ids: [],
            group_by: this.searchModel.groupBy,
            linked_model: linkedRecordIsActive
                ? this.recordContext.resModel
                : null,
            linked_id: linkedRecordIsActive ? this.recordContext.resId : null,
            mapped_partner_id:
                linkedRecordIsActive &&
                this.recordContext.resModel === "res.partner"
                    ? this.recordContext.resId
                    : null,
        };
    }

    domainContains(domain, fieldName, value) {
        if (!Array.isArray(domain)) {
            return false;
        }
        if (
            domain.length >= 3 &&
            domain[0] === fieldName &&
            domain[1] === "=" &&
            String(domain[2]) === String(value)
        ) {
            return true;
        }
        return domain.some(
            (item) =>
                Array.isArray(item) &&
                this.domainContains(item, fieldName, value)
        );
    }

    async onNativeSearchUpdate() {
        if (!this.searchReady) {
            return;
        }
        this.state.tagIds = this.activeTagShortcutIds();
        this.state.page = 1;
        this.state.selected = null;
        await this.load();
    }

    migrateLegacyTagFilters() {
        const tagIds = [...this.state.tagIds];
        if (!tagIds.length) {
            return false;
        }
        const matchingFacet = this.searchModel.facets.find((facet) => {
            if (!facet.domain) {
                return false;
            }
            try {
                const facetDomain = new Domain(facet.domain).toList();
                const leaves = this.domainLeaves(facetDomain);
                const facetIds = this.tagIdsFromDomain(facetDomain);
                return (
                    leaves.length === 1 &&
                    facetIds.length === tagIds.length &&
                    facetIds.every((tagId) => tagIds.includes(tagId))
                );
            } catch {
                return false;
            }
        });
        if (matchingFacet) {
            for (const queryItem of this.searchModel.query) {
                const searchItem =
                    this.searchModel.searchItems[queryItem.searchItemId];
                if (searchItem?.groupId === matchingFacet.groupId) {
                    searchItem.uslTagShortcutIds = tagIds;
                }
            }
            return false;
        }
        this.state.tagIds = [];
        this.replaceTagSearchFilters(tagIds);
        this.persistState();
        this.replaceNavigationState();
        return true;
    }

    domainLeaves(domain) {
        if (!Array.isArray(domain)) {
            return [];
        }
        if (
            domain.length >= 3 &&
            typeof domain[0] === "string" &&
            typeof domain[1] === "string"
        ) {
            return [domain];
        }
        return domain.flatMap((item) =>
            Array.isArray(item) ? this.domainLeaves(item) : []
        );
    }

    tagIdsFromDomain(domain) {
        return [
            ...new Set(
                this.domainLeaves(domain).flatMap((leaf) =>
                    leaf[0] === "tag_ids" &&
                    leaf[1] === "in" &&
                    Array.isArray(leaf[2])
                        ? leaf[2].map(Number).filter(Boolean)
                        : []
                )
            ),
        ];
    }

    async load() {
        this.state.loading = true;
        this.state.error = "";
        try {
            const result = await this.orm.call(
                "usl.document",
                "workspace_data",
                [],
                this.workspaceKwargs()
            );
            this.state.documents = result.documents;
            this.state.count = result.count;
            this.state.degraded = result.degraded;
            this.state.smartViews = result.smart_views || this.state.smartViews;
            this.state.tags = result.tags || this.state.tags;
            this.state.correspondents =
                result.correspondents || this.state.correspondents;
            this.state.documentTypes =
                result.document_types || this.state.documentTypes;
            this.state.companies = result.companies || this.state.companies;
            this.state.customFields =
                result.custom_fields || this.state.customFields;
            this.state.linkFacets = result.link_facets || this.state.linkFacets;
            this.state.canUpload = Boolean(result.can_upload);
            this.state.failedOperations = result.failed_operations || [];
            if (!this.state.operation && result.active_operation) {
                this.state.operation = result.active_operation;
                this.pollOperation(result.active_operation.id);
            }
            this.state.truncated = Boolean(result.truncated);
            this.state.error = result.error || "";
            this.state.workspace = result.selected_workspace || this.state.workspace;
            if (this.state.selected) {
                const refreshed = result.documents.find(
                    (item) => item.id === this.state.selected.id
                );
                if (refreshed) {
                    this.state.selected = { ...this.state.selected, ...refreshed };
                }
            }
        } catch (error) {
            this.state.degraded = true;
            this.state.error =
                error.data?.message ||
                error.message ||
                "The archive could not be loaded.";
        } finally {
            this.state.loading = false;
            this.persistState();
            this.replaceNavigationState();
        }
    }

    selectWorkspace(view) {
        this.state.workspace = view.key;
        this.state.page = 1;
        this.state.selected = null;
        return this.load();
    }

    applySavedFilters(filters) {
        this.state.query = filters.query || "";
        this.state.sort = filters.sort || "recent";
        for (const [key, defaultValue] of Object.entries(FILTER_DEFAULTS)) {
            const serializedKey =
                {
                    companyId: "company_id",
                    tagIds: "tag_ids",
                    correspondentId: "correspondent_id",
                    documentTypeId: "document_type_id",
                    dateFrom: "date_from",
                    dateTo: "date_to",
                    addedFrom: "added_from",
                    addedTo: "added_to",
                    reviewState: "review_state",
                    linkedState: "linked_state",
                    linkedRecord: "linked_record",
                }[key] || key;
            this.state[key] =
                filters[serializedKey] === undefined
                    ? defaultValue
                    : key === "tagIds"
                    ? filters[serializedKey].map(Number)
                    : String(filters[serializedKey] || "");
        }
    }

    search(event) {
        event.preventDefault();
        this.state.query = this.state.searchInput.trim();
        this.state.searchFocused = false;
        this.state.page = 1;
        return this.load();
    }

    onSearchInput(event) {
        this.state.searchInput = event.target.value;
        this.state.searchFocused = true;
    }

    clearSearch() {
        this.state.query = "";
        this.state.searchInput = "";
        this.state.page = 1;
        return this.load();
    }

    clearFilters() {
        for (const [key, value] of Object.entries(FILTER_DEFAULTS)) {
            this.state[key] = Array.isArray(value) ? [] : value;
        }
        this.state.page = 1;
        return this.load();
    }

    clearAll() {
        this.state.tagIds = [];
        this.searchModel.clearQuery();
    }

    updateFilter(field, event) {
        this.state[field] = event.target.value;
        this.state.page = 1;
        return this.load();
    }

    selectCustomField(event) {
        this.state.customFieldId = event.target.value;
        this.state.page = 1;
        if (this.state.customFieldValue) {
            this.customFieldFilterApplied = true;
            return this.load();
        }
    }

    scheduleCustomFieldFilter(event) {
        this.state.customFieldValue = event.target.value;
        this.state.page = 1;
        if (this.customFieldFilterTimer) {
            browser.clearTimeout(this.customFieldFilterTimer);
        }
        if (!this.state.customFieldValue) {
            this.customFieldFilterTimer = null;
            if (!this.customFieldFilterApplied) {
                return;
            }
            this.customFieldFilterApplied = false;
            return this.load();
        }
        if (!this.state.customFieldId) {
            return;
        }
        this.customFieldFilterTimer = browser.setTimeout(() => {
            this.customFieldFilterTimer = null;
            this.customFieldFilterApplied = true;
            this.load();
        }, 300);
    }

    applyCustomFieldFilter(event) {
        if (event.key !== "Enter" || !this.state.customFieldId) {
            return;
        }
        event.preventDefault();
        if (this.customFieldFilterTimer) {
            browser.clearTimeout(this.customFieldFilterTimer);
            this.customFieldFilterTimer = null;
        }
        this.customFieldFilterApplied = true;
        return this.load();
    }

    applySearchSuggestion(suggestion) {
        if (suggestion.kind === "hint") {
            this.state.searchInput = `${this.state.searchInput}${suggestion.hint}`;
            return;
        }
        const fields = {
            tag: "tagIds",
            type: "documentTypeId",
            from: "correspondentId",
            company: "companyId",
            review: "reviewState",
            privacy: "confidentiality",
            id: "paperlessId",
        };
        const field = fields[suggestion.kind];
        if (field === "tagIds") {
            const tagId = Number(suggestion.item.id);
            this.state.searchInput = this.state.query;
            this.state.searchFocused = false;
            this.state.page = 1;
            return this.replaceTagSearchFilters([
                ...this.activeTagShortcutIds(),
                tagId,
            ]);
        } else {
            this.state[field] = String(suggestion.item.id);
        }
        this.state.searchInput = this.state.query;
        this.state.searchFocused = false;
        this.state.page = 1;
        return this.load();
    }

    removeFacet(key) {
        if (key === "customField") {
            this.state.customFieldId = "";
            this.state.customFieldValue = "";
            this.state.page = 1;
            return this.load();
        }
        if (key.startsWith("tag:")) {
            const tagId = Number(key.split(":", 2)[1]);
            this.state.tagIds = this.state.tagIds.filter((id) => id !== tagId);
        } else {
            this.state[key] = Array.isArray(FILTER_DEFAULTS[key])
                ? []
                : FILTER_DEFAULTS[key] ?? "";
        }
        this.state.page = 1;
        return this.load();
    }

    toggleLinkedFilter() {
        this.state.linkedState =
            this.state.linkedState === "linked" ? "" : "linked";
        this.state.page = 1;
        return this.load();
    }

    tagShortcutSearchItem(tag) {
        return this.activeTagShortcutItems().find((item) =>
            (item.uslTagShortcutIds || [item.uslTagShortcutId]).includes(tag.id)
        );
    }

    activeTagShortcutItems() {
        const activeIds = new Set(
            this.searchModel.query.map((item) => item.searchItemId)
        );
        return Object.values(this.searchModel.searchItems).filter(
            (item) =>
                activeIds.has(item.id) &&
                (item.uslTagShortcutId ||
                    (item.uslTagShortcutIds || []).length)
        );
    }

    activeTagShortcutIds() {
        return [
            ...new Set(
                this.activeTagShortcutItems().flatMap((item) =>
                    (item.uslTagShortcutIds || [item.uslTagShortcutId])
                        .map(Number)
                        .filter(Boolean)
                )
            ),
        ];
    }

    isTagShortcutActive(tag) {
        return Boolean(this.tagShortcutSearchItem(tag));
    }

    replaceTagSearchFilters(tagIds) {
        const normalizedIds = [...new Set(tagIds.map(Number).filter(Boolean))];
        this.state.tagIds = normalizedIds;
        const activeItems = this.activeTagShortcutItems();
        const groupIds = [...new Set(activeItems.map((item) => item.groupId))];
        if (groupIds.length) {
            this.searchModel.blockNotification = true;
            for (const groupId of groupIds) {
                this.searchModel.deactivateGroup(groupId);
            }
            this.searchModel.blockNotification = false;
        }
        if (normalizedIds.length) {
            const names = normalizedIds.map(
                (tagId) =>
                    this.state.tags.find((tag) => tag.id === tagId)?.name ||
                    `Tag ${tagId}`
            );
            this.searchModel.createNewFilters([
                {
                    description:
                        normalizedIds.length === 1
                            ? `Tag: ${names[0]}`
                            : `Tags: ${names.join(" or ")}`,
                    domain: [["tag_ids", "in", normalizedIds]],
                    uslTagShortcutIds: normalizedIds,
                },
            ]);
        } else if (groupIds.length) {
            this.searchModel._notify();
        }
    }

    toggleTagFilter(tag) {
        const selected = new Set(this.activeTagShortcutIds());
        if (selected.has(tag.id)) {
            selected.delete(tag.id);
        } else {
            selected.add(tag.id);
        }
        this.replaceTagSearchFilters([...selected]);
    }

    onTagShortcutSearch(event) {
        this.state.tagShortcutQuery = event.target.value;
    }

    isSmartShortcutActive(shortcut) {
        if (shortcut.kind === "group") {
            return this.searchModel.groupBy.includes(shortcut.group_by);
        }
        return Object.values(this.searchModel.searchItems).some(
            (item) =>
                item.uslShortcutKey === shortcut.key &&
                this.searchModel.query.some(
                    (query) => query.searchItemId === item.id
                )
        );
    }

    toggleSmartShortcut(shortcut) {
        if (shortcut.kind === "group") {
            const [fieldName, interval] = shortcut.group_by.split(":");
            const groupItem = Object.values(this.searchModel.searchItems).find(
                (item) =>
                    ["groupBy", "dateGroupBy"].includes(item.type) &&
                    item.fieldName === fieldName
            );
            if (groupItem) {
                if (groupItem.type === "dateGroupBy") {
                    this.searchModel.toggleDateGroupBy(
                        groupItem.id,
                        interval || groupItem.defaultIntervalId
                    );
                } else {
                    this.searchModel.toggleSearchItem(groupItem.id);
                }
            } else {
                this.searchModel.createNewGroupBy(fieldName, { interval });
            }
            return;
        }
        const existing = Object.values(this.searchModel.searchItems).find(
            (item) => item.uslShortcutKey === shortcut.key
        );
        if (existing) {
            this.searchModel.toggleSearchItem(existing.id);
            return;
        }
        this.searchModel.createNewFilters([
            {
                description: shortcut.name,
                domain: shortcut.domain,
                uslShortcutKey: shortcut.key,
            },
        ]);
    }

    async savePersonalView() {
        this.state.savingView = true;
        try {
            const filters = {
                query: this.state.query,
                sort: this.state.sort,
                company_id: this.state.companyId,
                tag_ids: this.activeTagShortcutIds(),
                correspondent_id: this.state.correspondentId,
                document_type_id: this.state.documentTypeId,
                date_from: this.state.dateFrom,
                date_to: this.state.dateTo,
                added_from: this.state.addedFrom,
                added_to: this.state.addedTo,
                source: this.state.source,
                confidentiality: this.state.confidentiality,
                review_state: this.state.reviewState,
                linked_state: this.state.linkedState,
                linked_record: this.state.linkedRecord,
            };
            const view = await this.orm.call(
                "usl.document.smart.view",
                "save_personal_view",
                [this.state.savedViewName, filters]
            );
            this.state.smartViews.push(view);
            this.state.savedViewName = "";
            this.notification.add("View saved for you.", { type: "success" });
        } catch (error) {
            this.notification.add(
                error.data?.message || error.message || "The view could not be saved.",
                { type: "danger" }
            );
        } finally {
            this.state.savingView = false;
        }
    }

    async removePersonalView(view, event) {
        event.stopPropagation();
        await this.orm.unlink("usl.document.smart.view", [view.id]);
        this.state.smartViews = this.state.smartViews.filter(
            (item) => item.id !== view.id
        );
        if (this.state.workspace === view.key) {
            this.state.workspace = "recent";
            await this.load();
        }
    }

    async select(document) {
        this.persistState();
        this.closingDetail = false;
        this.hasLocalListHistory = true;
        this.writeNavigationState("push", document.id);
        await this.openDocumentById(document.id);
    }

    async openDocumentById(documentId, versionId = null) {
        const document =
            this.state.documents.find((item) => item.id === Number(documentId)) ||
            (this.state.selected?.id === Number(documentId)
                ? this.state.selected
                : {
                      id: Number(documentId),
                      name: "Document",
                      tags: [],
                      links: [],
                      versions: [],
                      can_edit: false,
                      can_restore: false,
                      can_manage: false,
                  });
        this.state.selected = {
            ...document,
            preview_url: `/usl_documents/${document.id}/preview`,
        };
        this.state.selectedLoading = true;
        this.state.editingMetadata = false;
        try {
            const detail = await this.orm.call(
                "usl.document",
                "document_detail",
                [document.id],
                { check_archive: true }
            );
            if (this.state.selected?.id === document.id) {
                this.state.selected = {
                    ...detail,
                    preview_url: this.documentPreviewUrl(detail),
                };
                this.state.degraded = detail.archive_available === false;
                if (versionId) {
                    const version = detail.versions?.find(
                        (item) => item.paperless_version_id === String(versionId)
                    );
                    if (version) {
                        this.state.selected.preview_url = version.preview_url;
                        this.state.selected.selected_version_id =
                            version.paperless_version_id;
                    }
                }
                this.persistState();
            }
        } catch (error) {
            this.notification.add(
                error.data?.message ||
                    error.message ||
                    "Document details could not be loaded.",
                { type: "danger", sticky: true }
            );
        } finally {
            this.state.selectedLoading = false;
        }
    }

    retryDocumentDetail() {
        if (this.state.selected) {
            return this.openDocumentById(
                this.state.selected.id,
                this.state.selected.selected_version_id
            );
        }
    }

    onDocumentKeydown(event, document) {
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            return this.select(document);
        }
    }

    closeDetail() {
        this.clearDetailState();
        this.persistState();
        if (this.hasLocalListHistory) {
            this.closingDetail = true;
            this.requestHistoryBack();
            this.scheduleCanonicalClose();
        } else {
            this.writeNavigationState("replace");
            this.restoreScroll();
        }
    }

    beginMetadataEdit() {
        const selected = this.state.selected;
        this.state.metadataDraft = {
            name: selected.name || "",
            document_date: selected.date || "",
            correspondent_id: selected.correspondent_id || false,
            document_type_id: selected.document_type_id || false,
        };
        this.state.editingMetadata = true;
    }

    cancelMetadataEdit() {
        this.state.editingMetadata = false;
        this.state.metadataDraft = null;
    }

    updateMetadataDraft(field, event) {
        this.state.metadataDraft[field] =
            ["correspondent_id", "document_type_id"].includes(field)
                ? Number(event.target.value) || false
                : event.target.value;
    }

    async setSelectedTags(tagIds) {
        if (!this.state.selected || this.state.savingMetadata) {
            return;
        }
        this.state.savingMetadata = true;
        try {
            const detail = await this.orm.call(
                "usl.document",
                "update_archive_metadata",
                [[this.state.selected.id], { tag_ids: tagIds }]
            );
            this.state.selected = {
                ...detail,
                preview_url: this.documentPreviewUrl(detail),
            };
            this.state.tagQuery = "";
            await this.load();
        } catch (error) {
            this.notification.add(
                error.data?.message ||
                    error.message ||
                    "Tags could not be updated. No change was kept.",
                { type: "danger", sticky: true }
            );
        } finally {
            this.state.savingMetadata = false;
        }
    }

    addSelectedTag(tag) {
        const ids = (this.state.selected.tags || []).map((item) => item.id);
        if (!ids.includes(tag.id)) {
            return this.setSelectedTags([...ids, tag.id]);
        }
    }

    removeSelectedTag(tagId) {
        return this.setSelectedTags(
            (this.state.selected.tags || [])
                .map((tag) => tag.id)
                .filter((id) => id !== tagId)
        );
    }

    async createAndAddTag() {
        const name = this.state.tagQuery.trim();
        if (!name || this.state.creatingTag) {
            return;
        }
        const existing = this.state.tags.find(
            (tag) => tag.name.toLowerCase() === name.toLowerCase()
        );
        if (existing) {
            return this.addSelectedTag(existing);
        }
        this.state.creatingTag = true;
        try {
            const [tagId] = await this.orm.create("usl.paperless.tag", [
                {
                    name,
                    color: "#4f6fad",
                    matching_algorithm: "0",
                    is_insensitive: true,
                },
            ]);
            const tag = {
                id: tagId,
                name,
                color: "#4f6fad",
                text_color: "#ffffff",
            };
            this.state.tags = [...this.state.tags, tag];
            await this.addSelectedTag(tag);
            this.notification.add(`Tag “${name}” created and added.`, {
                type: "success",
            });
        } catch (error) {
            this.notification.add(
                error.data?.message || error.message || "The tag could not be created.",
                { type: "danger", sticky: true }
            );
        } finally {
            this.state.creatingTag = false;
        }
    }

    async saveMetadata() {
        this.state.savingMetadata = true;
        try {
            const detail = await this.orm.call(
                "usl.document",
                "update_archive_metadata",
                [[this.state.selected.id], this.state.metadataDraft]
            );
            this.state.selected = {
                ...detail,
                preview_url: this.documentPreviewUrl(detail),
            };
            this.state.editingMetadata = false;
            this.notification.add("Document updated.", { type: "success" });
            await this.load();
        } catch (error) {
            this.notification.add(
                error.data?.message ||
                    error.message ||
                    "The document could not be updated.",
                { type: "danger", sticky: true }
            );
        } finally {
            this.state.savingMetadata = false;
        }
    }

    selectVersion(version) {
        this.state.selected.preview_url = version.preview_url;
        this.state.selected.selected_version_id = version.paperless_version_id;
        this.writeNavigationState(
            "replace",
            this.state.selected.id,
            version.paperless_version_id
        );
        this.persistState();
    }

    restoreVersion(version) {
        this.dialog.add(ConfirmationDialog, {
            title: "Restore this file?",
            body:
                "This file will become current as a new version. Nothing will be deleted.",
            confirmLabel: "Restore as current",
            confirm: async () => {
                try {
                    const result = await this.orm.call(
                        "usl.document",
                        "restore_version",
                        [[this.state.selected.id], version.paperless_version_id]
                    );
                    this.state.operation = {
                        name: version.filename || version.label || "Document version",
                        ...result,
                    };
                    this.notification.add(result.message, { type: "info" });
                    await this.pollOperation(result.operation_id);
                } catch (error) {
                    this.notification.add(
                        error.data?.message ||
                            error.message ||
                            "The version could not be restored.",
                        { type: "danger", sticky: true }
                    );
                }
            },
        });
    }

    restoreFromTrash() {
        this.dialog.add(ConfirmationDialog, {
            title: "Restore this document?",
            body: "The same archived document and all of its Odoo links will return.",
            confirmLabel: "Restore",
            confirm: async () => {
                try {
                    const result = await this.orm.call(
                        "usl.document",
                        "restore_from_trash",
                        [[this.state.selected.id]]
                    );
                    this.notification.add(result.message, { type: "success" });
                    await this.load();
                    await this.openDocumentById(result.document_id);
                } catch (error) {
                    this.notification.add(
                        error.data?.message ||
                            error.message ||
                            "The document could not be restored.",
                        { type: "danger", sticky: true }
                    );
                }
            },
        });
    }

    moveToTrash() {
        const links = this.state.selected?.link_count || 0;
        this.dialog.add(ConfirmationDialog, {
            title: "Move this document to Trash?",
            body: links
                ? `Its ${links} Odoo link${
                      links === 1 ? "" : "s"
                  } will remain visible. The document cannot be permanently deleted while linked.`
                : "The document can be restored later. Permanent deletion is a separate administrator action.",
            confirmLabel: "Move to Trash",
            confirmClass: "btn-danger",
            confirm: async () => {
                try {
                    const result = await this.orm.call(
                        "usl.document",
                        "move_to_trash",
                        [[this.state.selected.id]]
                    );
                    this.notification.add(result.message, { type: "success" });
                    await this.load();
                    await this.openDocumentById(result.document_id);
                } catch (error) {
                    this.notification.add(
                        error.data?.message ||
                            error.message ||
                            "The document could not be moved to Trash.",
                        { type: "danger", sticky: true }
                    );
                }
            },
        });
    }

    requestPermanentDelete() {
        const selected = this.state.selected;
        if (!selected || selected.permanent_delete_blocker) {
            return;
        }
        this.dialog.add(PermanentDeleteDialog, {
            documentName: selected.name,
            confirm: async (reason) => {
                try {
                    await this.orm.call(
                        "usl.document",
                        "approve_permanent_deletion",
                        [[selected.id], reason]
                    );
                    await this.orm.call(
                        "usl.document",
                        "permanently_delete_from_trash",
                        [[selected.id]]
                    );
                    this.state.selected = null;
                    this.notification.add(
                        "Document permanently deleted. An audit tombstone remains in Odoo.",
                        { type: "success", sticky: true }
                    );
                    await this.load();
                } catch (error) {
                    this.notification.add(
                        error.data?.message ||
                            error.message ||
                            "Permanent deletion failed.",
                        { type: "danger", sticky: true }
                    );
                    throw error;
                }
            },
        });
    }

    openRecord(document) {
        this.persistState();
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "usl.document",
            res_id: document.id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    previousPage() {
        if (this.state.page > 1) {
            this.state.page -= 1;
            return this.load();
        }
    }

    nextPage() {
        if (this.state.page * this.state.pageSize < this.state.count) {
            this.state.page += 1;
            return this.load();
        }
    }

    setView(view) {
        this.state.view = view;
        this.persistState();
    }

    onDragOver(event) {
        if (!this.state.canUpload) {
            return;
        }
        event.preventDefault();
        this.state.dragged = true;
    }

    onDragLeave() {
        this.state.dragged = false;
    }

    onDrop(event) {
        event.preventDefault();
        this.state.dragged = false;
        if (!this.state.canUpload) {
            return;
        }
        const file = event.dataTransfer.files?.[0];
        if (file) {
            return this.upload(file);
        }
    }

    onFileChange(event) {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (file) {
            return this.upload(file);
        }
    }

    onVersionFileChange(event) {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (file) {
            return this.uploadVersion(file);
        }
    }

    async uploadVersion(file) {
        if (!this.state.selected) {
            return;
        }
        this.state.uploading = true;
        this.state.operation = { name: file.name, state: "uploading" };
        try {
            const content = await this.fileAsBase64(file);
            const result = await this.orm.call("usl.document", "upload_new_version", [
                [this.state.selected.id],
                file.name,
                content,
                file.type || "application/octet-stream",
                file.name,
            ]);
            this.state.operation = { name: file.name, ...result };
            this.notification.add(result.message, {
                type: result.state === "duplicate" ? "warning" : "info",
            });
            if (result.state === "processing") {
                await this.pollOperation(result.operation_id);
            } else {
                await this.openDocumentById(this.state.selected.id);
            }
        } catch (error) {
            const message =
                error.data?.message || error.message || "Version upload failed.";
            this.state.operation = {
                name: file.name,
                state: "failed",
                error: message,
            };
            this.notification.add(message, { type: "danger", sticky: true });
        } finally {
            this.state.uploading = false;
        }
    }

    async upload(file) {
        if (!this.state.canUpload) {
            return;
        }
        this.state.uploading = true;
        this.state.operation = { name: file.name, state: "uploading" };
        try {
            const content = await this.fileAsBase64(file);
            const result = await this.orm.call(
                "usl.document",
                "upload_from_odoo",
                [
                    file.name,
                    content,
                    file.type || "application/octet-stream",
                ],
                this.recordContext
                    ? {
                          res_model: this.recordContext.resModel,
                          res_id: this.recordContext.resId,
                      }
                    : {}
            );
            this.state.operation = { name: file.name, ...result };
            if (result.state === "duplicate") {
                this.notification.add(result.message, { type: "warning" });
                await this.load();
            } else {
                this.notification.add(result.message, { type: "info" });
                await this.pollOperation(result.operation_id);
            }
        } catch (error) {
            const message =
                error.data?.message || error.message || "Upload failed.";
            this.state.operation = {
                name: file.name,
                state: "failed",
                error: message,
            };
            this.notification.add(message, { type: "danger", sticky: true });
        } finally {
            this.state.uploading = false;
        }
    }

    async linkSelected() {
        if (!this.recordContext || !this.state.selected) {
            return;
        }
        try {
            await this.orm.call("usl.document", "link_to_record", [
                [this.state.selected.id],
                this.recordContext.resModel,
                this.recordContext.resId,
            ]);
            this.notification.add("Document linked to this record.", {
                type: "success",
            });
            await this.load();
            await this.openDocumentById(this.state.selected.id);
        } catch (error) {
            this.notification.add(
                error.data?.message ||
                    error.message ||
                    "The document could not be linked.",
                { type: "danger", sticky: true }
            );
        }
    }

    async unlinkCurrent() {
        if (!this.recordContext || !this.state.selected) {
            return;
        }
        try {
            await this.orm.call("usl.document", "unlink_from_record", [
                [this.state.selected.id],
                this.recordContext.resModel,
                this.recordContext.resId,
            ]);
            this.notification.add(
                "Link removed. The archived document was not deleted.",
                { type: "success" }
            );
            await this.load();
            await this.openDocumentById(this.state.selected.id);
        } catch (error) {
            this.notification.add(
                error.data?.message ||
                    error.message ||
                    "The link could not be removed.",
                { type: "danger", sticky: true }
            );
        }
    }

    async openLink(link) {
        this.persistState();
        const action = await this.orm.call(
            "usl.document",
            "action_open_linked_record",
            [[this.state.selected.id], link.id]
        );
        return this.action.doAction(action);
    }

    isLinkedToCurrent() {
        return Boolean(
            this.recordContext &&
                this.state.selected?.links?.some(
                    (link) =>
                        link.model === this.recordContext.resModel &&
                        link.res_id === this.recordContext.resId
                )
        );
    }

    fileAsBase64(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onerror = reject;
            reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
            reader.readAsDataURL(file);
        });
    }

    async pollOperation(operationId) {
        if (!operationId || this.pollingOperationIds.has(operationId)) {
            return;
        }
        this.pollingOperationIds.add(operationId);
        for (let attempt = 0; attempt < 90; attempt++) {
            const statuses = await this.orm.call(
                "usl.document.operation",
                "poll",
                [[operationId]]
            );
            const status = statuses[operationId];
            this.state.operation = {
                ...this.state.operation,
                ...status,
                name:
                    status.name ||
                    this.state.operation?.name ||
                    "Document",
            };
            if (status.state === "archived") {
                this.notification.add("Document archived successfully.", {
                    type: "success",
                });
                const selected = this.state.selected;
                await this.load();
                if (selected) {
                    await this.openDocumentById(selected.id);
                }
                this.state.operation = null;
                this.pollingOperationIds.delete(operationId);
                return;
            }
            if (status.state === "failed") {
                this.notification.add(
                    status.error || "Paperless processing failed.",
                    { type: "danger", sticky: true }
                );
                await this.load();
                this.pollingOperationIds.delete(operationId);
                return;
            }
            await new Promise((resolve) => setTimeout(resolve, 2000));
        }
        this.notification.add(
            "Processing is taking longer than usual. You can leave this page; "
                + "the status will be restored when you return.",
            { type: "info" }
        );
        this.pollingOperationIds.delete(operationId);
    }

    async dismissOperation(operation) {
        await this.orm.call(
            "usl.document.operation",
            "acknowledge",
            [[operation.id]]
        );
        this.state.failedOperations = this.state.failedOperations.filter(
            (item) => item.id !== operation.id
        );
    }
}

export class DocumentsWorkspace extends Component {
    static template = "usl_documents.DocumentsWorkspace";
    static components = { WithSearch, DocumentsWorkspaceView };
    static props = { ...standardActionServiceProps };

    setup() {
        // Client actions do not pass through ``View.setup()``, while
        // SearchModel intentionally relies on the same environment contract.
        // Providing Odoo's native defaults keeps the real workspace and
        // isolated OWL tests on that single supported path.
        useSubEnv({
            config: {
                ...getDefaultConfig(),
                ...(this.env.config || {}),
            },
        });
    }

    get withSearchProps() {
        const params = this.props.action.params || {};
        const dynamicFilters = [];
        if (params.linked_filter && params.res_model && params.res_id) {
            const reference = `${params.res_model}:${Number(params.res_id)}`;
            const linkedDomain = [
                ["linked_record_ref", "=", reference],
            ];
            dynamicFilters.push({
                description: `Linked record: ${
                    params.record_name || "current record"
                }`,
                domain:
                    params.res_model === "res.partner"
                        ? [
                              "|",
                              ["mapped_contact_id", "=", Number(params.res_id)],
                              ...linkedDomain,
                          ]
                        : linkedDomain,
            });
        }
        return {
            resModel: "usl.document",
            ...(params.search_view_arch
                ? {
                      searchViewArch: params.search_view_arch,
                      searchViewFields: params.search_view_fields || {},
                      irFilters: params.ir_filters || [],
                      loadIrFilters: false,
                  }
                : { searchViewId: false, loadIrFilters: true }),
            context: this.props.action.context || {},
            domain: [],
            dynamicFilters,
            searchMenuTypes: ["filter", "groupBy", "favorite"],
        };
    }
}

registry.category("actions").add(
    "usl_documents.workspace",
    DocumentsWorkspace
);
registry.category("fields").add("usl_open_documents", {
    component: OpenDocumentsField,
    supportedTypes: ["integer"],
});
