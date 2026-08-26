import { beforeEach, expect, test } from "@odoo/hoot";
import { animationFrame, runAllTimers, tick } from "@odoo/hoot-mock";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";

import {
    contains,
    mockService,
    mountWithCleanup,
    onRpc,
    patchWithCleanup,
} from "@web/../tests/web_test_helpers";

import { DocumentPreview, DocumentsWorkspace } from "../src/documents_app";
import { browser } from "@web/core/browser/browser";
import { user } from "@web/core/user";

defineMailModels();

function storageKey(suffix) {
    return `usl_documents.workspace.${user.userId}.${suffix}`;
}

beforeEach(() => {
    browser.sessionStorage.removeItem(storageKey("global"));
    browser.sessionStorage.removeItem(storageKey("account.move.12"));
    const url = new URL(browser.location.href);
    url.searchParams.delete("usl_document");
    url.searchParams.delete("usl_version");
    url.searchParams.delete("usl_filters");
    url.searchParams.delete("domain");
    url.searchParams.delete("groupBy");
    url.searchParams.delete("orderBy");
    browser.history.replaceState({}, "", url.toString());
});

const emptyWorkspace = {
    documents: [],
    count: 0,
    page: 1,
    page_size: 24,
    degraded: false,
    metadata_included: true,
    truncated: false,
    can_upload: true,
    active_operation: false,
    failed_operations: [],
    selected_workspace: "recent",
    companies: [],
    tags: [],
    correspondents: [],
    document_types: [],
    custom_fields: [],
    smart_views: [
        {
            id: 1,
            key: "recent",
            name: "Recently added",
            icon: "fa-clock-o",
            personal: false,
            filters: {},
        },
        {
            id: 2,
            key: "all",
            name: "All documents",
            icon: "fa-archive",
            personal: false,
            filters: {},
        },
    ],
    link_facets: [],
};

const searchViewArch = `
    <search>
        <field name="all_text"/>
        <field name="semantic_text"/>
        <field name="name"/>
        <field name="archive_text"/>
        <field name="tag_ids"/>
        <field name="company_id"/>
        <field name="document_type_id"/>
        <field name="correspondent_id"/>
        <field name="document_date"/>
        <field name="custom_field_text" invisible="1"/>
        <filter name="needs_review" string="Needs review"
                domain="[('review_state', '=', 'needs_attention')]"/>
        <group>
            <filter name="group_company" string="Company"
                    context="{'group_by': 'company_id'}"/>
        </group>
    </search>
`;

const searchViewFields = {
    all_text: {
        name: "all_text",
        string: "Search everywhere — words + meaning",
        type: "char",
    },
    semantic_text: {
        name: "semantic_text",
        string: "Semantic search — meaning only",
        type: "char",
    },
    name: { name: "name", string: "Title", type: "char" },
    archive_text: { name: "archive_text", string: "Document content", type: "char" },
    custom_field_text: {
        name: "custom_field_text",
        string: "Additional details",
        type: "char",
    },
    tag_ids: {
        name: "tag_ids",
        string: "Tags",
        type: "many2many",
        relation: "usl.paperless.tag",
    },
    company_id: {
        name: "company_id",
        string: "Company",
        type: "many2one",
        relation: "res.company",
    },
    document_type_id: {
        name: "document_type_id",
        string: "Document type",
        type: "many2one",
        relation: "usl.paperless.document.type",
    },
    correspondent_id: {
        name: "correspondent_id",
        string: "Correspondent",
        type: "many2one",
        relation: "usl.paperless.correspondent",
    },
    document_date: {
        name: "document_date",
        string: "Document date",
        type: "date",
    },
    review_state: {
        name: "review_state",
        string: "Review status",
        type: "selection",
        selection: [
            ["needs_attention", "Needs review"],
            ["classified", "Classified"],
        ],
    },
};

function action(params = {}) {
    return {
        name: "Documents",
        tag: "usl_documents.workspace",
        type: "ir.actions.client",
        params: {
            search_view_arch: searchViewArch,
            search_view_fields: searchViewFields,
            ...params,
        },
    };
}

test("PDF preview renders after its conditional canvas is mounted", async () => {
    patchWithCleanup(DocumentPreview.prototype, {
        async load() {
            this.pdf = { destroy() {} };
            Object.assign(this.state, {
                loading: false,
                kind: "pdf",
                page: 1,
                pageCount: 2,
            });
        },
        async renderPdfPage() {
            const canvas = this.canvas.el;
            if (!canvas || !this.pdf || this.state.kind !== "pdf") {
                return;
            }
            canvas.width = 640;
            canvas.height = 905;
            this.renderedPdfKey = "test-render";
            expect.step("rendered with mounted canvas");
        },
    });

    await mountWithCleanup(DocumentPreview, {
        props: { url: "/usl_documents/7/preview", versionId: "9" },
    });
    await animationFrame();

    expect.verifySteps(["rendered with mounted canvas"]);
    expect(".o_usl_pdf_preview canvas").toHaveAttribute("width", "640");
    expect(".o_usl_pdf_preview canvas").toHaveAttribute("height", "905");
});

test("empty archive state is explicit and supports zero-filing upload", async () => {
    onRpc("usl.document", "workspace_data", () => emptyWorkspace);

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });

    expect(".o_usl_documents_empty").toHaveText(/No documents found/);
    expect("label.btn-primary").toHaveText(/Upload/);
    expect("input[type=file]").toHaveCount(1);
});

test("degraded state keeps Odoo available and offers retry", async () => {
    onRpc("usl.document", "workspace_data", () => ({
        ...emptyWorkspace,
        degraded: true,
        error: "Paperless is unavailable. Odoo remains usable; retry later.",
    }));

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });

    expect(".alert-warning").toHaveText(/Archive unavailable/);
    expect(".o_usl_documents_empty").toHaveText(/temporarily unavailable/);
    expect(".o_usl_documents_empty button.btn-primary").toHaveText(/Try again/);
});

test("document details stay useful and actionable during an archive outage", async () => {
    const document = {
        id: 61,
        name: "Cached supplier evidence",
        availability_state: "available",
        access_error: false,
        tags: [],
        link_count: 0,
    };
    onRpc("usl.document", "workspace_data", () => ({
        ...emptyWorkspace,
        documents: [document],
        count: 1,
    }));
    onRpc("usl.document", "document_detail", ({ kwargs }) => {
        expect(kwargs.check_archive).toBe(true);
        return {
            ...document,
            archive_available: false,
            can_edit: true,
            can_change_links: true,
            can_manage: true,
            versions: [],
            links: [],
        };
    });

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });
    await contains(".o_usl_document_card").click();
    await animationFrame();

    expect(".o_usl_documents_detail .alert-warning.mb-0").toHaveText(
        /Archive temporarily unavailable/
    );
    expect(".o_usl_documents_detail .alert-warning.mb-0").toHaveText(
        /Odoo links and business records are unaffected/
    );
    expect(".o_usl_document_preview").toHaveCount(0);
    expect("[data-testid='document-open-preview']").toHaveCount(0);
    expect(".o_usl_documents_detail .alert-warning.mb-0 button").toHaveText(
        /Try again/
    );
});

