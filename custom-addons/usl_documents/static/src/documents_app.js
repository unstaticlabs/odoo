/** @odoo-module **/

import {
    Component,
    onMounted,
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
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { DateTimeInput } from "@web/core/datetime/datetime_input";
import { Dialog } from "@web/core/dialog/dialog";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { deserializeDate, serializeDate } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";
import { Pager } from "@web/core/pager/pager";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useBus, useService } from "@web/core/utils/hooks";
import { SearchBar } from "@web/search/search_bar/search_bar";
import { WithSearch } from "@web/search/with_search/with_search";
import { useSetupAction } from "@web/search/action_hook";
import { Many2One } from "@web/views/fields/many2one/many2one";
import {
    Many2XAutocomplete,
    useSelectCreate,
} from "@web/views/fields/relational_utils";
import { getDefaultConfig } from "@web/views/view";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import {DocumentPreview} from "@usl_documents/document_preview";

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
const DEFAULT_PAGE_SIZE = 24;
const MAX_PAGE_SIZE = 500;

for (const key of [
    "uslDocumentsWorkspace",
    "uslDocumentId",
    "uslVersionId",
    "uslDocumentsRecordContext",
    "uslDocumentsRecordBoundary",
    "uslDocumentsReturnRecord",
]) {
    router.hideKeyFromUrl(key);
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

export class ShortcutSaveDialog extends Component {
    static template = "usl_documents.ShortcutSaveDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        shortcut: { type: [Object, { value: false }], optional: true },
        onSaved: { type: Function, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            loading: true,
            saving: false,
            name: this.props.shortcut?.name || "",
            icon: this.props.shortcut?.icon || "fa-filter",
            sequence: this.props.shortcut?.sequence || 10,
            selectedViewIds: [...(this.props.shortcut?.smart_view_ids || [])],
            smartViews: [],
        });
        onWillStart(async () => {
            const values = await this.orm.call(
                "usl.document.quick.filter",
                "builder_values",
                [this.props.shortcut?.id || false]
            );
            this.state.smartViews = values.smart_views || [];
            if (!this.props.shortcut && values.shortcut) {
                Object.assign(this.state, {
                    name: values.shortcut.name,
                    icon: values.shortcut.icon,
                    sequence: values.shortcut.sequence,
                    selectedViewIds: values.shortcut.smart_view_ids,
                });
            }
            this.state.loading = false;
        });
    }

    toggleView(viewId) {
        const selected = new Set(this.state.selectedViewIds);
        if (selected.has(viewId)) {
            selected.delete(viewId);
        } else {
            selected.add(viewId);
        }
        this.state.selectedViewIds = [...selected];
    }

    async save() {
        const name = this.state.name.trim();
        if (!name || this.state.saving) {
            return;
        }
        this.state.saving = true;
        try {
            const nativeValues = this.env.searchModel.getIrFilterValues({
                description: name,
                isShared: true,
            });
            const shortcut = await this.orm.call(
                "usl.document.quick.filter",
                "save_from_search",
                [name, {
                    domain: nativeValues.domain,
                    context: nativeValues.context,
                    sort: nativeValues.sort,
                }],
                {
                    shortcut_id: this.props.shortcut?.id || false,
                    icon: this.state.icon,
                    sequence: Number(this.state.sequence) || 10,
                    smart_view_ids: this.state.selectedViewIds,
                }
            );
            this.notification.add("One-click shortcut saved.", {
                type: "success",
            });
            await this.props.onSaved?.(shortcut);
            this.props.close();
        } catch (error) {
            this.notification.add(
                error.data?.message ||
                    error.message ||
                    "The shortcut could not be saved.",
                { type: "danger", sticky: true }
            );
        } finally {
            this.state.saving = false;
        }
    }
}

export class ShortcutFavoriteItem extends Component {
    static template = "usl_documents.ShortcutFavoriteItem";
    static components = { DropdownItem };
    static props = {};

    setup() {
        this.dialog = useService("dialog");
        this.state = useState({ allowed: false });
        onWillStart(async () => {
            this.state.allowed =
                this.env.searchModel.resModel === "usl.document" &&
                (await user.hasGroup(
                    "usl_documents.group_documents_manager"
                ));
        });
    }

    open() {
        this.dialog.add(ShortcutSaveDialog, {});
    }
}

registry.category("favoriteMenu").add(
    "usl-documents-one-click-shortcut",
    { Component: ShortcutFavoriteItem, groupNumber: 4 },
    { sequence: 10 }
);

