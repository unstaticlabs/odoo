import { expect, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";

import {
    contains,
    mountWithCleanup,
    onRpc,
} from "@web/../tests/web_test_helpers";

import { DocumentsWorkspace } from "../src/documents_app";
import { browser } from "@web/core/browser/browser";

defineMailModels();

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
    operations: [],
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
    const key = "usl_documents.workspace.account.move.12";
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
            browser.sessionStorage.getItem("usl_documents.workspace.global")
        ).companyId
    ).toBe("4");
    browser.sessionStorage.removeItem(key);
    browser.sessionStorage.removeItem("usl_documents.workspace.global");
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
    await contains(".o_usl_detail_section summary").click();
    expect(".o_usl_documents_detail").toHaveText(/Received original/);
    expect(".o_usl_documents_detail").toHaveText(/BILL\/2026\/0042/);
    expect(".o_usl_documents_detail").not.toHaveText(/synchronized/i);
    expect(".o_usl_documents_detail").toHaveText(/Accounting/);
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