test("read-only evidence users do not see upload controls", async () => {
    onRpc("usl.document", "workspace_data", () => ({
        ...emptyWorkspace,
        can_upload: false,
    }));

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });

    expect("label.btn-primary").toHaveCount(0);
    expect("input[type=file]").toHaveCount(0);
    expect(".o_usl_documents_empty").toHaveText(
        /No accessible documents match this view/
    );
});

test("failed ingestion remains actionable in Needs review", async () => {
    onRpc("usl.document", "workspace_data", () => ({
        ...emptyWorkspace,
        selected_workspace: "attention",
        failed_operations: [
            {
                id: 19,
                name: "corrupted.pdf",
                state: "failed",
                error: "The file is corrupted.",
            },
        ],
    }));

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });

    expect(".alert-warning").toHaveText(/corrupted\.pdf/);
    expect(".alert-warning").toHaveText(/Choose file to retry/);
    expect(".alert-warning").toHaveText(/Dismiss/);
});

test("workspace search state survives record navigation", async () => {
    const key = storageKey("account.move.12");
    browser.sessionStorage.setItem(
        key,
        JSON.stringify({
            query: "embedded cobalt phrase",
            companyId: "4",
            documentTypeId: "8",
            workspace: "all",
            view: "list",
            sort: "title",
            page: 2,
        })
    );
    onRpc("usl.document", "workspace_data", ({ kwargs }) => {
        expect(kwargs.query).toBe("");
        expect(kwargs.search_domain).toEqual([]);
        expect(kwargs.page).toBe(2);
        return emptyWorkspace;
    });

    await mountWithCleanup(DocumentsWorkspace, {
        props: {
            action: action({ res_model: "account.move", res_id: 12 }),
        },
    });

    expect(".o_searchview_input").toHaveCount(1);
    expect(
        JSON.parse(
            browser.sessionStorage.getItem(storageKey("global"))
        ).companyId
    ).toBe("4");
    browser.sessionStorage.removeItem(key);
    browser.sessionStorage.removeItem(storageKey("global"));
});

test("native search facets survive a linked-record round trip", async () => {
    browser.sessionStorage.setItem(
        storageKey("global"),
        JSON.stringify({
            workspace: "all",
            nativeSearch: {
                key: 42,
                facets: [
                    {
                        type: "field",
                        title: "Search everywhere",
                        values: ["embedded cobalt phrase"],
                        separator: "or",
                        domain: '[("all_text", "ilike", "embedded cobalt phrase")]',
                    },
                ],
                domain: '[("all_text", "ilike", "embedded cobalt phrase")]',
                groupBys: [],
            },
        })
    );
    let searchDomain = [];
    onRpc("usl.document", "workspace_data", ({ kwargs }) => {
        searchDomain = kwargs.search_domain;
        return emptyWorkspace;
    });

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });

    expect(searchDomain).toEqual([
        ["all_text", "ilike", "embedded cobalt phrase"],
    ]);
    expect(".o_searchview_facet").toHaveText(/Search everywhere/);
    expect(".o_searchview_facet").toHaveText(/embedded cobalt phrase/);
});

test("a record smart button starts from an uncluttered linked-record view", async () => {
    browser.sessionStorage.setItem(
        storageKey("global"),
        JSON.stringify({
            workspace: "trash",
            query: "unrelated search",
            tagIds: [99],
        })
    );
    onRpc("usl.document", "workspace_data", ({ kwargs }) => {
        expect(kwargs.workspace).toBe("archive_search");
        expect(kwargs.query).toBe("");
        expect(kwargs.shortcut_tag_ids).toEqual([]);
        expect(kwargs.search_domain).toEqual([
            ["linked_record_ref", "=", "account.move:12"],
        ]);
        expect(kwargs.linked_model).toBe("account.move");
        expect(kwargs.linked_id).toBe(12);
        return emptyWorkspace;
    });

    await mountWithCleanup(DocumentsWorkspace, {
        props: {
            action: action({
                res_model: "account.move",
                res_id: 12,
                linked_filter: true,
            }),
        },
    });

    expect(".o_searchview_input").toHaveValue("");
});

