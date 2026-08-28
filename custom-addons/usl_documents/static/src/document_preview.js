/** @odoo-module **/

import {
    Component,
    onMounted,
    onPatched,
    onWillDestroy,
    onWillUpdateProps,
    useRef,
    useState,
} from "@odoo/owl";
import {browser} from "@web/core/browser/browser";
import {loadPDFJSAssets} from "@web/core/utils/pdfjs";

/**
 * Access-controlled inline preview shared by Documents and record cards.
 *
 * Keep this component independent from the full Documents workspace so small
 * consumers do not have to load the workspace action and its services.
 */
export class DocumentPreview extends Component {
    static template = "usl_documents.DocumentPreview";
    static props = {
        url: String,
        versionId: {type: String, optional: true},
    };

    setup() {
        this.canvas = useRef("canvas");
        this.loadToken = 0;
        this.imageUrl = null;
        this.pdf = null;
        this.pdfRenderTask = null;
        this.renderedPdfKey = null;
        this.previewWidth = 0;
        this.resizeObserver = null;
        this.state = useState({
            loading: true,
            kind: "",
            text: "",
            error: "",
            page: 1,
            pageCount: 0,
            rendering: false,
        });
        onMounted(() => this.load(this.props));
        // The PDF canvas is conditional. The PDF can finish loading before Owl
        // has patched that canvas into the DOM, so render only after the patch
        // that makes the ref available. This also keeps page changes reliable.
        onPatched(() => this.renderPdfPage());
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
        this.pdfRenderTask?.cancel();
        this.pdfRenderTask = null;
        this.renderedPdfKey = null;
        this.previewWidth = 0;
        this.resizeObserver?.disconnect();
        this.resizeObserver = null;
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
            rendering: false,
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
        if (!canvas || !this.pdf || this.state.kind !== "pdf") {
            return;
        }
        const token = this.loadToken;
        const availableWidth = Math.max(
            240,
            Math.floor(canvas.parentElement?.clientWidth || 680)
        );
        const renderKey = `${token}:${this.state.page}:${availableWidth}`;
        if (this.renderedPdfKey === renderKey) {
            return;
        }
        this.renderedPdfKey = renderKey;
        this.state.rendering = true;
        let renderTask = null;
        try {
            const page = await this.pdf.getPage(this.state.page);
            if (token !== this.loadToken) {
                return;
            }
            const baseViewport = page.getViewport({scale: 1});
            const scale = Math.min(2, availableWidth / baseViewport.width);
            const viewport = page.getViewport({scale});
            canvas.width = Math.ceil(viewport.width);
            canvas.height = Math.ceil(viewport.height);
            renderTask = page.render({
                canvasContext: canvas.getContext("2d"),
                viewport,
            });
            this.pdfRenderTask = renderTask;
            await renderTask.promise;
            if (token !== this.loadToken) {
                return;
            }
            this.observePreviewWidth(canvas);
        } catch (error) {
            if (error?.name !== "RenderingCancelledException" && token === this.loadToken) {
                this.state.kind = "";
                this.state.error =
                    error?.message || "The PDF preview could not be displayed.";
            }
        } finally {
            if (renderTask && this.pdfRenderTask === renderTask) {
                this.pdfRenderTask = null;
            }
            if (token === this.loadToken) {
                this.state.rendering = false;
            }
        }
    }

    observePreviewWidth(canvas) {
        const container = canvas.parentElement;
        if (!container || !globalThis.ResizeObserver || this.resizeObserver) {
            return;
        }
        this.previewWidth = Math.floor(container.clientWidth);
        this.resizeObserver = new ResizeObserver((entries) => {
            const width = Math.floor(entries[0]?.contentRect?.width || 0);
            if (!width || width === this.previewWidth) {
                return;
            }
            this.previewWidth = width;
            this.renderedPdfKey = null;
            browser.requestAnimationFrame(() => this.renderPdfPage());
        });
        this.resizeObserver.observe(container);
    }

    previousPage() {
        if (this.state.page > 1) {
            this.pdfRenderTask?.cancel();
            this.renderedPdfKey = null;
            this.state.page -= 1;
        }
    }

    nextPage() {
        if (this.state.page < this.state.pageCount) {
            this.pdfRenderTask?.cancel();
            this.renderedPdfKey = null;
            this.state.page += 1;
        }
    }
}
