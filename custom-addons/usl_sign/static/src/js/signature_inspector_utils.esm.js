/** @odoo-module **/

const PDF_HEADER = "%PDF-";
const PDF_SIGNATURE_PATTERN = /\/ByteRange\s*\[\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*\]/g;

export const MAX_INSPECTOR_FILE_BYTES = 100 * 1024 * 1024;
export const MAX_DOSSIER_ATTACHMENTS = 200;

export function signatureVerificationMode(subFilter) {
    switch (String(subFilter || "").toLowerCase()) {
        case "adbe.pkcs7.detached":
        case "etsi.cades.detached":
            return "detached";
        case "etsi.rfc3161":
            return "timestamp";
        case "adbe.pkcs7.sha1":
            return "encapsulated-sha1";
        default:
            return "unsupported";
    }
}

export function bytesEqual(left, right) {
    const leftBytes = left instanceof Uint8Array ? left : new Uint8Array(left);
    const rightBytes = right instanceof Uint8Array ? right : new Uint8Array(right);
    return (
        leftBytes.length === rightBytes.length &&
        leftBytes.every((value, index) => value === rightBytes[index])
    );
}

export async function encapsulatedSha1Matches(
    signedBytes,
    encapsulatedDigest,
    subtle = crypto.subtle
) {
    return bytesEqual(await subtle.digest("SHA-1", signedBytes), encapsulatedDigest);
}

function pdfText(bytes) {
    return new TextDecoder("latin1").decode(bytes);
}

function decodePdfLiteral(value) {
    return value
        .replace(/\\([nrtbf()\\])/g, (_match, escaped) => {
            return {n: "\n", r: "\r", t: "\t", b: "\b", f: "\f"}[escaped] || escaped;
        })
        .replace(/\\\r?\n/g, "")
        .replace(/\\([0-7]{1,3})/g, (_match, octal) =>
            String.fromCharCode(Number.parseInt(octal, 8))
        );
}

function dictionaryString(dictionary, key) {
    const match = dictionary.match(new RegExp(`/${key}\\s*\\(([^)]*(?:\\\\.[^)]*)*)\\)`));
    return match ? decodePdfLiteral(match[1]) : "";
}

function dictionaryName(dictionary, key) {
    const match = dictionary.match(new RegExp(`/${key}\\s*/([^\\s<>\\[\\](){}/]+)`));
    return match ? match[1] : "";
}

function trimDerPadding(bytes) {
    if (bytes.length < 2 || bytes[0] !== 0x30) {
        return bytes;
    }
    const firstLength = bytes[1];
    let headerLength = 2;
    let contentLength = firstLength;
    if (firstLength & 0x80) {
        const lengthBytes = firstLength & 0x7f;
        if (!lengthBytes || lengthBytes > 4 || bytes.length < 2 + lengthBytes) {
            return bytes;
        }
        headerLength += lengthBytes;
        contentLength = 0;
        for (let index = 0; index < lengthBytes; index++) {
            contentLength = contentLength * 256 + bytes[2 + index];
        }
    }
    const derLength = headerLength + contentLength;
    return derLength <= bytes.length ? bytes.slice(0, derLength) : bytes;
}

function hexContents(dictionary, dictionaryStart) {
    const match = /\/Contents\s*<([\da-fA-F\s]+)>/.exec(dictionary);
    if (!match) {
        return null;
    }
    const compact = match[1].replace(/\s/g, "");
    if (!compact.length || compact.length % 2) {
        return {error: "The signature container has malformed hexadecimal content."};
    }
    const bytes = new Uint8Array(compact.length / 2);
    for (let index = 0; index < compact.length; index += 2) {
        bytes[index / 2] = Number.parseInt(compact.slice(index, index + 2), 16);
    }
    const tokenStart = dictionaryStart + match.index + match[0].indexOf("<");
    return {
        bytes: trimDerPadding(bytes),
        tokenStart,
        tokenEnd: tokenStart + match[0].slice(match[0].indexOf("<")).length,
    };
}

function signatureDictionaryBounds(text, byteRangeIndex) {
    const objectStart = text.lastIndexOf(" obj", byteRangeIndex);
    const dictionaryStart = text.lastIndexOf("<<", byteRangeIndex);
    const objectEnd = text.indexOf("endobj", byteRangeIndex);
    if (dictionaryStart < 0 || objectEnd < 0 || (objectStart >= 0 && dictionaryStart < objectStart)) {
        return null;
    }
    return {start: dictionaryStart, end: objectEnd};
}