test("detail prioritizes title, native day-first date, versions, and links", async () => {
    const document = {
        id: 7,
        name: "Supplier invoice",
        paperless_id: 42,
        date: "2026-07-29",
        company: "USL",
        confidentiality: "accounting",
        review_state: "classified",
        availability_state: "available",
        access_error: false,
        correspondent: "Supplier",
        correspondent_id: 4,
        document_type: "Invoice",
        document_type_id: 8,
        tags: [
            {
                id: 9,
                name: "Accounting",
                color: "#336699",
                text_color: "#ffffff",
            },
        ],
        filename: "invoice.pdf",
        source: "odoo_upload",
        link_count: 1,
        version_count: 1,
        paperless_url: "https://documents.example.test/documents/42/details",
    };
    onRpc("usl.document", "workspace_data", () => ({
        ...emptyWorkspace,
        tags: document.tags,
        correspondents: [{ id: 4, name: "Supplier" }],
        document_types: [{ id: 8, name: "Invoice" }],
        documents: [document],
        count: 1,
    }));
    onRpc("usl.document", "document_detail", async () => {
        // Reproduce the real workspace transition: the cached card renders
        // before the permission-aware document detail is available.
        await animationFrame();
        return {
            ...document,
            checksum: "a".repeat(64),
            can_edit: true,
            can_change_links: true,
            can_manage: true,
            versions: [
                {
                    id: 3,
                    paperless_version_id: "9",
                    label: "Received original",
                    filename: "invoice.pdf",
                    checksum: "a".repeat(64),
                    is_current: true,
                    is_received_original: true,
                    preview_url: "/usl_documents/7/preview?version=9",
                    original_url:
                        "/usl_documents/7/download?original=1&version=9",
                    archive_url:
                        "/usl_documents/7/download?original=0&version=9",
                },
            ],
            links: [
                {
                    id: 5,
                    record_name: "BILL/2026/0042",
                    model_label: "Journal Entry",
                    model: "account.move",
                    res_id: 12,
                    linked_by: "Valentin",
                },
            ],
        };
    });

    await mountWithCleanup(DocumentsWorkspace, {
        props: {
            action: action({ res_model: "account.move", res_id: 12 }),
        },
    });
    const listHistoryLength = browser.history.length;
    await contains(".o_usl_document_card").click();
    await animationFrame();
    expect(browser.history.length).toBe(listHistoryLength + 1);

    expect(".o_usl_inline_metadata").toHaveCount(1);
    expect(".o_usl_inline_metadata select").toHaveCount(0);
    expect("#usl_document_title").toHaveValue("Supplier invoice");
    expect("#usl_document_title").toHaveClass("o_usl_detail_title_input");
    expect(".o_usl_documents_detail").not.toHaveText(/Classification/);
    expect("#usl_document_type").toHaveValue("Invoice");
    expect("#usl_document_correspondent").toHaveValue("Supplier");
    expect("#usl_document_date").toHaveCount(1);
    expect("#usl_document_date").toHaveValue("29/07/2026");
    expect("#usl_document_date").toHaveAttribute("type", "text");
    await contains("#usl_document_date").click();
    expect(".o_datetime_picker").toHaveCount(1);
    expect(".o_datetime_picker .o_selected").toHaveText("29");
    await contains("#usl_document_date").press("Escape");
    expect(".o_datetime_picker").toHaveCount(0);
    expect(".o_usl_detail_section h6").toHaveText(/File versions/);
    expect(".o_usl_detail_section .text-bg-primary").toHaveText(/Current/);
    expect(".o_usl_documents_detail").toHaveText(/Received original/);
    expect(".o_usl_documents_detail").toHaveText(/BILL\/2026\/0042/);
    expect(".o_usl_documents_detail").not.toHaveText(/synchronized/i);
    expect(".o_usl_documents_detail").toHaveText(/Accounting/);
    expect(".o_usl_document_preview").toHaveAttribute("data-version", "9");
    expect("a[href*='original=0']").toHaveCount(2);
    expect(".o_usl_detail_header_actions").toHaveAttribute(
        "aria-label",
        "Document links"
    );
    expect("[data-testid='document-open-preview']").toHaveText(/Open Preview/);
    expect("[data-testid='document-open-preview']").toHaveAttribute(
        "href",
        "/usl_documents/7/preview?version=9"
    );
    expect("[data-testid='document-open-preview']").toHaveAttribute(
        "aria-label",
        "Open document preview in a new tab"
    );
    expect("[data-testid='document-open-preview']").toHaveAttribute(
        "target",
        "_blank"
    );
    expect("[data-testid='document-open-preview']").toHaveAttribute(
        "rel",
        "noopener noreferrer"
    );
    expect("[data-testid='document-open-paperless']").toHaveText(
        /Open in Paperless/
    );
    expect("[data-testid='document-open-paperless']").toHaveAttribute(
        "href",
        "https://documents.example.test/documents/42/details"
    );
    expect("[data-testid='document-open-paperless']").toHaveAttribute(
        "aria-label",
        "Open document in Paperless in a new tab"
    );
    expect("[data-testid='document-open-paperless']").toHaveAttribute(
        "target",
        "_blank"
    );
    expect("[data-testid='document-open-paperless']").toHaveAttribute(
        "rel",
        "noopener noreferrer"
    );
    expect(".o_usl_documents_detail footer a.btn-primary").toHaveText(
        /Download original/
    );
    await contains(".o_usl_action_menu summary").click();
    expect(".o_usl_documents_detail footer").toHaveText(/Upload new version/);
    expect(".o_usl_documents_detail footer").toHaveText(
        /Remove link from this record/
    );
    expect(".o_usl_documents_detail footer").not.toHaveText(/Open in Paperless/);
    expect(
        new URL(browser.location.href).searchParams.get("usl_document")
    ).toBe("7");
    expect(browser.history.state.nextState.usl_document).toBe(7);
    expect(browser.history.state.skipRouteChange).toBe(true);
    let popStateCount = 0;
    const countPopState = () => {
        popStateCount += 1;
    };
    browser.addEventListener("popstate", countPopState);
    browser.history.back();
    await animationFrame();
    await animationFrame();
    await animationFrame();
    browser.removeEventListener("popstate", countPopState);
    expect(popStateCount).toBe(1);
    expect(".o_usl_documents_detail").toHaveCount(0);
    expect(
        new URL(browser.location.href).searchParams.get("usl_document")
    ).toBe(null);
    browser.history.forward();
    await animationFrame();
    expect(".o_usl_documents_detail").toHaveCount(1);
});

test("review banner completes review without opening the technical record", async () => {
    let reviewState = "needs_attention";
    let markReviewedCalls = 0;
    const document = {
        id: 71,
        name: "Quarterly tax pack",
        paperless_id: 171,
        date: "2026-07-30",
        company: "USL",
        company_id: 1,
        review_state: reviewState,
        availability_state: "available",
        access_error: false,
        correspondent: "Tax authority",
        document_type: "Tax filing",
        tags: [],
        link_count: 0,
    };
    const detail = () => ({
        ...document,
        review_state: reviewState,
        can_edit: true,
        can_change_links: true,
        can_manage: true,
        can_mark_reviewed: reviewState !== "reviewed",
        review_blocker: false,
        archive_available: true,
        links: [],
        versions: [],
    });
    onRpc("usl.document", "workspace_data", () => ({
        ...emptyWorkspace,
        documents: [{ ...document, review_state: reviewState }],
        count: 1,
    }));
    onRpc("usl.document", "document_detail", () => detail());
    onRpc("usl.document", "action_mark_reviewed", ({ args }) => {
        expect(args).toEqual([[71]]);
        markReviewedCalls += 1;
        reviewState = "reviewed";
        return detail();
    });

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });
    await contains(".o_usl_document_card").click();
    await animationFrame();

    expect(".o_usl_review_banner").toHaveText(/Review this document/);
    expect(".o_usl_review_banner").toHaveText(
        /Check the company, classification, and linked records/
    );
    await contains(".o_usl_review_banner button", {
        text: "Mark reviewed",
    }).click();
    await animationFrame();

    expect(markReviewedCalls).toBe(1);
    expect(".o_usl_review_banner").toHaveCount(0);
    expect(".o_usl_document_card .text-bg-warning").toHaveCount(0);
    expect(".o_notification").toHaveText(
        /“Quarterly tax pack” marked as reviewed/
    );
});

test("review banner explains a blocking decision without a technical detour", async () => {
    const document = {
        id: 72,
        name: "Unassigned evidence",
        paperless_id: 172,
        date: "2026-07-30",
        company: false,
        company_id: false,
        review_state: "needs_attention",
        availability_state: "available",
        access_error: false,
        correspondent: false,
        document_type: false,
        tags: [],
        link_count: 0,
    };
    onRpc("usl.document", "workspace_data", () => ({
        ...emptyWorkspace,
        documents: [document],
        count: 1,
    }));
    onRpc("usl.document", "document_detail", () => ({
        ...document,
        can_edit: true,
        can_change_links: true,
        can_manage: true,
        can_mark_reviewed: false,
        review_blocker: "Choose the legal company before completing the review.",
        archive_available: true,
        links: [],
        versions: [],
    }));

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });
    await contains(".o_usl_document_card").click();
    await animationFrame();

    expect(".o_usl_review_banner").toHaveText(/Choose the legal company/);
    expect(".o_usl_review_banner button").toHaveAttribute("disabled");
});

