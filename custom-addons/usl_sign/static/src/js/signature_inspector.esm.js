/** @odoo-module **/

import {Component, onWillUnmount, useRef, useState} from "@odoo/owl";
import {_t} from "@web/core/l10n/translation";
import {loadPDFJSAssets} from "@web/core/utils/pdfjs";
import {registry} from "@web/core/registry";
import {useService} from "@web/core/utils/hooks";

import {
    formatBytes,
    MAX_DOSSIER_ATTACHMENTS,
    MAX_INSPECTOR_FILE_BYTES,
    overallStatus,
    safeAttachmentName,
} from "./signature_inspector_utils.esm.js";

const INSPECTOR_WORKER_URL = "/usl_sign/static/lib/signature_inspector_worker.js";

export function createInspectorWorker() {
    return new Worker(INSPECTOR_WORKER_URL);
}

export class SignatureInspector extends Component {
    static template = "usl_sign.SignatureInspector";
    static props = ["*"];

    setup() {
        this.notification = useService("notification");
        this.fileInput = useRef("fileInput");
        this.worker = null;
        this.state = useState({
            stage: "empty",
            dragging: false,
            error: "",
            fileName: "",
            fileSize: 0,
            result: null,
        });
        onWillUnmount(() => this.worker?.terminate());
    }

    get verdict() {
        const signatureVerdict = overallStatus(
            this.allPdfResults.flatMap((document) => document.signatures),
            _t
        );
        const dossierVerdict = this.dossierVerdict;
        if (!dossierVerdict) {
            return signatureVerdict;
        }
        if (dossierVerdict.tone === "danger" || signatureVerdict.tone === "danger") {
            return dossierVerdict.tone === "danger" ? dossierVerdict : signatureVerdict;
        }
        if (dossierVerdict.tone === "warning" || signatureVerdict.tone === "warning") {
            return dossierVerdict.tone === "warning" ? dossierVerdict : signatureVerdict;
        }
        return signatureVerdict.tone === "neutral" ? dossierVerdict : signatureVerdict;
    }

    get allPdfResults() {
        if (!this.state.result) {
            return [];
        }
        return [
            this.state.result.document,
            ...this.state.result.dossier.attachments
                .filter((attachment) => attachment.pdf)
                .map((attachment) => attachment.pdf),
        ];
    }

    get totalSignatures() {
        return this.allPdfResults.reduce((total, document) => total + document.signatures.length, 0);
    }

    get dossierVerdict() {
        const dossier = this.state.result?.dossier;
        if (!dossier?.detected) {
            return null;
        }
        const manifest = dossier.signedManifest;
        if (!manifest) {
            return {
                tone: "warning",
                title: _t("Attachments found; no USL signed manifest found"),
                detail: _t(
                    "The embedded files are listed and hashed, but this browser cannot tie them to a signed USL evidence manifest."
                ),
            };
        }
        if (
            !manifest.hashValid ||
            !manifest.signatureValid ||
            dossier.artifactChecks.mismatches.length ||
            dossier.attachments.some((attachment) =>
                attachment.pdf?.signatures.some(
                    (signature) => !signature.byteRangeValid || signature.cryptoValid === false
                )
            )
        ) {
            return {
                tone: "danger",
                title: _t("The evidence dossier has integrity problems"),
                detail: _t(
                    "The signed manifest, an embedded artifact, or an embedded PDF signature does not match."
                ),
            };
        }
        return {
            tone: "success",
            title: _t("The evidence dossier is internally consistent"),
            detail: _t(
                "%s manifest artifact(s) match their embedded files. External certificate trust is not established offline.",
                dossier.artifactChecks.matched
            ),
        };
    }

    formatBytes(value) {
        return formatBytes(value);
    }

    formatDate(value) {
        if (!value) {
            return _t("Not provided");
        }
        const date = new Date(value);
        return Number.isNaN(date.valueOf())
            ? value
            : new Intl.DateTimeFormat(undefined, {dateStyle: "medium", timeStyle: "short"}).format(
                  date
              );
    }

    shortHash(value) {
        return value ? `${value.slice(0, 12)}…${value.slice(-8)}` : _t("Unavailable");
    }

    signatureTitle(signature) {
        return (
            signature.certificate?.subject.commonName ||
            signature.certificate?.subject.label ||
            signature.claimedName ||
            _t("Unnamed signer")
        );
    }

    signatureKindLabel(signature) {
        const commonName = signature.certificate?.subject.commonName || "";
        if (commonName.startsWith("USL Sign Personal:")) {
            return _t("Personal signer PAdES signature");
        }
        if (commonName.startsWith("USL Sign Platform Seal")) {
            return _t("Platform integrity seal");
        }
        return signature.signatureKind || _t("Document signature");
    }

    signatureTone(signature) {
        if (signature.cryptoValid === false || !signature.byteRangeValid) {
            return "danger";
        }
        if (signature.cryptoValid !== true || signature.weakAlgorithm) {
            return "warning";
        }
        return "success";
    }

    signatureLabel(signature) {
        if (signature.cryptoValid === false || !signature.byteRangeValid) {
            return _t("Invalid");
        }
        if (signature.cryptoValid !== true) {
            return _t("Not fully checked");
        }
        if (signature.weakAlgorithm) {
            return _t("Intact · legacy SHA-1");
        }
        return signature.coversCurrentFile ? _t("Intact") : _t("Intact revision");
    }

    certificatePathLinked(signature) {
        return Boolean(
            signature.certificatePath?.internallyConsistent &&
                signature.certificatePath.verifiedIssuerLinks
        );
    }

