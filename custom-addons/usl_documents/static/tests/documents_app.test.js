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
    companies: [],
    document_types: [],
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

    expect(".o_usl_documents_empty").toHaveText(/No accessible documents/);
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

    expect(".alert-warning").toHaveText(/Archive degraded/);
    expect(".o_usl_documents_empty").toHaveText(/Archive temporarily unavailable/);
    expect(".o_usl_documents_empty button.btn-primary").toHaveText(/Retry/);
});

test("workspace search state survives record navigation", async () => {
    const key = "usl_documents.workspace.account.move.12";
    browser.sessionStorage.setItem(
        key,
        JSON.stringify({
            query: "embedded cobalt phrase",
            companyId: "4",
            documentType: "Invoice",
            workspace: "all",
            view: "list",
            sort: "title",
            page: 2,
        })
    );
    onRpc("usl.document", "workspace_data", ({ kwargs }) => {
        expect(kwargs.query).toBe("embedded cobalt phrase");
        expect(kwargs.company_id).toBe("4");
        expect(kwargs.document_type).toBe("Invoice");
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

test("detail exposes permission, versions, downloads, and Odoo relationships", async () => {
    const document = {
        id: 7,
        name: "Supplier invoice",
        paperless_id: 42,
        date: "2026-07-29",
        company: "USL",
        confidentiality: "accounting",
        review_state: "classified",
        availability_state: "available",
        permission_sync_state: "synchronized",
        correspondent: "Supplier",
        document_type: "Invoice",
        filename: "invoice.pdf",
        source: "odoo_upload",
        checksum: "a".repeat(64),
        link_count: 1,
        version_count: 1,
        paperless_url: "https://documents.example.test/documents/42/details",
    };
    onRpc("usl.document", "workspace_data", () => ({
        ...emptyWorkspace,
        documents: [document],
        count: 1,
    }));
    onRpc("usl.document", "document_detail", () => ({
        ...document,
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

    expect(".o_usl_documents_detail").toHaveText(/Received original/);
    expect(".o_usl_documents_detail").toHaveText(/BILL\/2026\/0042/);
    expect(".o_usl_documents_detail").toHaveText(/synchronized/);
    expect("a[href*='original=0']").toHaveCount(2);
    expect("button.btn-outline-danger").toHaveText(/Remove this Odoo relationship/);
    expect("a[target='_blank']").toHaveText(/Open in Paperless/);
});