test("selected document survives a reload after the host router normalizes the URL", async () => {
    const document = {
        id: 77,
        name: "Reload-safe contract",
        paperless_id: 177,
        date: "2026-07-30",
        company: "USL",
        review_state: "reviewed",
        availability_state: "available",
        access_error: false,
        correspondent: "Northstar Retail",
        document_type: "Contract",
        tags: [],
        link_count: 0,
    };
    browser.sessionStorage.setItem(
        storageKey("global"),
        JSON.stringify({
            workspace: "all",
            selectedDocumentId: document.id,
            selectedVersionId: "12",
        })
    );
    onRpc("usl.document", "workspace_data", () => ({
        ...emptyWorkspace,
        documents: [document],
        count: 1,
    }));
    onRpc("usl.document", "document_detail", ({ args }) => {
        expect(args).toEqual([77]);
        return {
            ...document,
            can_edit: true,
            can_change_links: true,
            can_manage: true,
            links: [],
            versions: [
                {
                    id: 12,
                    paperless_version_id: "12",
                    label: "Received original",
                    is_current: true,
                    is_received_original: true,
                    preview_url: "/usl_documents/77/preview?version=12",
                    original_url:
                        "/usl_documents/77/download?original=1&version=12",
                    archive_url:
                        "/usl_documents/77/download?original=0&version=12",
                },
            ],
        };
    });

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });
    await animationFrame();

    expect("#usl_document_title").toHaveValue("Reload-safe contract");
    expect(".o_usl_documents_detail header").toHaveText(/30\/07\/2026/);
    expect(
        new URL(browser.location.href).searchParams.get("usl_document")
    ).toBe("77");
    expect(
        new URL(browser.location.href).searchParams.get("usl_version")
    ).toBe("12");
});

test("top tag shortcuts compose with native search facets", async () => {
    const tags = [
        { id: 21, name: "Tax & reporting", color: "#31a354" },
        { id: 22, name: "Contracts & legal", color: "#8c6bb1" },
    ];
    let lastDomain = [];
    onRpc("usl.document", "workspace_data", ({ kwargs }) => {
        expect(kwargs.shortcut_tag_ids).toEqual([]);
        lastDomain = kwargs.search_domain;
        return { ...emptyWorkspace, tags };
    });
    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });

    expect(".o_cp_searchview").toHaveCount(1);
    await contains(".o_usl_filter_shortcuts .o_usl_tag_chip", {
        text: "Tax & reporting",
    }).click();
    expect(lastDomain).toEqual([["tag_ids", "in", [21]]]);
    expect(".o_searchview_facet").toHaveText(/Tag: Tax & reporting/);
    expect(".o_usl_filter_shortcuts .is-selected").toHaveText(/Tax & reporting/);
    await contains(".o_usl_filter_shortcuts .o_usl_tag_chip", {
        text: "Contracts & legal",
    }).click();
    expect(lastDomain).toEqual([["tag_ids", "in", [21, 22]]]);
    expect(".o_searchview_facet").toHaveText(
        /Tags: Tax & reporting or Contracts & legal/
    );
    expect(".o_usl_filter_shortcuts .is-selected").toHaveCount(2);
    await contains(".o_usl_filter_shortcuts .is-selected", {
        text: "Tax & reporting",
    }).click();
    expect(lastDomain).toEqual([["tag_ids", "in", [22]]]);
    expect(".o_searchview_facet").toHaveText(/Tag: Contracts & legal/);
    expect(".o_usl_filter_shortcuts .is-selected").toHaveCount(1);
});

test("restored tag OR facet stays synchronized with top shortcuts", async () => {
    const tags = [
        { id: 21, name: "Tax & reporting", color: "#31a354" },
        { id: 22, name: "Contracts & legal", color: "#8c6bb1" },
    ];
    const url = new URL(browser.location.href);
    url.searchParams.set("domain", '[("tag_ids", "in", [21, 22])]');
    browser.history.replaceState({}, "", url.toString());
    browser.sessionStorage.setItem(
        storageKey("global"),
        JSON.stringify({ workspace: "recent", tagIds: [21, 22] })
    );
    let lastDomain = [];
    onRpc("usl.document", "workspace_data", ({ kwargs }) => {
        expect(kwargs.shortcut_tag_ids).toEqual([]);
        lastDomain = kwargs.search_domain;
        return { ...emptyWorkspace, tags };
    });

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });

    expect(lastDomain).toEqual([["tag_ids", "in", [21, 22]]]);
    expect(".o_usl_filter_shortcuts .is-selected").toHaveCount(2);
    expect(".o_searchview_facet").toHaveCount(1);
    await contains(".o_usl_filter_shortcuts .is-selected", {
        text: "Tax & reporting",
    }).click();
    expect(lastDomain).toEqual([["tag_ids", "in", [22]]]);
    expect(".o_usl_filter_shortcuts .is-selected").toHaveCount(1);
});

test("large tag catalogs stay readable in a bounded searchable picker", async () => {
    const tags = Array.from({ length: 20 }, (_, index) => ({
        id: index + 1,
        name:
            index === 19
                ? "A deliberately long archive classification tag"
                : `Archive tag ${String(index + 1).padStart(2, "0")}`,
        color: "#31a354",
        document_count: 20 - index,
    }));
    let lastDomain = [];
    onRpc("usl.document", "workspace_data", ({ kwargs }) => {
        lastDomain = kwargs.search_domain;
        return { ...emptyWorkspace, tags };
    });

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });

    expect(
        ".o_usl_filter_shortcuts > .o_usl_tag_chip"
    ).toHaveCount(6);
    expect(".o_usl_more_tags summary").toHaveText(/More tags \(14\)/);
    await contains(".o_usl_more_tags summary").click();
    await contains(".o_usl_more_tags input").fill("deliberately long");
    expect(".o_usl_more_tags_results .dropdown-item").toHaveCount(1);
    expect(".o_usl_more_tags_results .dropdown-item").toHaveAttribute(
        "title",
        "Filter by tag: A deliberately long archive classification tag"
    );
    await contains(".o_usl_more_tags_results .dropdown-item").click();
    expect(lastDomain).toEqual([["tag_ids", "in", [20]]]);
    expect(".o_searchview_facet").toHaveText(
        /Tag: A deliberately long archive classification tag/
    );
});