export class DocumentsWorkspaceView extends Component {
    static template = "usl_documents.DocumentsWorkspaceView";
    static components = {
        DateTimeInput,
        DocumentPreview,
        Many2One,
        Many2XAutocomplete,
        Pager,
        SearchBar,
    };
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
        this.selectContact = useSelectCreate({
            resModel: "res.partner",
            activeActions: { create: false },
            onSelected: (partnerId) =>
                this.createCorrespondentFromPartner(
                    Array.isArray(partnerId) ? partnerId[0] : partnerId
                ),
            onCreateEdit: () => {},
        });
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
                // A record smart button has its own dynamic Odoo filter. Do
                // not inherit the global workspace search merely because the
                // global snapshot is the storage fallback.
                restored.nativeSearch = null;
                for (const [key, value] of Object.entries(FILTER_DEFAULTS)) {
                    restored[key] = Array.isArray(value) ? [] : value;
                }
            }
            if (!hasRecordState && !urlState.documentId) {
                restored.selectedDocumentId = null;
                restored.selectedVersionId = null;
            }
            if (params.linked_filter) {
                restored.workspace = "archive_search";
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
        if (
            restored.nativeSearch &&
            !this.searchModel.generateQueryString()
        ) {
            // Odoo may normalize the native domain/groupBy URL parameters
            // while switching to a linked business record. Restore the exact
            // SearchModel snapshot kept by this workspace so Back returns to
            // the same visible facets and effective domain.
            this.searchModel.applySearch(restored.nativeSearch);
        }
        this.initialDocumentId =
            Number(params.initial_document_id) ||
            urlState.documentId ||
            Number(restored.selectedDocumentId) ||
            null;
        this.initialVersionId =
            this.stringValue(params.initial_version_id) ||
            urlState.versionId ||
            this.stringValue(restored.selectedVersionId) ||
            null;
        this.hasRecordHistoryBoundary = false;
        this.hasLocalListHistory = false;
        this.closingDetail = false;
        this.recordContextKey = this.recordContext
            ? `${this.recordContext.resModel}:${this.recordContext.resId}`
            : null;
        this.state = useState({
            loading: true,
            uploading: false,
            savingView: false,
            dragged: false,
            degraded: false,
            error: "",
            query: typeof restored.query === "string" ? restored.query : "",
            searchInput: typeof restored.query === "string" ? restored.query : "",
            searchFocused: false,
            workspace:
                typeof restored.workspace === "string" ? restored.workspace : "home",
            searchMode: ["hybrid", "exact", "semantic"].includes(
                restored.searchMode
            )
                ? restored.searchMode
                : "hybrid",
            backgroundMode: ["include", "exclude", "only"].includes(
                restored.backgroundMode
            )
                ? restored.backgroundMode
                : "include",
            view: ["cards", "list"].includes(restored.view) ? restored.view : "cards",
            sort: ["recent", "ingested", "date", "title", "semantic"].includes(
                restored.sort
            )
                ? restored.sort
                : "recent",
            orderBy: Array.isArray(restored.orderBy)
                ? restored.orderBy.filter(
                      (term) =>
                          term &&
                          typeof term.name === "string" &&
                          typeof term.asc === "boolean"
                  )
                : this.searchModel.orderBy,
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
            pageSize:
                Number.isInteger(restored.pageSize) &&
                restored.pageSize > 0 &&
                restored.pageSize <= MAX_PAGE_SIZE
                    ? restored.pageSize
                    : DEFAULT_PAGE_SIZE,
            documents: [],
            selected: null,
            selectedLoading: false,
            reviewing: false,
            savingFields: {},
            tagShortcutQuery: "",
            operation: null,
            failedOperations: [],
            canUpload: false,
            truncated: false,
            warnings: [],
            semanticRefining: false,
            semanticScoresLoaded: false,
            starring: {},
            changingLibrary: false,
        });
        this.searchReady = false;
        this.workspaceLoadToken = 0;
        this.operationPollGeneration = 0;
        this.workspaceMetadataLoaded = false;
        this.resultWindow = [];
        this.resultWindowOffset = 0;
        this.resultWindowComplete = false;
        this.metadataSaveQueue = Promise.resolve();
        useSetupAction({
            getOrderBy: () => this.state.orderBy,
        });
        onWillUpdateProps((nextProps) => {
            const nextOrderBy = Array.isArray(nextProps.orderBy)
                ? nextProps.orderBy
                : [];
            if (
                JSON.stringify(nextOrderBy) !==
                JSON.stringify(this.props.orderBy || [])
            ) {
                this.state.orderBy = nextOrderBy;
                this.state.page = 1;
            }
        });
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
            this.workspaceLoadToken += 1;
            this.operationPollGeneration += 1;
            this.pollingOperationIds.clear();
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

    rpc(model, method, args = [], kwargs = {}) {
        const actionContext = this.props.action.context || {};
        const callContext = {
            ...actionContext,
            ...(kwargs.context || {}),
        };
        return this.orm.call(model, method, args, {
            ...kwargs,
            ...(Object.keys(callContext).length
                ? { context: callContext }
                : {}),
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

    get isSavingMetadata() {
        return Object.values(this.state.savingFields).some(Boolean);
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

    get isArchiveSearchEmpty() {
        return (
            this.state.workspace === "archive_search" &&
            !this.searchModel.facets.length
        );
    }

    get selectedRelationshipRole() {
        if (!this.state.selected) {
            return null;
        }
        if (this.recordContext) {
            return this.state.selected.links?.find(
                (link) =>
                    link.model === this.recordContext.resModel &&
                    link.res_id === this.recordContext.resId
            )?.document_role;
        }
        const mutableLinks = (this.state.selected.links || []).filter((link) =>
            ["background", "library"].includes(link.document_role)
        );
        if (mutableLinks.length === 1) {
            return mutableLinks[0].document_role;
        }
        return this.state.selected.intake_role;
    }

    get canPromoteSelected() {
        return (
            this.state.selected?.can_edit &&
            this.state.selected.availability_state === "available" &&
            this.selectedRelationshipRole === "background"
        );
    }

    get canDemoteSelected() {
        return (
            this.state.selected?.can_edit &&
            this.state.selected.availability_state === "available" &&
            this.selectedRelationshipRole === "library"
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
                    needs_attention: "Needs attention",
                    classified: "Ready for review",
                    reviewed: "Reviewed",
                }[document.review_state] || "No review status"),
            document_date: (document, interval) =>
                this.groupDateLabel(document.date, interval),
            archive_added_at: (document, interval) =>
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
                { id: "needs_attention", name: "Needs attention" },
                { id: "classified", name: "Ready for review" },
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

    get correspondentValue() {
        return this.state.selected?.correspondent_id
            ? {
                  id: this.state.selected.correspondent_id,
                  display_name: this.state.selected.correspondent || "Correspondent",
              }
            : false;
    }

    get documentTypeValue() {
        return this.state.selected?.document_type_id
            ? {
                  id: this.state.selected.document_type_id,
                  display_name: this.state.selected.document_type || "Document type",
              }
            : false;
    }

    get companyValue() {
        return this.state.selected?.company_id
            ? {
                  id: this.state.selected.company_id,
                  display_name: this.state.selected.company || "Company",
              }
            : false;
    }

    get companyProps() {
        return {
            id: "usl_document_company",
            relation: "res.company",
            string: "Company",
            value: this.companyValue,
            update: (value) => this.selectCompany(value),
            domain: () => [
                [
                    "id",
                    "in",
                    (this.state.companies || []).map((company) => company.id),
                ],
            ],
            placeholder: "Choose a company…",
            searchMoreLabel: "Search more companies…",
            canCreate: false,
            canQuickCreate: false,
            canCreateEdit: false,
            canOpen: false,
            readonly:
                !this.state.selected?.can_change_company ||
                Boolean(this.state.savingFields.company_id),
        };
    }

    get correspondentProps() {
        return {
            id: "usl_document_correspondent",
            relation: "usl.paperless.correspondent",
            string: "Correspondent",
            value: this.correspondentValue,
            update: (value) => this.selectCorrespondent(value),
            domain: () => [["active", "=", true]],
            otherSources: [this.contactAutocompleteSource],
            placeholder: "Choose or create a correspondent…",
            searchMoreLabel: "Search more correspondents…",
            canCreate: true,
            canQuickCreate: true,
            canCreateEdit: false,
            canOpen: false,
            readonly:
                !this.state.selected?.can_edit ||
                Boolean(this.state.savingFields.correspondent_id),
        };
    }

    get documentTypeProps() {
        return {
            id: "usl_document_type",
            relation: "usl.paperless.document.type",
            string: "Document type",
            value: this.documentTypeValue,
            update: (value) => this.selectDocumentType(value),
            domain: () => [["active", "=", true]],
            placeholder: "Choose or create a document type…",
            searchMoreLabel: "Search more document types…",
            canCreate: true,
            canQuickCreate: true,
            canCreateEdit: false,
            canOpen: false,
            readonly:
                !this.state.selected?.can_edit ||
                Boolean(this.state.savingFields.document_type_id),
        };
    }

    get contactAutocompleteSource() {
        return {
            placeholder: "Searching Contacts…",
            options: async (request) => {
                const records = await this.orm.call(
                    "res.partner",
                    "web_name_search",
                    [],
                    {
                        name: request,
                        operator: "ilike",
                        domain: [["active", "=", true]],
                        limit: 8,
                        specification: {
                            display_name: {},
                            email: {},
                            company_id: { fields: { display_name: {} } },
                        },
                    }
                );
                const options = records.map((record) => ({
                    label: `Use Contact: ${record.display_name}`,
                    onSelect: () =>
                        this.createCorrespondentFromPartner(record.id),
                }));
                options.push({
                    label: "Search Contacts…",
                    cssClass: "o_m2o_dropdown_option o_m2o_dropdown_option_search_more",
                    onSelect: () =>
                        this.selectContact({
                            domain: [["active", "=", true]],
                            context: {},
                            filters: request
                                ? [
                                      {
                                          description: `Quick search: ${request}`,
                                          domain: [
                                              ["name", "ilike", request],
                                          ],
                                      },
                                  ]
                                : [],
                            title: "Search: Contacts",
                        }),
                });
                return options;
            },
        };
    }

    get tagAutocompleteProps() {
        const selectedIds = (this.state.selected?.tags || []).map(
            (tag) => tag.id
        );
        return {
            activeActions: {
                create: true,
                createEdit: false,
                link: true,
                write: true,
            },
            fieldString: "Tags",
            getDomain: () => [
                ["active", "=", true],
                ["id", "not in", selectedIds],
            ],
            isToMany: true,
            placeholder: "Add a tag…",
            quickCreate: (name) => this.createAndAddTag(name),
            resModel: "usl.paperless.tag",
            searchMoreLabel: "Search more tags…",
            update: (records) =>
                Promise.all(
                    (records || []).map((record) =>
                        this.addSelectedTag({
                            id: record.id,
                            name: record.display_name || record.name,
                        })
                    )
                ),
            value: "",
        };
    }

    get documentDateValue() {
        return this.state.selected?.date
            ? deserializeDate(this.state.selected.date)
            : false;
    }

    get selectedDocumentDateDisplay() {
        return this.formatDocumentDate(this.state.selected?.date);
    }

    formatDocumentDate(value) {
        if (!value) {
            return "";
        }
        try {
            return deserializeDate(value).toFormat("dd/MM/yyyy");
        } catch {
            return value;
        }
    }

    get pagerProps() {
        return {
            offset: (this.state.page - 1) * this.state.pageSize,
            limit: this.state.pageSize,
            total: this.state.count,
            onUpdate: ({ offset, limit }) => {
                const pageSize = Math.min(
                    MAX_PAGE_SIZE,
                    Math.max(1, Number(limit) || this.state.pageSize)
                );
                this.state.pageSize = pageSize;
                this.state.page = Math.floor(offset / pageSize) + 1;
                const windowStart = Number(offset) - this.resultWindowOffset;
                const requestedEnd = Math.min(
                    Number(offset) + pageSize,
                    this.state.count
                );
                const windowEnd = requestedEnd - this.resultWindowOffset;
                if (
                    this.resultWindow.length &&
                    windowStart >= 0 &&
                    windowEnd <= this.resultWindow.length
                ) {
                    this.state.documents = this.resultWindow.slice(
                        windowStart,
                        windowEnd
                    );
                    this.persistState();
                    this.replaceNavigationState();
                    return Promise.resolve();
                }
                return this.load();
            },
        };
    }

    get canSortBySemantic() {
        return (
            this.state.semanticScoresLoaded ||
            this.state.semanticRefining ||
            this.state.sort === "semantic"
        );
    }

    get cardSort() {
        if (!this.state.orderBy.length) {
            return this.state.sort;
        }
        const [term] = this.state.orderBy;
        if (this.state.orderBy.length === 1) {
            if (term.name === "document_date" && !term.asc) {
                return "recent";
            }
            if (term.name === "archive_added_at" && !term.asc) {
                return "ingested";
            }
            if (term.name === "name" && term.asc) {
                return "title";
            }
        }
        return "custom";
    }

    semanticMatchPercent(document) {
        const suppliedValue = document?.semantic_match_percent;
        const supplied = Number(suppliedValue);
        if (
            suppliedValue !== null &&
            suppliedValue !== undefined &&
            Number.isFinite(supplied)
        ) {
            return Math.min(100, Math.max(0, Math.round(supplied)));
        }
        const similarityValue = document?.semantic_similarity;
        const similarity = Number(similarityValue);
        return similarityValue !== null &&
            similarityValue !== undefined &&
            Number.isFinite(similarity)
            ? Math.min(100, Math.max(0, Math.round(similarity * 100)))
            : null;
    }

    semanticMatchTone(document) {
        const percent = this.semanticMatchPercent(document);
        if (percent >= 75) {
            return "is-strong";
        }
        if (percent >= 50) {
            return "is-medium";
        }
        return "is-light";
    }

    semanticMatchLabel(document) {
        const percent = this.semanticMatchPercent(document);
        return percent === null ? "" : _t("Semantic similarity: %s%%", percent);
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
            searchMode: this.state.searchMode,
            backgroundMode: this.state.backgroundMode,
            orderBy: this.state.orderBy,
            page: this.state.page,
            pageSize: this.state.pageSize,
            ...Object.fromEntries(
                Object.keys(FILTER_DEFAULTS).map((key) => [key, this.state[key]])
            ),
            scrollTop: this.listScroller.el?.scrollTop || 0,
            selectedDocumentId: this.state.selected?.id || null,
            selectedVersionId:
                this.state.selected?.selected_version_id || null,
            nativeSearch: this.nativeSearchSnapshot(),
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
            orderBy: this.state.orderBy,
            page: this.state.page,
            pageSize: this.state.pageSize,
            ...Object.fromEntries(
                Object.keys(FILTER_DEFAULTS).map((key) => [key, this.state[key]])
            ),
            nativeSearch: this.nativeSearchSnapshot(),
        };
    }

    nativeSearchSnapshot() {
        // Search facets are reactive objects. Persist only their JSON data so
        // the snapshot is safe in both the URL and sessionStorage.
        const search = this.searchModel.getCurrentSearch();
        return JSON.parse(
            JSON.stringify({
                key: search.key,
                facets: search.facets,
                domain: search.domain,
                groupBys: search.groupBys,
            })
        );
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
            // SearchModel intentionally serializes its shareable state with
            // encodeURIComponent, matching Odoo's router. Do not pass this
            // encoded query through URLSearchParams: it rewrites spaces as
            // "+", while Odoo's router only decodes percent escapes. Repeated
            // reloads would otherwise turn a valid domain into one containing
            // accumulating unary "+" tokens.
            const nativeSearch = this.searchModel.generateQueryString();
            const navigationUrl = new URL(
                nativeSearch
                    ? `${url.href}${url.search ? "&" : "?"}${nativeSearch}`
                    : url.href
            );
            const routeFromUrl = router.urlToState(navigationUrl);
            const hostRoute =
                browser.history.state?.nextState || router.current || {};
            const isRecordBoundary = Boolean(
                this.hasRecordHistoryBoundary &&
                    mode === "replace" &&
                    !this.hasLocalListHistory
            );
            const nextState = {
                ...hostRoute,
                ...routeFromUrl,
                // Query-only rewrites in tests, embedded webviews, and older
                // /web routes do not reconstruct the controller stack from
                // the URL. Never discard Odoo's authoritative action stack.
                actionStack:
                    routeFromUrl.actionStack || hostRoute.actionStack,
                usl_document: documentId || undefined,
                usl_version: versionId || undefined,
                domain:
                    navigationUrl.searchParams.get("domain") || undefined,
                groupBy:
                    navigationUrl.searchParams.get("groupBy") || undefined,
                orderBy:
                    navigationUrl.searchParams.get("orderBy") || undefined,
                uslDocumentsWorkspace: true,
                uslDocumentId: documentId || null,
                uslVersionId: versionId || null,
                uslDocumentsRecordContext: this.recordContextKey,
                uslDocumentsRecordBoundary: isRecordBoundary
                    ? this.recordContextKey
                    : null,
                uslDocumentsReturnRecord: null,
            };
            const historyState = {
                ...(browser.history.state || {}),
                nextState,
                // This client action owns its drawer and filter history.
                // Its entry from a business-record smart button is the
                // exception: Odoo must reload that route when the user comes
                // Forward from the originating record.
                skipRouteChange: !isRecordBoundary,
                uslDocumentsWorkspace: true,
                uslDocumentId: documentId || null,
                uslVersionId: versionId || null,
                uslDocumentsRecordContext: this.recordContextKey,
                uslDocumentsRecordBoundary: isRecordBoundary
                    ? this.recordContextKey
                    : null,
                uslDocumentsReturnRecord: null,
            };
            // The global Odoo router intentionally batches route changes.
            // Using it for a local drawer can append a second canonicalized
            // detail URL after this entry. A native Odoo-compatible history
            // entry gives one click one entry, while retaining deep links and
            // leaving the untouched Forward entry able to reopen the drawer.
            if (
                mode === "push" &&
                navigationUrl.href !== browser.location.href
            ) {
                browser.history.pushState(historyState, "", navigationUrl);
            } else {
                browser.history.replaceState(historyState, "", navigationUrl);
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
        if (!this.recordContext) {
            return;
        }
        const existingBoundary =
            browser.history.state?.uslDocumentsRecordBoundary ||
            browser.history.state?.nextState?.uslDocumentsRecordBoundary;
        if (existingBoundary === this.recordContextKey) {
            // Forward navigation remounted the existing route boundary.
            this.hasRecordHistoryBoundary = true;
            return;
        }
        try {
            const workspaceUrl = new URL(browser.location.href);
            const baseState = { ...(browser.history.state || {}) };
            const workspaceRoute = {
                ...(baseState.nextState || router.current),
            };
            const actionStack = workspaceRoute.actionStack;
            if (!Array.isArray(actionStack) || actionStack.length < 2) {
                // A direct/deep link has no originating action to restore.
                // Keep Odoo's normal breadcrumb behavior in that case.
                return;
            }
            const previousStack = actionStack
                .slice(0, -1)
                .map((item) => ({ ...item }));
            const previousRoute = {
                actionStack: previousStack,
                ...previousStack.at(-1),
            };
            const previousUrl = new URL(
                router.stateToUrl(previousRoute),
                browser.location.origin
            );
            browser.history.replaceState(
                {
                    nextState: previousRoute,
                },
                "",
                previousUrl
            );
            this.hasRecordHistoryBoundary = true;
            const documentsRoute = {
                ...workspaceRoute,
                uslDocumentsWorkspace: true,
                uslDocumentId: this.state.selected?.id || null,
                uslVersionId:
                    this.state.selected?.selected_version_id || null,
                uslDocumentsRecordContext: this.recordContextKey,
                uslDocumentsRecordBoundary: this.recordContextKey,
                uslDocumentsReturnRecord: null,
            };
            browser.history.pushState(
                {
                    ...baseState,
                    nextState: documentsRoute,
                    // This is a real Odoo route boundary. Forward navigation
                    // must remount the Documents client action.
                    skipRouteChange: false,
                    uslDocumentsWorkspace: true,
                    uslDocumentId: this.state.selected?.id || null,
                    uslVersionId:
                        this.state.selected?.selected_version_id || null,
                    uslDocumentsRecordContext: this.recordContextKey,
                    uslDocumentsRecordBoundary: this.recordContextKey,
                    uslDocumentsReturnRecord: null,
                },
                "",
                workspaceUrl
            );
        } catch {
            // Odoo breadcrumbs remain available if a browser blocks History API writes.
        }
    }

    clearDetailState() {
        this.state.selected = null;
        this.state.savingFields = {};
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
        if (returnRecord) {
            this.closingDetail = false;
            this.clearWorkspaceRouteState();
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

    clearWorkspaceRouteState() {
        try {
            const url = new URL(browser.location.href);
            for (const key of [
                "domain",
                "groupBy",
                "orderBy",
                "usl_document",
                "usl_version",
                "usl_filters",
            ]) {
                url.searchParams.delete(key);
            }
            const currentState = { ...(browser.history.state || {}) };
            const nextState = {
                ...(currentState.nextState || router.urlToState(url)),
            };
            for (const key of [
                "domain",
                "groupBy",
                "orderBy",
                "usl_document",
                "usl_version",
                "usl_filters",
            ]) {
                delete nextState[key];
            }
            browser.history.replaceState(
                {
                    ...currentState,
                    nextState,
                    skipRouteChange: true,
                    uslDocumentsWorkspace: false,
                    uslDocumentId: null,
                    uslVersionId: null,
                    uslDocumentsRecordContext: null,
                    uslDocumentsRecordBoundary: null,
                    uslDocumentsReturnRecord: null,
                },
                "",
                url
            );
        } catch {
            // The record action remains a safe fallback if History API is blocked.
        }
    }

    workspaceKwargs({
        searchMode = this.state.searchMode,
        includeWorkspaceMetadata = !this.workspaceMetadataLoaded,
    } = {}) {
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
            order_by: this.state.orderBy,
            search_domain: this.searchModel.domain,
            search_mode: searchMode,
            include_workspace_metadata: includeWorkspaceMetadata,
            background_mode: this.state.backgroundMode,
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

    hasProgressiveSemanticSearch() {
        return (
            this.state.searchMode === "hybrid" &&
            this.domainLeaves(this.searchModel.domain).some(
                (leaf) => leaf[0] === "all_text" && leaf[2]
            )
        );
    }

    hasSemanticSearch() {
        return this.domainLeaves(this.searchModel.domain).some(
            (leaf) =>
                (leaf[0] === "semantic_text" ||
                    (leaf[0] === "all_text" && this.state.searchMode !== "exact")) &&
                leaf[2]
        );
    }

    applyWorkspaceResult(result) {
        if (Array.isArray(result.result_window)) {
            this.resultWindow = result.result_window;
            this.resultWindowOffset = Number(result.result_window_offset) || 0;
            this.resultWindowComplete = Boolean(result.result_window_complete);
        } else {
            this.resultWindow = [];
            this.resultWindowOffset = 0;
            this.resultWindowComplete = false;
        }
        this.state.documents = result.documents;
        this.state.count = result.count;
        this.state.page = Number(result.page) || this.state.page;
        this.state.pageSize = Math.min(
            MAX_PAGE_SIZE,
            Math.max(1, Number(result.page_size) || this.state.pageSize)
        );
        this.state.semanticScoresLoaded = Boolean(result.semantic_scores_loaded);
        this.state.degraded = result.degraded;
        if (result.metadata_included) {
            this.state.smartViews = result.smart_views || [];
            this.state.tags = result.tags || [];
            this.state.correspondents = result.correspondents || [];
            this.state.documentTypes = result.document_types || [];
            this.state.companies = result.companies || [];
            this.state.customFields = result.custom_fields || [];
            this.state.linkFacets = result.link_facets || [];
            this.workspaceMetadataLoaded = true;
        }
        this.state.canUpload = Boolean(result.can_upload);
        this.state.failedOperations = result.failed_operations || [];
        if (!this.state.operation && result.active_operation) {
            this.state.operation = result.active_operation;
            this.pollOperation(result.active_operation.id);
        }
        this.state.truncated = Boolean(result.truncated);
        this.state.warnings = (result.warnings || []).map((warning) =>
            typeof warning === "string"
                ? warning
                : warning.message || warning.code || "Search is partially unavailable."
        );
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
    }

    async load() {
        if (this.state.sort === "semantic" && !this.hasSemanticSearch()) {
            this.state.sort = "recent";
        }
        const loadToken = ++this.workspaceLoadToken;
        const progressive = this.hasProgressiveSemanticSearch();
        const includeWorkspaceMetadata = !this.workspaceMetadataLoaded;
        this.state.loading = true;
        this.state.semanticRefining = false;
        this.state.error = "";
        try {
            const result = await this.rpc(
                "usl.document",
                "workspace_data",
                [],
                this.workspaceKwargs({
                    searchMode: progressive ? "exact" : this.state.searchMode,
                    includeWorkspaceMetadata,
                })
            );
            if (loadToken !== this.workspaceLoadToken) {
                return;
            }
            this.applyWorkspaceResult(result);
            this.state.loading = false;
            if (progressive && !result.degraded && !result.error) {
                this.state.semanticRefining = true;
                try {
                    const refined = await this.rpc(
                        "usl.document",
                        "workspace_data",
                        [],
                        this.workspaceKwargs({
                            searchMode: "hybrid",
                            includeWorkspaceMetadata: false,
                        })
                    );
                    if (loadToken !== this.workspaceLoadToken) {
                        return;
                    }
                    this.applyWorkspaceResult(refined);
                } catch (_error) {
                    if (loadToken === this.workspaceLoadToken) {
                        this.state.warnings = [
                            ...this.state.warnings,
                            _t(
                                "Exact matches are shown; semantic refinement is temporarily unavailable."
                            ),
                        ];
                    }
                } finally {
                    if (loadToken === this.workspaceLoadToken) {
                        this.state.semanticRefining = false;
                    }
                }
            }
        } catch (error) {
            if (loadToken !== this.workspaceLoadToken) {
                return;
            }
            this.state.degraded = true;
            this.state.error =
                error.data?.message ||
                error.message ||
                "The archive could not be loaded.";
        } finally {
            if (loadToken === this.workspaceLoadToken) {
                this.state.loading = false;
                this.state.semanticRefining = false;
                this.persistState();
                this.replaceNavigationState();
            }
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
        if (field === "sort") {
            this.state.orderBy = [];
        }
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
        if (this.state.workspace !== "archive_search") {
            // Tag counts describe the complete archive visible to the current
            // user. Apply their filters in the same scope instead of silently
            // intersecting them with Home or another smart-view domain.
            this.state.workspace = "archive_search";
            this.state.page = 1;
            this.state.selected = null;
        }
        this.replaceTagSearchFilters([...selected]);
    }

    onTagShortcutSearch(event) {
        this.state.tagShortcutQuery = event.target.value;
    }

    isSmartShortcutActive(shortcut) {
        if (shortcut.kind === "group") {
            return (shortcut.group_by || []).every((groupBy) =>
                this.searchModel.groupBy.includes(groupBy)
            );
        }
        return Object.values(this.searchModel.searchItems).some(
            (item) =>
                item.uslShortcutKey === shortcut.key &&
                this.searchModel.query.some(
                    (query) => query.searchItemId === item.id
                )
        );
    }

    setShortcutGroups(shortcut, activate) {
        for (const rawGroupBy of shortcut.group_by || []) {
            const [fieldName, interval] = rawGroupBy.split(":");
            let groupItem = Object.values(this.searchModel.searchItems).find(
                (item) =>
                    ["groupBy", "dateGroupBy"].includes(item.type) &&
                    item.fieldName === fieldName &&
                    (
                        item.uslShortcutKey === shortcut.key ||
                        !item.uslShortcutKey
                    )
            );
            if (groupItem && !groupItem.uslShortcutKey) {
                groupItem.uslShortcutKey = shortcut.key;
            }
            if (!groupItem && activate) {
                const beforeIds = new Set(
                    Object.keys(this.searchModel.searchItems).map(Number)
                );
                this.searchModel.createNewGroupBy(fieldName, { interval });
                groupItem = Object.values(this.searchModel.searchItems).find(
                    (item) =>
                        !beforeIds.has(item.id) &&
                        ["groupBy", "dateGroupBy"].includes(item.type) &&
                        item.fieldName === fieldName
                );
                if (groupItem) {
                    groupItem.uslShortcutKey = shortcut.key;
                }
                continue;
            }
            if (!groupItem) {
                continue;
            }
            const active = this.searchModel.query.some(
                (query) => query.searchItemId === groupItem.id
            );
            if (active === activate) {
                continue;
            }
            if (groupItem.type === "dateGroupBy") {
                this.searchModel.toggleDateGroupBy(
                    groupItem.id,
                    interval || groupItem.defaultIntervalId
                );
            } else {
                this.searchModel.toggleSearchItem(groupItem.id);
            }
        }
    }

    toggleSmartShortcut(shortcut) {
        if (shortcut.kind === "group") {
            this.setShortcutGroups(
                shortcut,
                !this.isSmartShortcutActive(shortcut)
            );
            if (shortcut.order_by?.length) {
                this.state.orderBy = shortcut.order_by;
                this.state.page = 1;
                this.load();
            }
            return;
        }
        const existing = Object.values(this.searchModel.searchItems).find(
            (item) => item.uslShortcutKey === shortcut.key
        );
        if (existing) {
            if (
                shortcut.order_by?.length &&
                JSON.stringify(this.state.orderBy) ===
                    JSON.stringify(shortcut.order_by)
            ) {
                this.state.orderBy = [];
            }
            this.setShortcutGroups(shortcut, false);
            this.searchModel.toggleSearchItem(existing.id);
            return;
        }
        if (shortcut.order_by?.length) {
            this.state.orderBy = shortcut.order_by;
        }
        this.setShortcutGroups(shortcut, true);
        this.searchModel.createNewFilters([
            {
                description: shortcut.name,
                domain: shortcut.domain,
                uslShortcutKey: shortcut.key,
            },
        ]);
    }

    sortDocuments(fieldName) {
        const current = this.state.orderBy[0];
        const defaultAscending = [
            "name",
            "correspondent_id",
            "document_type_id",
            "company_id",
            "tag_sort_key",
            "status_sort_key",
        ].includes(fieldName);
        this.state.orderBy = [
            {
                name: fieldName,
                asc:
                    current?.name === fieldName
                        ? !current.asc
                        : defaultAscending,
            },
        ];
        this.state.page = 1;
        return this.load();
    }

    sortIcon(fieldName) {
        const current = this.state.orderBy[0];
        if (current?.name !== fieldName) {
            return "fa fa-sort opacity-50";
        }
        return current.asc ? "fa fa-sort-asc" : "fa fa-sort-desc";
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
            this.state.workspace = "home";
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
        try {
            const detail = await this.rpc(
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
                try {
                    await this.orm.call(
                        "usl.document",
                        "action_mark_opened",
                        [[document.id]]
                    );
                } catch {
                    // Personal recency is a convenience. It must never block
                    // authorized archive access or document preview.
                }
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

    async toggleStar(document) {
        if (!document?.id || this.state.starring[document.id]) {
            return;
        }
        this.state.starring[document.id] = true;
        const starred = !document.is_starred;
        const setStarred = (value) => {
            for (const item of [...this.state.documents, ...this.resultWindow]) {
                if (item.id === document.id) {
                    item.is_starred = value;
                }
            }
            if (this.state.selected?.id === document.id) {
                this.state.selected.is_starred = value;
            }
        };
        setStarred(starred);
        try {
            await this.orm.call("usl.document", "action_set_starred", [
                [document.id],
                starred,
            ]);
            this.notification.add(
                starred ? "Added to your starred documents." : "Removed from your starred documents.",
                { type: "success" }
            );
            const favoriteShortcut = this.smartViewShortcuts.find(
                (shortcut) => shortcut.key === "starred"
            );
            if (
                !starred &&
                favoriteShortcut &&
                this.isSmartShortcutActive(favoriteShortcut)
            ) {
                this.state.documents = this.state.documents.filter(
                    (item) => item.id !== document.id
                );
                this.state.count = Math.max(0, this.state.count - 1);
            }
        } catch (error) {
            setStarred(!starred);
            this.notification.add(
                error.data?.message || error.message || "Your star was not saved.",
                { type: "danger", sticky: true }
            );
        } finally {
            this.state.starring[document.id] = false;
        }
    }

    async setLibraryVisibility(promote) {
        const selected = this.state.selected;
        if (!selected || this.state.changingLibrary) {
            return;
        }
        this.state.changingLibrary = true;
        try {
            const detail = await this.orm.call(
                "usl.document",
                "action_set_library_visibility",
                [[selected.id], promote],
                this.recordContext
                    ? {
                          res_model: this.recordContext.resModel,
                          res_id: this.recordContext.resId,
                      }
                    : {}
            );
            this.state.selected = {
                ...detail,
                preview_url: this.documentPreviewUrl(detail),
            };
            this.notification.add(
                promote
                    ? "Added to My library. The archived file was reused."
                    : "Removed from My library. The archive and business links were kept.",
                { type: "success" }
            );
            await this.load();
        } catch (error) {
            this.notification.add(
                error.data?.message ||
                    error.message ||
                    "Library visibility could not be changed.",
                { type: "danger", sticky: true }
            );
        } finally {
            this.state.changingLibrary = false;
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

    async markReviewed() {
        const selectedId = this.state.selected?.id;
        const documentName = this.state.selected?.name || "Document";
        if (!selectedId || this.state.reviewing) {
            return;
        }
        this.state.reviewing = true;
        try {
            const detail = await this.orm.call(
                "usl.document",
                "action_mark_reviewed",
                [[selectedId]]
            );
            if (this.state.selected?.id === selectedId) {
                this.state.selected = {
                    ...detail,
                    preview_url: this.documentPreviewUrl(detail),
                };
                await this.load();
            }
            this.notification.add(`“${documentName}” marked as reviewed.`, {
                type: "success",
            });
        } catch (error) {
            const reason =
                error.data?.message || error.message || "The review was not saved.";
            this.notification.add(
                `“${documentName}” could not be marked as reviewed. ${reason}`,
                { type: "danger", sticky: true }
            );
        } finally {
            this.state.reviewing = false;
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

    saveMetadataField(field, value) {
        return this.resolveMetadataField(field, async () => value);
    }

    resolveMetadataField(field, resolveValue) {
        const selectedId = this.state.selected?.id;
        if (!selectedId || this.state.savingFields[field]) {
            return Promise.resolve();
        }
        this.state.savingFields[field] = true;
        const save = async () => {
            try {
                const value = await resolveValue();
                const detail = await this.orm.call(
                    "usl.document",
                    "update_archive_metadata",
                    [[selectedId], { [field]: value }]
                );
                if (this.state.selected?.id === selectedId) {
                    this.state.selected = {
                        ...detail,
                        preview_url: this.documentPreviewUrl(detail),
                    };
                    await this.load();
                }
            } catch (error) {
                this.notification.add(
                    error.data?.message ||
                        error.message ||
                        "The change could not be saved. The previous value was kept.",
                    { type: "danger", sticky: true }
                );
            } finally {
                this.state.savingFields[field] = false;
            }
        };
        this.metadataSaveQueue = this.metadataSaveQueue.then(save, save);
        return this.metadataSaveQueue;
    }

    selectCorrespondent(value) {
        const recordId = value?.id || value?.[0] || false;
        if (recordId || !value) {
            return this.saveMetadataField("correspondent_id", recordId);
        }
        const name = String(value.display_name || "").trim();
        if (!name) {
            return this.saveMetadataField("correspondent_id", false);
        }
        return this.resolveMetadataField("correspondent_id", async () => {
            const [correspondentId] = await this.orm.create(
                "usl.paperless.correspondent",
                [
                    {
                        name,
                        matching_algorithm: "0",
                        is_insensitive: true,
                    },
                ]
            );
            return correspondentId;
        });
    }

    selectDocumentType(value) {
        const recordId = value?.id || value?.[0] || false;
        if (recordId || !value) {
            return this.saveMetadataField("document_type_id", recordId);
        }
        const name = String(value.display_name || "").trim();
        if (!name) {
            return this.saveMetadataField("document_type_id", false);
        }
        return this.resolveMetadataField("document_type_id", async () => {
            const [documentTypeId] = await this.orm.create(
                "usl.paperless.document.type",
                [
                    {
                        name,
                        matching_algorithm: "0",
                        is_insensitive: true,
                    },
                ]
            );
            return documentTypeId;
        });
    }

    selectCompany(value) {
        const companyId = value?.id || value?.[0] || false;
        const selectedId = this.state.selected?.id;
        if (
            !selectedId ||
            this.state.savingFields.company_id ||
            companyId === (this.state.selected?.company_id || false)
        ) {
            return Promise.resolve();
        }
        this.state.savingFields.company_id = true;
        const save = async () => {
            try {
                const detail = await this.orm.call(
                    "usl.document",
                    "set_company",
                    [[selectedId], companyId]
                );
                if (this.state.selected?.id === selectedId) {
                    this.state.selected = {
                        ...detail,
                        preview_url: this.documentPreviewUrl(detail),
                    };
                    await this.load();
                }
            } catch (error) {
                this.notification.add(
                    error.data?.message ||
                        error.message ||
                        "The company could not be changed. The previous company was kept.",
                    { type: "danger", sticky: true }
                );
            } finally {
                this.state.savingFields.company_id = false;
            }
        };
        this.metadataSaveQueue = this.metadataSaveQueue.then(save, save);
        return this.metadataSaveQueue;
    }

    saveTitle(event) {
        const value = event.target.value.trim();
        if (!value) {
            event.target.value = this.state.selected?.name || "";
            this.notification.add("A document title is required.", {
                type: "warning",
            });
            return;
        }
        if (value !== this.state.selected?.name) {
            return this.saveMetadataField("name", value);
        }
    }

    onTitleKeydown(event) {
        if (event.key === "Enter") {
            event.preventDefault();
            event.target.blur();
        } else if (event.key === "Escape") {
            event.preventDefault();
            event.target.value = this.state.selected?.name || "";
            event.target.blur();
        }
    }

    saveDocumentDate(value) {
        return this.saveMetadataField(
            "document_date",
            value ? serializeDate(value) : false
        );
    }

    setSelectedTags(tagIds) {
        return this.saveMetadataField("tag_ids", tagIds);
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

    async createAndAddTag(requestedName) {
        const name = String(requestedName || "").trim();
        if (!name) {
            return;
        }
        const existing = this.state.tags.find(
            (tag) => tag.name.toLowerCase() === name.toLowerCase()
        );
        if (existing) {
            return this.addSelectedTag(existing);
        }
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
            const documentName = this.state.selected?.name || "this document";
            this.notification.add(
                `Tag “${name}” created and added to “${documentName}”.`,
                { type: "success" }
            );
        } catch (error) {
            this.notification.add(
                error.data?.message || error.message || "The tag could not be created.",
                { type: "danger", sticky: true }
            );
        }
    }

    createCorrespondentFromPartner(partnerId) {
        if (!partnerId) {
            return;
        }
        return this.resolveMetadataField("correspondent_id", async () => {
            const correspondent = await this.orm.call(
                "usl.paperless.correspondent",
                "create_from_partner",
                [partnerId]
            );
            if (
                !this.state.correspondents.some(
                    (item) => item.id === correspondent.id
                )
            ) {
                this.state.correspondents = [
                    ...this.state.correspondents,
                    correspondent,
                ];
            }
            return correspondent.id;
        });
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
        const documentName = this.state.selected?.name || "Document";
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
                            `A version of “${documentName}” could not be restored.`,
                        { type: "danger", sticky: true }
                    );
                }
            },
        });
    }

    restoreFromTrash() {
        const documentName = this.state.selected?.name || "Document";
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
                            `“${documentName}” could not be restored.`,
                        { type: "danger", sticky: true }
                    );
                }
            },
        });
    }

    moveToTrash() {
        const documentName = this.state.selected?.name || "Document";
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
                            `“${documentName}” could not be moved to Trash.`,
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
                        `“${selected.name}” was permanently deleted. An audit tombstone remains in Odoo.`,
                        { type: "success", sticky: true }
                    );
                    await this.load();
                } catch (error) {
                    this.notification.add(
                        error.data?.message ||
                            error.message ||
                            `“${selected.name}” could not be permanently deleted.`,
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
        this.replaceNavigationState();
    }

    setCardSort(event) {
        this.state.sort = event.target.value;
        this.state.orderBy = [];
        this.state.page = 1;
        return this.load();
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
        const documentName = this.state.selected.name;
        try {
            await this.rpc("usl.document", "link_to_record", [
                [this.state.selected.id],
                this.recordContext.resModel,
                this.recordContext.resId,
            ]);
            this.notification.add(`“${documentName}” was linked to this record.`, {
                type: "success",
            });
            await this.load();
            await this.openDocumentById(this.state.selected.id);
        } catch (error) {
            this.notification.add(
                error.data?.message ||
                    error.message ||
                    `“${documentName}” could not be linked.`,
                { type: "danger", sticky: true }
            );
        }
    }

    async unlinkCurrent() {
        if (!this.recordContext || !this.state.selected) {
            return;
        }
        const documentName = this.state.selected.name;
        try {
            await this.rpc("usl.document", "unlink_from_record", [
                [this.state.selected.id],
                this.recordContext.resModel,
                this.recordContext.resId,
            ]);
            this.notification.add(
                `The link to “${documentName}” was removed. The archived document was not deleted.`,
                { type: "success" }
            );
            await this.load();
            await this.openDocumentById(this.state.selected.id);
        } catch (error) {
            this.notification.add(
                error.data?.message ||
                    error.message ||
                    `The link to “${documentName}” could not be removed.`,
                { type: "danger", sticky: true }
            );
        }
    }

    async openLink(link) {
        this.persistState();
        const action = await this.rpc(
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
        const generation = this.operationPollGeneration;
        const isActive = () => generation === this.operationPollGeneration;
        this.pollingOperationIds.add(operationId);
        try {
            for (let attempt = 0; attempt < 90; attempt++) {
                let statuses;
                try {
                    statuses = await this.orm.call(
                        "usl.document.operation",
                        "poll",
                        [[operationId]]
                    );
                } catch (error) {
                    if (!isActive()) {
                        return;
                    }
                    throw error;
                }
                if (!isActive()) {
                    return;
                }
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
                    const documentName =
                        status.document_name ||
                        this.state.selected?.name ||
                        this.state.operation?.name ||
                        "Document";
                    this.notification.add(
                        `“${documentName}” was archived successfully.`,
                        { type: "success" }
                    );
                    const selected = this.state.selected;
                    await this.load();
                    if (!isActive()) {
                        return;
                    }
                    if (selected) {
                        await this.openDocumentById(selected.id);
                    }
                    if (!isActive()) {
                        return;
                    }
                    this.state.operation = null;
                    return;
                }
                if (status.state === "failed") {
                    this.notification.add(
                        status.error || "Paperless processing failed.",
                        { type: "danger", sticky: true }
                    );
                    await this.load();
                    return;
                }
                await new Promise((resolve) => setTimeout(resolve, 2000));
                if (!isActive()) {
                    return;
                }
            }
            if (isActive()) {
                this.notification.add(
                    "Processing is taking longer than usual. You can leave this page; " +
                        "the status will be restored when you return.",
                    { type: "info" }
                );
            }
        } finally {
            this.pollingOperationIds.delete(operationId);
        }
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
    DocumentsWorkspace,
    { force: true }
);