function pdfStreamRanges(text) {
    const ranges = [];
    const pattern = /(?:^|\r\n|\r|\n)stream(?:\r\n|\r|\n)/g;
    let match;
    while ((match = pattern.exec(text))) {
        const streamKeyword = match.index + match[0].indexOf("stream");
        const contentStart = streamKeyword + match[0].slice(match[0].indexOf("stream")).length;
        const dictionaryStart = text.lastIndexOf("<<", streamKeyword);
        const dictionaryEnd = text.lastIndexOf(">>", streamKeyword);
        if (dictionaryStart < 0 || dictionaryEnd < dictionaryStart) {
            continue;
        }
        const dictionary = text.slice(dictionaryStart, dictionaryEnd + 2);
        const length = /\/Length\s+(\d+)\b/.exec(dictionary);
        const fallbackEnd = text.indexOf("endstream", contentStart);
        const contentEnd = length
            ? Math.min(contentStart + Number(length[1]), text.length)
            : fallbackEnd >= 0
              ? fallbackEnd
              : text.length;
        ranges.push([contentStart, contentEnd]);
    }
    return ranges;
}

function byteRangeStatus(values, fileLength, contents) {
    const [firstStart, firstLength, secondStart, secondLength] = values;
    const revisionEnd = secondStart + secondLength;
    if (
        values.some((value) => !Number.isSafeInteger(value) || value < 0) ||
        firstStart !== 0 ||
        firstLength > secondStart ||
        revisionEnd > fileLength ||
        !contents ||
        contents.error ||
        contents.tokenStart !== firstLength ||
        contents.tokenEnd !== secondStart
    ) {
        return {
            valid: false,
            revisionEnd,
            message: "The PDF signature byte range is malformed or does not surround its signature container.",
        };
    }
    return {
        valid: true,
        revisionEnd,
        message:
            revisionEnd === fileLength
                ? "This signature covers the current end of the PDF."
                : `${fileLength - revisionEnd} byte(s) were appended after this signed revision.`,
    };
}

/**
 * Locate detached PDF signature containers without evaluating document scripts or rendering pages.
 * The strict byte-range checks are repeated by the cryptographic worker before verification.
 */
export function parsePdfSignatures(input) {
    const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
    const text = pdfText(bytes);
    const signatures = [];
    const streamRanges = pdfStreamRanges(text);
    let match;
    PDF_SIGNATURE_PATTERN.lastIndex = 0;
    while ((match = PDF_SIGNATURE_PATTERN.exec(text))) {
        if (streamRanges.some(([start, end]) => match.index >= start && match.index < end)) {
            continue;
        }
        const bounds = signatureDictionaryBounds(text, match.index);
        if (!bounds) {
            signatures.push({
                index: signatures.length + 1,
                byteRange: match.slice(1).map(Number),
                byteRangeValid: false,
                error: "The signature dictionary could not be bounded safely.",
            });
            continue;
        }
        const dictionary = text.slice(bounds.start, bounds.end);
        const contents = hexContents(dictionary, bounds.start);
        const byteRange = match.slice(1).map(Number);
        const range = byteRangeStatus(byteRange, bytes.length, contents);
        signatures.push({
            index: signatures.length + 1,
            byteRange,
            byteRangeValid: range.valid,
            byteRangeMessage: range.message,
            revisionEnd: range.revisionEnd,
            coversCurrentFile: range.valid && range.revisionEnd === bytes.length,
            contents: contents?.bytes || new Uint8Array(),
            subFilter: dictionaryName(dictionary, "SubFilter") || "Unknown",
            filter: dictionaryName(dictionary, "Filter") || "Unknown",
            claimedName: dictionaryString(dictionary, "Name"),
            claimedReason: dictionaryString(dictionary, "Reason"),
            claimedLocation: dictionaryString(dictionary, "Location"),
            claimedSigningTime: dictionaryString(dictionary, "M"),
            error: contents?.error || (!contents ? "The signature container is missing." : ""),
        });
    }
    return signatures.sort((left, right) => left.revisionEnd - right.revisionEnd);
}

export function isPdf(bytes) {
    const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    return data.length >= PDF_HEADER.length && pdfText(data.slice(0, PDF_HEADER.length)) === PDF_HEADER;
}

export function joinedSignedBytes(bytes, byteRange) {
    const [firstStart, firstLength, secondStart, secondLength] = byteRange;
    const result = new Uint8Array(firstLength + secondLength);
    result.set(bytes.slice(firstStart, firstStart + firstLength), 0);
    result.set(bytes.slice(secondStart, secondStart + secondLength), firstLength);
    return result;
}

export function hex(buffer) {
    return Array.from(new Uint8Array(buffer), (value) => value.toString(16).padStart(2, "0")).join("");
}