test("Back from a record-context workspace returns to the linked record", async () => {
    let returnedAction = null;
    let returnedOptions = null;
    mockService("action", {
        async doAction(actionToRun, options) {
            returnedAction = actionToRun;
            returnedOptions = options;
        },
    });
    onRpc("usl.document", "workspace_data", () => emptyWorkspace);

    await mountWithCleanup(DocumentsWorkspace, {
        props: {
            action: action({
                res_model: "account.move",
                res_id: 12,
                linked_filter: true,
            }),
        },
    });
    expect(
        browser.history.state.uslDocumentsRecordContext ||
            browser.history.state.nextState?.uslDocumentsRecordContext
    ).toBe("account.move:12");

    const popState = new Event("popstate");
    Object.defineProperty(popState, "state", {
        value: {
            nextState: { actionStack: [] },
        },
    });
    browser.dispatchEvent(popState);
    await tick();

    expect(returnedAction).toEqual({
        type: "ir.actions.act_window",
        res_model: "account.move",
        res_id: 12,
        views: [[false, "form"]],
        target: "current",
    });
    expect(returnedOptions).toEqual({ clearBreadcrumbs: true });
});

test("native search defaults to broad archive search and keeps specialist fields advanced", async () => {
    let lastDomain = [];
    onRpc("usl.document", "workspace_data", ({ kwargs }) => {
        lastDomain = kwargs.search_domain;
        return emptyWorkspace;
    });
    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });

    await contains(".o_searchview_input").fill("heliotrope");
    expect(".o_searchview_autocomplete").toHaveText(
        /Search everywhere — words \+ meaning/
    );
    expect(".o_searchview_autocomplete").toHaveText(
        /Semantic search — meaning only/
    );
    expect(".o_searchview_autocomplete").toHaveText(/Document content/);
    expect(".o_searchview_autocomplete").not.toHaveText(/Additional details/);
    await contains(".o_searchview_autocomplete .o-dropdown-item").click();

    expect(lastDomain).toEqual([
        ["all_text", "ilike", "heliotrope"],
    ]);
    expect(".o_searchview_facet").toHaveText(/Search everywhere/);
    expect(".o_searchview_facet").toHaveText(/heliotrope/);
    const encodedDomain = browser.location.href
        .split("domain=")[1]
        .split("&")[0];
    expect(encodedDomain.includes("+")).toBe(false);
    expect(decodeURIComponent(encodedDomain)).toBe(
        '[("all_text", "ilike", "heliotrope")]'
    );
});

test("broad search shows exact results before semantic refinement", async () => {
    const calls = [];
    let releaseSemantic;
    onRpc("usl.document", "workspace_data", ({ kwargs }) => {
        calls.push(kwargs);
        const isBroadSearch = JSON.stringify(kwargs.search_domain).includes(
            "all_text"
        );
        if (isBroadSearch && kwargs.search_mode === "hybrid") {
            return new Promise((resolve) => {
                releaseSemantic = () =>
                    resolve({
                        ...emptyWorkspace,
                        metadata_included: false,
                        count: 2,
                        documents: [
                            { id: 1, name: "Exact match", tags: [] },
                            { id: 2, name: "Meaning match", tags: [] },
                        ],
                });
            });
        }
        if (!isBroadSearch) {
            return emptyWorkspace;
        }
        return {
            ...emptyWorkspace,
            count: 1,
            documents: [{ id: 1, name: "Exact match", tags: [] }],
        };
    });
    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });

    await contains(".o_searchview_input").fill("renewal obligation");
    await contains(".o_searchview_autocomplete .o-dropdown-item").click();
    expect(calls.at(-1).search_mode).toBe("hybrid");
    expect(calls.at(-2).search_mode).toBe("exact");
    expect(calls.at(-1).include_workspace_metadata).toBe(false);
    expect(".o_usl_document_card").toHaveCount(1);
    expect(".alert-info").toHaveText(/Exact matches are ready/);

    releaseSemantic();
    await animationFrame();
    expect(".o_usl_document_card").toHaveCount(2);
    expect(".alert-info").toHaveCount(0);
});

test.tags("desktop");
test("tags are searchable, removable, and creatable from document details", async () => {
    const existingTag = {
        id: 31,
        name: "Accounting",
        color: "#355f9f",
        text_color: "#ffffff",
    };
    const newTag = {
        id: 32,
        name: "Board approved",
        color: "#4f6fad",
        text_color: "#ffffff",
    };
    const document = {
        id: 30,
        name: "Board package",
        paperless_id: 130,
        date: "2026-07-29",
        company: "USL",
        review_state: "classified",
        availability_state: "available",
        access_error: false,
        correspondent: "",
        document_type: "",
        tags: [existingTag],
        link_count: 0,
        primary_link: false,
    };
    let detail = {
        ...document,
        can_edit: true,
        can_change_links: true,
        can_manage: false,
        versions: [],
        links: [],
    };
    onRpc("usl.document", "workspace_data", () => ({
        ...emptyWorkspace,
        tags: [existingTag],
        documents: [{ ...document, tags: detail.tags }],
        count: 1,
    }));
    onRpc("usl.document", "document_detail", () => detail);
    onRpc("usl.paperless.tag", "create", () => [newTag.id]);
    onRpc("usl.paperless.tag", "web_name_search", () => []);
    onRpc("usl.document", "update_archive_metadata", ({ args }) => {
        const tagIds = args[1].tag_ids;
        detail = {
            ...detail,
            tags: [existingTag, newTag].filter((tag) => tagIds.includes(tag.id)),
        };
        return detail;
    });

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });
    await contains(".o_usl_document_card").click();
    await animationFrame();
    await contains(".o_usl_tag_autocomplete input").edit("Board approved", {
        confirm: false,
    });
    await runAllTimers();
    await contains(".o_m2o_dropdown_option_create").click();
    expect(".o_usl_detail_tags").toHaveText(/Board approved/);
    await contains(".o_usl_tag_autocomplete input").click();
    expect(".o-autocomplete--dropdown-menu").toHaveCount(1);
    await contains("section h6", { text: "Linked records" }).click();
    expect(".o-autocomplete--dropdown-menu").toHaveCount(0);
    await contains("button[aria-label='Remove tag Accounting']").click();
    expect("button[aria-label='Remove tag Accounting']").toHaveCount(0);
});

