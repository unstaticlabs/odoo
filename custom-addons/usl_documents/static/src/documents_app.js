/** @odoo-module **/

import {
    Component,
    onMounted,
    onWillStart,
    onWillUnmount,
    useRef,
    useState,
} from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
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
};

export class DocumentsWorkspace extends Component {
    static template = "usl_documents.DocumentsWorkspace";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        const params = this.props.action.params || {};
        this.recordContext =
            params.res_model && params.res_id
                ? { resModel: params.res_model, resId: Number(params.res_id) }
                : null;
        this.listScroller = useRef("documentList");
        this.globalStorageKey = "usl_documents.workspace.global";
        this.storageKey = `usl_documents.workspace.${
            this.recordContext
                ? `${this.recordContext.resModel}.${this.recordContext.resId}`
                : "global"
        }`;
        let restored = {};
        try {
            restored = JSON.parse(
                browser.sessionStorage.getItem(this.storageKey) ||
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
            restored.linkedRecord = params.linked_filter
                ? `${this.recordContext.resModel}:${this.recordContext.resId}`
                : "";
            restored.linkedState = "";
            restored.mappedPartnerId = params.mapped_partner_id
                ? String(params.mapped_partner_id)
                : "";
        }
        this.initialDocumentId = urlState.documentId;
        this.initialVersionId = urlState.versionId;
        this.hasLocalListHistory = false;
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
            creatingTag: false,
            operation: null,
            truncated: false,
        });
        this.onPopState = (event) => this.handlePopState(event);
        onWillStart(async () => {
            await this.load();
            if (this.initialDocumentId) {
                await this.openDocumentById(
                    this.initialDocumentId,
                    this.initialVersionId
                );
            }
        });
        onMounted(() => {
            browser.addEventListener("popstate", this.onPopState);
            this.replaceNavigationState();
            this.restoreScroll();
        });
        onWillUnmount(() => browser.removeEventListener("popstate", this.onPopState));
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
        return [
            this.state.companyId,
            this.state.tagIds.length,
            this.state.correspondentId,
            this.state.documentTypeId,
            this.state.dateFrom,
            this.state.dateTo,
            this.state.addedFrom,
            this.state.addedTo,
            this.state.source,
            this.state.confidentiality,
            this.state.reviewState,
            this.state.linkedState,
            this.state.linkedRecord,
            this.state.mappedPartnerId,
        ].filter(Boolean).length;
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
            /(?:^|\s)(tag|type|from|company|review|privacy):([^\s]*)$/i
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
        };
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
            browser.history[`${mode}State`](
                {
                    ...(browser.history.state || {}),
                    skipRouteChange: true,
                    uslDocumentsWorkspace: true,
                    uslDocumentId: documentId || null,
                    uslVersionId: versionId || null,
                },
                "",
                url
            );
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
        if (!historyState.uslDocumentsWorkspace) {
            return;
        }
        const documentId = Number(historyState.uslDocumentId) || null;
        if (!documentId) {
            this.state.selected = null;
            this.state.editingMetadata = false;
            this.state.tagPickerOpen = false;
            this.restoreScroll();
            return;
        }
        await this.openDocumentById(documentId, historyState.uslVersionId);
    }

    workspaceKwargs() {
        return {
            query: this.state.query,
            workspace: this.state.workspace,
            page: this.state.page,
            page_size: this.state.pageSize,
            sort: this.state.sort,
            company_id: this.state.companyId || null,
            tag_ids: this.state.tagIds,
            correspondent_id: this.state.correspondentId || null,
            document_type_id: this.state.documentTypeId || null,
            date_from: this.state.dateFrom || null,
            date_to: this.state.dateTo || null,
            added_from: this.state.addedFrom || null,
            added_to: this.state.addedTo || null,
            source: this.state.source || null,
            confidentiality: this.state.confidentiality || null,
            review_state: this.state.reviewState || null,
            linked_state: this.state.linkedState || null,
            linked_model: this.state.linkedRecord
                ? this.state.linkedRecord.split(":", 1)[0]
                : null,
            linked_id: this.state.linkedRecord
                ? Number(this.state.linkedRecord.split(":", 2)[1])
                : null,
            mapped_partner_id: this.state.mappedPartnerId || null,
        };
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
            this.state.linkFacets = result.link_facets || this.state.linkFacets;
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
        if (view.filters && Object.keys(view.filters).length) {
            this.applySavedFilters(view.filters);
        }
        this.state.page = 1;
        this.state.selected = null;
        this.state.searchInput = this.state.query;
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
        this.state.query = "";
        this.state.searchInput = "";
        return this.clearFilters();
    }

    updateFilter(field, event) {
        this.state[field] = event.target.value;
        this.state.page = 1;
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
        };
        const field = fields[suggestion.kind];
        if (field === "tagIds") {
            const tagId = Number(suggestion.item.id);
            if (!this.state.tagIds.includes(tagId)) {
                this.state.tagIds = [...this.state.tagIds, tagId];
            }
        } else {
            this.state[field] = String(suggestion.item.id);
        }
        this.state.searchInput = this.state.query;
        this.state.searchFocused = false;
        this.state.page = 1;
        return this.load();
    }

    removeFacet(key) {
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

    toggleTagFilter(tagId) {
        this.state.tagIds = this.state.tagIds.includes(tagId)
            ? this.state.tagIds.filter((id) => id !== tagId)
            : [...this.state.tagIds, tagId];
        this.state.page = 1;
        return this.load();
    }

    async savePersonalView() {
        this.state.savingView = true;
        try {
            const filters = {
                query: this.state.query,
                sort: this.state.sort,
                company_id: this.state.companyId,
                tag_ids: this.state.tagIds,
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
        this.hasLocalListHistory = true;
        this.writeNavigationState("push", document.id);
        await this.openDocumentById(document.id);
    }

    async openDocumentById(documentId, versionId = null) {
        const document =
            this.state.documents.find((item) => item.id === Number(documentId)) || {
                id: Number(documentId),
                name: "Document",
            };
        this.state.selected = {
            ...document,
            preview_url: `/usl_documents/${document.id}/preview`,
        };
        this.state.selectedLoading = true;
        this.state.editingMetadata = false;
        try {
            const detail = await this.orm.call("usl.document", "document_detail", [
                document.id,
            ]);
            if (this.state.selected?.id === document.id) {
                this.state.selected = {
                    ...detail,
                    preview_url: `/usl_documents/${document.id}/preview`,
                };
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

    onDocumentKeydown(event, document) {
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            return this.select(document);
        }
    }

    closeDetail() {
        if (this.hasLocalListHistory) {
            browser.history.back();
        } else {
            this.state.selected = null;
            this.state.editingMetadata = false;
            this.state.tagPickerOpen = false;
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
                preview_url: `/usl_documents/${detail.id}/preview`,
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
                preview_url: `/usl_documents/${detail.id}/preview`,
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
        event.preventDefault();
        this.state.dragged = true;
    }

    onDragLeave() {
        this.state.dragged = false;
    }

    onDrop(event) {
        event.preventDefault();
        this.state.dragged = false;
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
            "usl.document.link",
            "action_open_record",
            [[link.id]]
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
        for (let attempt = 0; attempt < 30; attempt++) {
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
                return;
            }
            if (status.state === "failed") {
                this.notification.add(
                    status.error || "Paperless processing failed.",
                    { type: "danger", sticky: true }
                );
                return;
            }
            await new Promise((resolve) => setTimeout(resolve, 2000));
        }
        this.notification.add(
            "Processing continues in Paperless. This status will update automatically.",
            { type: "info" }
        );
    }
}

registry.category("actions").add(
    "usl_documents.workspace",
    DocumentsWorkspace
);