export function formatBytes(bytes) {
    if (bytes < 1024) {
        return `${bytes} B`;
    }
    if (bytes < 1024 * 1024) {
        return `${(bytes / 1024).toFixed(1)} KB`;
    }
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function safeAttachmentName(name) {
    return String(name || "unnamed").split(/[\\/]/).pop().slice(0, 180) || "unnamed";
}

function artifactCandidates(item, attachments, usedIndexes) {
    const available = attachments.filter((_attachment, index) => !usedIndexes.has(index));
    if (item.kind === "authentication") {
        return available.filter(
            (attachment) =>
                attachment.name.startsWith("authentication-summary-") &&
                attachment.json?.artifact_sha256 === item.sha256
        );
    }
    if (item.kind === "signed") {
        return available.filter(
            (attachment) =>
                attachment.name.startsWith("final-") &&
                (item.name === "final signed PDF" || attachment.name.endsWith(`-${item.name}`))
        );
    }
    const canonical = available.filter(
        (attachment) =>
            attachment.name.startsWith(`${item.kind}-`) &&
            attachment.name.endsWith(`-${item.name}`)
    );
    if (canonical.length) {
        return canonical;
    }
    const exact = available.filter((attachment) => attachment.name === item.name);
    if (exact.length) {
        return exact;
    }
    // Older third-party dossiers may not include the artifact kind. Accept a
    // suffix only when it identifies one unused attachment unambiguously.
    return available.filter((attachment) => attachment.name.endsWith(`-${item.name}`));
}

/**
 * Match signed-manifest entries to embedded dossier files without allowing a
 * source and a frozen revision that share a display filename to collide.
 */
export function matchManifestArtifacts(manifest, attachments) {
    if (!manifest) {
        return {checked: 0, matched: 0, mismatches: []};
    }
    const expected = [...(manifest.artifacts || [])];
    const finalHash = String(manifest.final_sha256 || "").toLowerCase();
    if (
        finalHash &&
        !expected.some(
            (item) => item.kind === "signed" && String(item.sha256 || "").toLowerCase() === finalHash
        )
    ) {
        expected.push({kind: "signed", name: "final signed PDF", sha256: finalHash});
    }
    const mismatches = [];
    const usedIndexes = new Set();
    let matched = 0;
    for (const item of expected) {
        const candidates = artifactCandidates(item, attachments, usedIndexes);
        if (candidates.length !== 1) {
            mismatches.push(
                candidates.length
                    ? `${item.kind} “${item.name}”: more than one embedded file matches`
                    : `${item.kind} “${item.name}”: not represented in the dossier`
            );
            continue;
        }
        const candidate = candidates[0];
        const candidateIndex = attachments.indexOf(candidate);
        // A manifest entry consumes its uniquely identified attachment even
        // when the digest is wrong. A later entry must never reuse those
        // bytes and accidentally turn a corrupt dossier into a partial match.
        usedIndexes.add(candidateIndex);
        if (
            item.kind !== "authentication" &&
            candidate.sha256 !== String(item.sha256 || "").toLowerCase()
        ) {
            mismatches.push(
                `${item.kind} “${item.name}” (${candidate.name}): SHA-256 does not match the manifest`
            );
            continue;
        }
        matched++;
    }
    return {checked: expected.length, matched, mismatches};
}

export function overallStatus(signatures, translate = (value) => value) {
    if (!signatures.length) {
        return {
            tone: "neutral",
            title: translate("No digital signatures found"),
            detail: translate(
                "The PDF may contain a drawn signature, but no verifiable certificate-based signature was detected."
            ),
        };
    }
    if (signatures.some((signature) => signature.cryptoValid === false || !signature.byteRangeValid)) {
        return {
            tone: "danger",
            title: translate("At least one signature is invalid"),
            detail: translate(
                "Do not rely on this file until the failed signature or malformed byte range is explained."
            ),
        };
    }
    if (signatures.some((signature) => signature.cryptoValid !== true)) {
        return {
            tone: "warning",
            title: translate("Signature validation is incomplete"),
            detail: translate(
                "The file contains signatures, but this browser could not cryptographically verify every one."
            ),
        };
    }
    if (signatures.some((signature) => signature.weakAlgorithm)) {
        return {
            tone: "warning",
            title: translate("Signatures are intact, with legacy cryptography"),
            detail: translate(
                "The signatures check out, but at least one uses SHA-1. Prefer a current SHA-256 signature when available."
            ),
        };
    }
    const latest = signatures.at(-1);
    if (!latest.coversCurrentFile) {
        return {
            tone: "warning",
            title: translate("Signatures are valid, with later changes"),
            detail: translate(
                "The cryptographic signatures check out, but bytes were added after the latest signed revision."
            ),
        };
    }
    return {
        tone: "success",
        title: translate("All detected signatures are intact"),
        detail: translate(
            "The cryptographic signatures and signed byte ranges check out. Certificate trust still needs an authoritative validator."
        ),
    };
}