test.tags("desktop");
test("correspondent and document type quick creation save inline without an edit mode", async () => {
    const document = {
        id: 33,
        name: "Unclassified evidence",
        paperless_id: 133,
        date: "2026-07-30",
        company: "USL",
        review_state: "needs_attention",
        availability_state: "available",
        access_error: false,
        correspondent: "",
        correspondent_id: false,
        document_type: "",
        document_type_id: false,
        tags: [],
        link_count: 0,
        primary_link: false,
    };
    let detail = {
        ...document,
        can_edit: true,
        can_change_links: true,
        can_manage: false,
        versions: [],
        links: [],
    };
    onRpc("usl.document", "workspace_data", () => ({
        ...emptyWorkspace,
        documents: [{ ...document, ...detail }],
        count: 1,
    }));
    onRpc("usl.document", "document_detail", () => detail);
    onRpc("usl.paperless.document.type", "web_name_search", () => []);
    onRpc("usl.paperless.correspondent", "web_name_search", () => []);
    onRpc("res.partner", "web_name_search", () => []);
    onRpc("usl.paperless.document.type", "create", () => [41]);
    onRpc("usl.paperless.correspondent", "create", () => [42]);
    onRpc("usl.document", "update_archive_metadata", ({ args }) => {
        const values = args[1];
        if (values.document_type_id === 41) {
            detail = {
                ...detail,
                document_type_id: 41,
                document_type: "Policy",
            };
        }
        if (values.correspondent_id === 42) {
            detail = {
                ...detail,
                correspondent_id: 42,
                correspondent: "Archive sender",
            };
        }
        return detail;
    });

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });
    await contains(".o_usl_document_card").click();
    await animationFrame();

    await contains("#usl_document_type").edit("Policy", { confirm: false });
    await runAllTimers();
    await contains(".o_m2o_dropdown_option_create").click();
    expect("#usl_document_type").toHaveValue("Policy");

    await contains("#usl_document_correspondent").edit("Archive sender", {
        confirm: false,
    });
    await runAllTimers();
    await contains(".o_m2o_dropdown_option_create").click();
    expect("#usl_document_correspondent").toHaveValue("Archive sender");
    expect(".o_usl_inline_metadata select").toHaveCount(0);
});

test.tags("desktop");
test("a manager changes the Odoo-owned company inline", async () => {
    const document = {
        id: 34,
        name: "Unlinked company evidence",
        paperless_id: 134,
        date: "2026-07-30",
        company: "USL",
        company_id: 1,
        review_state: "classified",
        availability_state: "available",
        access_error: false,
        correspondent: "",
        correspondent_id: false,
        document_type: "",
        document_type_id: false,
        tags: [],
        link_count: 0,
        primary_link: false,
    };
    let detail = {
        ...document,
        can_edit: true,
        can_change_company: true,
        can_change_links: true,
        can_manage: true,
        versions: [],
        links: [],
    };
    let selectedCompanyId = false;
    onRpc("usl.document", "workspace_data", () => ({
        ...emptyWorkspace,
        companies: [
            { id: 1, name: "USL" },
            { id: 2, name: "Restricted Company" },
        ],
        documents: [{ ...document, ...detail }],
        count: 1,
    }));
    onRpc("usl.document", "document_detail", () => detail);
    onRpc("res.company", "web_name_search", () => [
        { id: 2, display_name: "Restricted Company" },
    ]);
    onRpc("usl.document", "set_company", ({ args }) => {
        selectedCompanyId = args[1];
        detail = {
            ...detail,
            company: "Restricted Company",
            company_id: selectedCompanyId,
        };
        return detail;
    });

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });
    await contains(".o_usl_document_card").click();
    await animationFrame();

    await contains("#usl_document_company").edit("Restricted", {
        confirm: false,
    });
    await runAllTimers();
    await contains(".o-autocomplete--dropdown-item", {
        text: "Restricted Company",
    }).click();
    await animationFrame();

    expect(selectedCompanyId).toBe(2);
    expect("#usl_document_company").toHaveValue("Restricted Company");
});

test("native Filters, Group By, Favorites and tag shortcuts stay uncluttered", async () => {
    const tags = [
        { id: 1, name: "Banking", color: "#225588", text_color: "#ffffff" },
        { id: 2, name: "Tax", color: "#228855", text_color: "#ffffff" },
        { id: 3, name: "Reviewed", color: "#885522", text_color: "#ffffff" },
        { id: 4, name: "July", color: "#662288", text_color: "#ffffff" },
    ];
    let calls = 0;
    const workspaces = [];
    onRpc("usl.document", "workspace_data", ({ kwargs }) => {
        calls++;
        workspaces.push(kwargs);
        return {
            ...emptyWorkspace,
            tags,
            smart_views: emptyWorkspace.smart_views.map((view) => ({
                ...view,
                quick_filters:
                    view.key === "recent"
                        ? [
                              {
                                  id: 1,
                                  key: "group_company",
                                  name: "Group by company",
                                  icon: "fa-building-o",
                                  kind: "group",
                                  group_by: ["company_id"],
                                  domain: [],
                              },
                              {
                                  id: 2,
                                  key: "needs_review",
                                  name: "Needs review",
                                  icon: "fa-exclamation-circle",
                                  kind: "filter",
                                  group_by: [],
                                  domain: [
                                      [
                                          "review_state",
                                          "=",
                                          "needs_attention",
                                      ],
                                  ],
                              },
                          ]
                        : [],
            })),
            documents: [
                {
                    id: 10,
                    name: "Bank statement",
                    date: "2026-07-01",
                    review_state: "classified",
                    availability_state: "available",
                    access_error: false,
                    correspondent: "Example Bank",
                    document_type: "Statement",
                    tags,
                    link_count: 0,
                    primary_link: false,
                },
            ],
            count: 1,
        };
    });

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });

    expect(".o_usl_document_card .o_usl_tag_chip").toHaveCount(3);
    expect(".o_usl_document_card").toHaveText(/\+1/);
    expect(".o_usl_more_filters").toHaveCount(0);
    await contains(".o_searchview_dropdown_toggler").click();
    expect(".o_search_bar_menu").toHaveText(/Filters/);
    expect(".o_search_bar_menu").toHaveText(/Group By/);
    expect(".o_search_bar_menu").toHaveText(/Favorites/);
    await contains(".o_searchview_dropdown_toggler").click();
    await contains(".o_usl_filter_shortcuts button", {
        text: "Group by company",
    }).click();
    expect(calls).toBe(2);
    expect(workspaces.at(-1).group_by).toEqual(["company_id"]);
    await contains(".o_usl_document_card .o_usl_tag_chip").click();
    expect(calls).toBe(3);
    expect(workspaces.at(-1).shortcut_tag_ids).toEqual([]);
    expect(workspaces.at(-1).search_domain).toEqual([
        ["tag_ids", "in", [1]],
    ]);
    await contains(".o_usl_filter_shortcuts button", {
        text: "Needs review",
    }).click();
    expect(calls).toBe(4);
    expect(JSON.stringify(workspaces.at(-1).search_domain)).toMatch(
        /tag_ids.*review_state/
    );
    expect(
        ".o_usl_filter_shortcuts [data-shortcut-key='needs_review']"
    ).toHaveClass("btn-primary");
    await contains(
        ".o_usl_filter_shortcuts [data-shortcut-key='needs_review']"
    ).click();
    expect(calls).toBe(5);
    expect(workspaces.at(-1).search_domain).toEqual([
        ["tag_ids", "in", [1]],
    ]);
});

