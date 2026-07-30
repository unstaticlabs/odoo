import { beforeEach, expect, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";

import {
    contains,
    mountWithCleanup,
    onRpc,
} from "@web/../tests/web_test_helpers";

import { DocumentsWorkspace } from "../src/documents_app";
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
    browser.history.replaceState({}, "", url.toString());
});

const emptyWorkspace = {
    documents: [],
    count: 0,
    page: 1,
    page_size: 24,
    degraded: false,
    truncated: false,
    selected_workspace: "recent",
    companies: [],
    tags: [],
    correspondents: [],
    document_types: [],
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

function action(params = {}) {
    return {
        name: "Documents",
        tag: "usl_documents.workspace",
        type: "ir.actions.client",
        params,
    };
}

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
        expect(kwargs.query).toBe("embedded cobalt phrase");
        expect(kwargs.company_id).toBe("4");
        expect(kwargs.document_type_id).toBe("8");
        expect(kwargs.page).toBe(2);
        return emptyWorkspace;
    });

    await mountWithCleanup(DocumentsWorkspace, {
        props: {
            action: action({ res_model: "account.move", res_id: 12 }),
        },
    });

    expect("input[type=search]").toHaveValue("embedded cobalt phrase");
    expect(
        JSON.parse(
            browser.sessionStorage.getItem(storageKey("global"))
        ).companyId
    ).toBe("4");
    browser.sessionStorage.removeItem(key);
    browser.sessionStorage.removeItem(storageKey("global"));
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
        expect(kwargs.workspace).toBe("all");
        expect(kwargs.query).toBe("");
        expect(kwargs.tag_ids).toEqual([]);
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

    expect("input[type=search]").toHaveValue("");
});

test("detail prioritizes original, classification, versions, and linked records", async () => {
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
    onRpc("usl.document", "document_detail", () => ({
        ...document,
        checksum: "a".repeat(64),
        can_edit: true,
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
                original_url: "/usl_documents/7/download?original=1&version=9",
                archive_url: "/usl_documents/7/download?original=0&version=9",
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
    }));

    await mountWithCleanup(DocumentsWorkspace, {
        props: {
            action: action({ res_model: "account.move", res_id: 12 }),
        },
    });
    await contains(".o_usl_document_card").click();
    await animationFrame();

    await contains("button", { text: "Edit" }).click();
    expect(".o_usl_metadata_document_type").toHaveValue("8");
    expect(".o_usl_metadata_correspondent").toHaveValue("4");
    await contains("button", { text: "Cancel" }).click();
    expect(".o_usl_detail_section h6").toHaveText(/File versions/);
    expect(".o_usl_detail_section .text-bg-primary").toHaveText(/Current/);
    expect(".o_usl_documents_detail").toHaveText(/Received original/);
    expect(".o_usl_documents_detail").toHaveText(/BILL\/2026\/0042/);
    expect(".o_usl_documents_detail").not.toHaveText(/synchronized/i);
    expect(".o_usl_documents_detail").toHaveText(/Accounting/);
    expect(".o_usl_documents_detail iframe").toHaveAttribute("data-version", "9");
    expect("a[href*='original=0']").toHaveCount(2);
    expect(".o_usl_documents_detail footer a.btn-primary").toHaveText(
        /Download original/
    );
    await contains(".o_usl_action_menu summary").click();
    expect(".o_usl_documents_detail footer").toHaveText(/Upload new version/);
    expect(".o_usl_documents_detail footer").toHaveText(
        /Remove link from this record/
    );
    expect("a[target='_blank']").toHaveText(/Open in Paperless/);
    expect(
        new URL(browser.location.href).searchParams.get("usl_document")
    ).toBe("7");
    await contains(".o_usl_documents_detail .btn-close").click();
    await animationFrame();
    expect(".o_usl_documents_detail").toHaveCount(0);
    browser.history.forward();
    await animationFrame();
    expect(".o_usl_documents_detail").toHaveCount(1);
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

    expect(".o_usl_documents_detail").toHaveText(/Reload-safe contract/);
    expect(
        new URL(browser.location.href).searchParams.get("usl_document")
    ).toBe("77");
    expect(
        new URL(browser.location.href).searchParams.get("usl_version")
    ).toBe("12");
});

test("search suggestions create removable native-style facets", async () => {
    const tags = [
        { id: 21, name: "Tax & reporting", color: "#31a354" },
        { id: 22, name: "Contracts & legal", color: "#8c6bb1" },
    ];
    let lastTagIds = [];
    onRpc("usl.document", "workspace_data", ({ kwargs }) => {
        lastTagIds = kwargs.tag_ids;
        return { ...emptyWorkspace, tags };
    });
    await mountWithCleanup(DocumentsWorkspace, {
        props: { action: action() },
    });

    await contains(".o_usl_documents_search input").click();
    await contains(".o_usl_documents_search input").fill("tag:tax", {
        confirm: false,
    });
    expect(".o_usl_search_suggestions").toHaveText(/Tax & reporting/);
    await contains(".o_usl_search_suggestions button").click();
    expect(lastTagIds).toEqual([21]);
    expect(".o_usl_active_facets").toHaveText(/Tag: Tax & reporting/);
    await contains(".o_usl_active_facets button").click();
    expect(lastTagIds).toEqual([]);
    expect(".o_usl_active_facets").toHaveCount(0);
});

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
    await contains("button", { text: "Add tag" }).click();
    await contains(".o_usl_tag_picker input").fill("Board approved");
    await contains(".o_usl_tag_picker button.text-primary").click();
    expect(".o_usl_detail_tags").toHaveText(/Board approved/);
    await contains("button[aria-label='Remove tag Accounting']").click();
    expect("button[aria-label='Remove tag Accounting']").toHaveCount(0);
});

test("tag chips filter results and advanced filters stay tucked away", async () => {
    const tags = [
        { id: 1, name: "Banking", color: "#225588", text_color: "#ffffff" },
        { id: 2, name: "Tax", color: "#228855", text_color: "#ffffff" },
        { id: 3, name: "Reviewed", color: "#885522", text_color: "#ffffff" },
        { id: 4, name: "July", color: "#662288", text_color: "#ffffff" },
    ];
    let calls = 0;
    onRpc("usl.document", "workspace_data", ({ kwargs }) => {
        calls++;
        if (calls > 1) {
            expect(kwargs.tag_ids).toEqual([1]);
        }
        return {
            ...emptyWorkspace,
            tags,
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
    await contains("button", { text: "More filters" }).click();
    expect(".o_usl_more_filters").toHaveCount(1);
    await contains(".o_usl_document_card .o_usl_tag_chip").click();
    expect(calls).toBe(2);
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
    expect(".o_usl_documents_detail iframe").toHaveCount(0);
});