    byteRangeMessage(signature) {
        if (
            signature.byteRangeMessage ===
            "The PDF signature byte range is malformed or does not surround its signature container."
        ) {
            return _t(
                "The PDF signature byte range is malformed or does not surround its signature container."
            );
        }
        if (signature.byteRangeMessage === "This signature covers the current end of the PDF.") {
            return _t("This signature covers the current end of the PDF.");
        }
        const laterBytes = /^(\d+) byte\(s\) were appended after this signed revision\.$/.exec(
            signature.byteRangeMessage || ""
        );
        return laterBytes
            ? _t("%s byte(s) were appended after this signed revision.", laterBytes[1])
            : signature.byteRangeMessage;
    }

    chooseFile() {
        this.fileInput.el?.click();
    }

    async onFileChange(event) {
        const [file] = event.currentTarget.files || [];
        event.currentTarget.value = "";
        if (file) {
            await this.inspectFile(file);
        }
    }

    onDragOver(event) {
        event.preventDefault();
        if (event.dataTransfer) {
            event.dataTransfer.dropEffect = "copy";
        }
        this.state.dragging = true;
    }

    onDragLeave(event) {
        if (!event.currentTarget.contains(event.relatedTarget)) {
            this.state.dragging = false;
        }
    }

    async onDrop(event) {
        event.preventDefault();
        this.state.dragging = false;
        const files = [...(event.dataTransfer?.files || [])];
        if (files.length !== 1) {
            this.showError(_t("Choose one signed PDF or one PDF evidence dossier."));
            return;
        }
        await this.inspectFile(files[0]);
    }

    reset() {
        this.worker?.terminate();
        this.worker = null;
        Object.assign(this.state, {
            stage: "empty",
            dragging: false,
            error: "",
            fileName: "",
            fileSize: 0,
            result: null,
        });
    }

    showError(message) {
        Object.assign(this.state, {stage: "error", error: message, result: null});
    }

    async extractAttachments(bytes) {
        await loadPDFJSAssets();
        const originalWorkerSrc = globalThis.pdfjsLib.GlobalWorkerOptions.workerSrc;
        globalThis.pdfjsLib.GlobalWorkerOptions.workerSrc =
            "/web/static/lib/pdfjs/build/pdf.worker.js";
        let document;
        try {
            document = await globalThis.pdfjsLib.getDocument({
                data: bytes.slice(),
                isEvalSupported: false,
                useWorkerFetch: false,
            }).promise;
            const attachmentMap = (await document.getAttachments()) || {};
            const rows = Object.values(attachmentMap);
            if (rows.length > MAX_DOSSIER_ATTACHMENTS) {
                throw new Error(
                    _t("This dossier has too many attachments to inspect safely (maximum: 200).")
                );
            }
            const totalBytes = rows.reduce((total, row) => total + (row.content?.byteLength || 0), 0);
            if (totalBytes > MAX_INSPECTOR_FILE_BYTES) {
                throw new Error(
                    _t("The dossier's embedded files are too large to inspect safely (maximum: 100 MB).")
                );
            }
            return rows.map((row) => ({
                name: safeAttachmentName(row.filename || row.rawFilename),
                description: row.description || "",
                // Own each transfer buffer: PDF.js may return views over shared buffers.
                content: new Uint8Array(row.content).slice(),
            }));
        } finally {
            await document?.destroy();
            globalThis.pdfjsLib.GlobalWorkerOptions.workerSrc = originalWorkerSrc;
        }
    }

    inspectInWorker(payload) {
        this.worker?.terminate();
        this.worker = createInspectorWorker();
        return new Promise((resolve, reject) => {
            this.worker.onmessage = ({data}) => {
                this.worker?.terminate();
                this.worker = null;
                data.ok ? resolve(data.result) : reject(new Error(data.error));
            };
            this.worker.onerror = () => {
                this.worker?.terminate();
                this.worker = null;
                reject(new Error(_t("The local signature checker stopped unexpectedly.")));
            };
            const transfers = [payload.document.buffer];
            transfers.push(...payload.attachments.map((attachment) => attachment.content.buffer));
            this.worker.postMessage(payload, transfers);
        });
    }

    async inspectFile(file) {
        if (file.size > MAX_INSPECTOR_FILE_BYTES) {
            this.showError(_t("This PDF is too large to inspect safely (maximum: 100 MB)."));
            return;
        }
        if (!file.name.toLowerCase().endsWith(".pdf") && file.type !== "application/pdf") {
            this.showError(_t("Choose a PDF file. Other formats are not opened."));
            return;
        }
        Object.assign(this.state, {
            stage: "loading",
            error: "",
            fileName: safeAttachmentName(file.name),
            fileSize: file.size,
            result: null,
        });
        try {
            const bytes = new Uint8Array(await file.arrayBuffer());
            const attachments = await this.extractAttachments(bytes);
            this.state.result = await this.inspectInWorker({
                name: file.name,
                document: bytes,
                attachments,
            });
            this.state.stage = "ready";
        } catch (error) {
            this.showError(error.message || _t("This PDF could not be inspected."));
        }
    }

    async copyHash(value) {
        try {
            await navigator.clipboard.writeText(value);
            this.notification.add(_t("SHA-256 copied."), {type: "success"});
        } catch {
            this.notification.add(_t("The hash could not be copied. Select it in Details instead."), {
                type: "warning",
            });
        }
    }
}

registry.category("actions").add("usl_sign.signature_inspector", SignatureInspector);