test("Odoo-style list headers persist validated ordering in the URL", async () => {
    browser.sessionStorage.setItem(
        storageKey("global"),
        JSON.stringify({ workspace: "all", view: "list" })
    );
    const orders = [];
    const document = {
        id: 81,
        name: "Sortable evidence",
        date: "2026-07-30",
        company: "USL",
        review_state: "reviewed",
        availability_state: "available",
        access_error: false,
        correspondent: "Supplier",
        document_type: "Invoice",
        tags: [],
        link_count: 0,
        primary_link: false,
    };
    onRpc("usl.document", "workspace_data", ({ kwargs }) => {
        orders.push(kwargs.order_by);
        return {
            ...emptyWorkspace,
            selected_workspace: "all",
            documents: [document],
            count: 1,
        };
    });

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });
    await contains(".o_list_table th", { text: "Document" }).click();

    expect(orders.at(-1)).toEqual([{ name: "name", asc: true }]);
    expect(
        JSON.parse(
            new URL(browser.location.href).searchParams.get("orderBy")
        )
    ).toEqual([{ name: "name", asc: true }]);

    await contains(".o_list_table th", { text: "Document" }).click();
    expect(orders.at(-1)).toEqual([{ name: "name", asc: false }]);

    await contains("button[title='Card view']").click();
    expect(".o_usl_documents_grid").toHaveCount(1);
    expect(
        JSON.parse(
            new URL(browser.location.href).searchParams.get("usl_filters")
        ).view
    ).toBe("cards");
    expect("select[aria-label='Sort documents']").toHaveValue("custom");

    await contains("button[title='Compact list']").click();
    expect(".o_list_table").toHaveCount(1);
    expect(
        JSON.parse(
            new URL(browser.location.href).searchParams.get("usl_filters")
        ).view
    ).toBe("list");
});

test("Trash shows attribution and keeps linked documents recoverable", async () => {
    let availabilityState = "available";
    let moveCalled = false;
    const document = {
        id: 54,
        name: "Signed supplier agreement",
        paperless_id: 154,
        date: "2026-07-18",
        company: "USL",
        review_state: "reviewed",
        availability_state: availabilityState,
        access_error: false,
        correspondent: "Alpine Office Supplies",
        document_type: "Contract",
        tags: [],
        link_count: 2,
        primary_link: false,
    };
    onRpc("usl.document", "workspace_data", () => ({
        ...emptyWorkspace,
        documents: [{ ...document, availability_state: availabilityState }],
        count: 1,
    }));
    onRpc("usl.document", "document_detail", () => ({
        ...document,
        availability_state: availabilityState,
        can_edit: true,
        can_change_links: true,
        can_trash: availabilityState === "available",
        can_restore: availabilityState === "trashed",
        can_manage: true,
        archive_available: true,
        trashed_by:
            availabilityState === "trashed" ? "Administrator" : false,
        trashed_at:
            availabilityState === "trashed" ? "2026-07-30 12:30:00" : false,
        permanent_delete_blocker:
            availabilityState === "trashed"
                ? "Remove 2 active Odoo links before permanent deletion."
                : false,
        versions: [],
        links: [],
    }));
    onRpc("usl.document", "move_to_trash", () => {
        moveCalled = true;
        availabilityState = "trashed";
        return {
            document_id: document.id,
            message:
                "“Signed supplier agreement” was moved to Trash. Its Odoo links were kept.",
        };
    });

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });
    await contains(".o_usl_document_card").click();
    await animationFrame();
    await contains(".o_usl_action_menu summary").click();
    await contains("button", { text: "Move to Trash" }).click();

    expect(".modal").toHaveText(/2 Odoo links will remain visible/);
    expect(".modal").toHaveText(/cannot be permanently deleted while linked/);
    await contains(".modal-footer .btn-danger").click();
    await animationFrame();

    expect(moveCalled).toBe(true);
    expect(".o_notification").toHaveText(
        /Signed supplier agreement.*moved to Trash/
    );
    expect(".o_usl_documents_detail").toHaveText(/In Trash/);
    expect(".o_usl_documents_detail").toHaveText(/Administrator/);
    expect(".o_usl_documents_detail").toHaveText(/2026-07-30 12:30:00/);
    expect(".o_usl_documents_detail footer .btn-primary").toHaveText(
        /Restore document/
    );
    expect(
        ".o_usl_documents_detail footer .btn-outline-danger"
    ).not.toBeEnabled();
    expect(".o_usl_documents_detail").toHaveText(
        /Remove 2 active Odoo links before permanent deletion/
    );
});

test("authorized permanent deletion requires a reason and keeps an audit flow", async () => {
    const document = {
        id: 55,
        name: "Expired temporary scan",
        paperless_id: 155,
        date: "2020-01-01",
        company: "USL",
        review_state: "reviewed",
        availability_state: "trashed",
        access_error: false,
        correspondent: "",
        document_type: "Temporary",
        tags: [],
        link_count: 0,
        primary_link: false,
    };
    const calls = [];
    onRpc("usl.document", "workspace_data", () => ({
        ...emptyWorkspace,
        documents: [document],
        count: 1,
    }));
    onRpc("usl.document", "document_detail", () => ({
        ...document,
        can_edit: false,
        can_change_links: true,
        can_trash: false,
        can_restore: true,
        can_manage: true,
        archive_available: true,
        trashed_by: "Administrator",
        trashed_at: "2026-07-30 12:45:00",
        permanent_delete_blocker: false,
        retention_hold: false,
        retention_until: "2025-01-01",
        versions: [],
        links: [],
    }));
    onRpc("usl.document", "approve_permanent_deletion", ({ args }) => {
        calls.push(["approve", args]);
        return true;
    });
    onRpc("usl.document", "permanently_delete_from_trash", ({ args }) => {
        calls.push(["delete", args]);
        return true;
    });

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action({ initial_workspace: "trash" }) },
    });
    await contains(".o_usl_document_card").click();
    await animationFrame();
    await contains(
        ".o_usl_documents_detail footer .btn-outline-danger"
    ).click();

    expect(".modal-footer .btn-danger").not.toBeEnabled();
    await contains("#usl_delete_reason").fill(
        "Retention expired; duplicate temporary scan."
    );
    expect(".modal-footer .btn-danger").toBeEnabled();
    await contains(".modal-footer .btn-danger").click();
    await animationFrame();

    expect(calls).toEqual([
        [
            "approve",
            [[55], "Retention expired; duplicate temporary scan."],
        ],
        ["delete", [[55]]],
    ]);
    expect(".o_notification").toHaveText(
        /Expired temporary scan.*permanently deleted/
    );
    expect(".o_usl_documents_detail").toHaveCount(0);
});

test("workspace and open document detail do not overflow their viewport", async () => {
    const evidence = {
        id: 56,
        name: "Responsive evidence",
        paperless_id: 156,
        date: "2026-07-30",
        company: "USL",
        review_state: "reviewed",
        availability_state: "available",
        access_error: false,
        correspondent: "Example Bank",
        document_type: "Statement",
        tags: [],
        link_count: 0,
        primary_link: false,
    };
    onRpc("usl.document", "workspace_data", () => ({
        ...emptyWorkspace,
        documents: [evidence],
        count: 1,
    }));
    onRpc("usl.document", "document_detail", () => ({
        ...evidence,
        can_edit: true,
        can_change_links: true,
        can_trash: true,
        can_restore: false,
        can_manage: true,
        archive_available: true,
        versions: [],
        links: [],
    }));

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });
    const workspace = document.querySelector(".o_usl_documents");
    expect(workspace.scrollWidth <= workspace.clientWidth).toBe(true);
    await contains(".o_usl_document_card").click();
    await animationFrame();
    expect(workspace.scrollWidth <= workspace.clientWidth).toBe(true);
    expect(".o_usl_documents_detail footer .btn-primary").toHaveText(
        /Download original/
    );
});

test("permission failures are actionable while healthy state stays quiet", async () => {
    const document = {
        id: 11,
        name: "Restricted evidence",
        paperless_id: 44,
        date: "2026-07-29",
        company: "USL",
        review_state: "classified",
        availability_state: "permission_error",
        access_error: "Paperless rejected the permission update.",
        correspondent: "",
        document_type: "",
        tags: [],
        link_count: 0,
        primary_link: false,
    };
    onRpc("usl.document", "workspace_data", () => ({
        ...emptyWorkspace,
        documents: [document],
        count: 1,
    }));
    onRpc("usl.document", "document_detail", () => ({
        ...document,
        can_edit: false,
        can_change_links: false,
        can_manage: false,
        versions: [],
        links: [],
    }));

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });
    await contains(".o_usl_document_card").click();
    await animationFrame();

    expect(".o_usl_documents_detail .alert-danger").toHaveText(
        /access needs attention/i
    );
    expect(".o_usl_document_preview").toHaveCount(0);
    expect("[data-testid='document-open-preview']").toHaveCount(0);
    expect("[data-testid='document-open-paperless']").toHaveCount(0);
    expect(".o_usl_documents_detail").not.toHaveText(/Download original/i);
    expect(".o_usl_document_card img").toHaveCount(0);
    expect(".o_usl_document_thumb_placeholder").toHaveCount(1);
});

test("archive search starts empty and exposes human search controls", async () => {
    const calls = [];
    onRpc("usl.document", "workspace_data", ({ kwargs }) => {
        calls.push(kwargs);
        return {
            ...emptyWorkspace,
            selected_workspace: "archive_search",
            smart_views: [
                {
                    id: 1,
                    key: "home",
                    name: "Home",
                    icon: "fa-home",
                    personal: false,
                    filters: {},
                },
                {
                    id: 2,
                    key: "archive_search",
                    name: "Archive search",
                    icon: "fa-search",
                    personal: false,
                    filters: {},
                },
            ],
        };
    });

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action({ initial_workspace: "archive_search" }) },
    });

    expect(calls[0].workspace).toBe("archive_search");
    expect(calls[0].search_mode).toBe("hybrid");
    expect(calls[0].background_mode).toBe("include");
    expect(".o_usl_documents_empty").toHaveText(/Search the archive/);
    expect(".o_usl_documents_empty").toHaveText(/company, date, type/);
    expect("[aria-label='Archive search matching']").toHaveValue("hybrid");
    expect("[aria-label='Background document visibility']").toHaveValue(
        "include"
    );

    await contains("[aria-label='Archive search matching']").select("exact");
    expect(calls.at(-1).search_mode).toBe("exact");
    await contains("[aria-label='Background document visibility']").select(
        "exclude"
    );
    expect(calls.at(-1).background_mode).toBe("exclude");
});

test("background relationship is promoted without an upload journey", async () => {
    let role = "background";
    const calls = [];
    const document = {
        id: 91,
        name: "Project brief",
        paperless_id: 191,
        date: "2026-08-25",
        company: "USL",
        review_state: "classified",
        availability_state: "available",
        access_error: false,
        correspondent: "",
        document_type: "Brief",
        tags: [],
        link_count: 1,
        intake_role: "background",
        is_starred: false,
        primary_link: { name: "Search UX project", model: "project.project" },
    };
    const detail = () => ({
        ...document,
        can_edit: true,
        can_change_links: true,
        can_manage: false,
        can_trash: true,
        archive_available: true,
        versions: [],
        links: [
            {
                id: 8,
                model: "project.project",
                res_id: 12,
                record_name: "Search UX project",
                model_label: "Project",
                document_role: role,
            },
        ],
    });
    onRpc("usl.document", "workspace_data", () => ({
        ...emptyWorkspace,
        selected_workspace: "archive_search",
        documents: [document],
        count: 1,
    }));
    onRpc("usl.document", "document_detail", () => detail());
    onRpc("usl.document", "action_set_library_visibility", ({ args, kwargs }) => {
        expect(args).toEqual([[91], true]);
        expect(kwargs.res_model).toBe("project.project");
        expect(kwargs.res_id).toBe(12);
        calls.push(true);
        role = args[1] ? "library" : "background";
        return detail();
    });

    await mountWithCleanup(DocumentsWorkspace, {
        props: {
            action: action({
                initial_workspace: "archive_search",
                res_model: "project.project",
                res_id: 12,
            }),
        },
    });
    await contains(".o_usl_document_card").click();
    await animationFrame();
    expect(".o_usl_detail_header_actions").toHaveText(/Add to My library/);
    await contains("button", { text: "Add to My library" }).click();
    await animationFrame();

    expect(calls).toEqual([true]);
    expect(".o_notification").toHaveText(/archived file was reused/);
    expect(".o_usl_detail_header_actions").toHaveText(/Remove from My library/);
});

test("stars are personal workspace controls", async () => {
    let starred = false;
    const document = {
        id: 92,
        name: "Reference note",
        paperless_id: 192,
        date: "2026-08-25",
        company: "USL",
        review_state: "classified",
        availability_state: "available",
        access_error: false,
        correspondent: "",
        document_type: "Note",
        tags: [],
        link_count: 0,
        is_starred: starred,
        primary_link: false,
    };
    onRpc("usl.document", "workspace_data", () => ({
        ...emptyWorkspace,
        documents: [{ ...document, is_starred: starred }],
        count: 1,
    }));
    onRpc("usl.document", "action_set_starred", ({ args }) => {
        expect(args).toEqual([[92], true]);
        starred = true;
        return { document_id: 92, is_starred: true };
    });

    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });
    await contains(".o_usl_star_button").click();
    await animationFrame();

    expect(".o_notification").toHaveText(/Added to your starred documents/);
    expect(".o_usl_star_button").toHaveClass("is-starred");
});
