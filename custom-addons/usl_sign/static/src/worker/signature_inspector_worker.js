import * as asn1js from "asn1js";
import * as pkijs from "pkijs";

import {
    encapsulatedSha1Matches,
    hex,
    isPdf,
    joinedSignedBytes,
    parsePdfSignatures,
    safeAttachmentName,
    signatureVerificationMode,
} from "../js/signature_inspector_utils.esm.js";

const OIDS = {
    commonName: "2.5.4.3",
    country: "2.5.4.6",
    locality: "2.5.4.7",
    organization: "2.5.4.10",
    organizationalUnit: "2.5.4.11",
    email: "1.2.840.113549.1.9.1",
    signingTime: "1.2.840.113549.1.9.5",
    timestampToken: "1.2.840.113549.1.9.16.2.14",
    qcStatements: "1.3.6.1.5.5.7.1.3",
};

const ALGORITHMS = {
    "1.3.14.3.2.26": "SHA-1",
    "2.16.840.1.101.3.4.2.1": "SHA-256",
    "2.16.840.1.101.3.4.2.2": "SHA-384",
    "2.16.840.1.101.3.4.2.3": "SHA-512",
    "1.2.840.113549.1.1.1": "RSA",
    "1.2.840.113549.1.1.5": "RSA with SHA-1",
    "1.2.840.113549.1.1.10": "RSA-PSS",
    "1.2.840.113549.1.1.11": "RSA with SHA-256",
    "1.2.840.113549.1.1.12": "RSA with SHA-384",
    "1.2.840.113549.1.1.13": "RSA with SHA-512",
    "1.2.840.10045.2.1": "Elliptic curve",
    "1.2.840.10045.4.3.2": "ECDSA with SHA-256",
    "1.2.840.10045.4.3.3": "ECDSA with SHA-384",
    "1.2.840.10045.4.3.4": "ECDSA with SHA-512",
};

function copyBuffer(bytes) {
    return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
}

function asBytes(value) {
    return value instanceof Uint8Array ? value : new Uint8Array(value);
}

async function sha256(bytes) {
    return hex(await crypto.subtle.digest("SHA-256", asBytes(bytes)));
}

function attributeValue(attribute) {
    const value = attribute?.values?.[0];
    if (value?.toDate) {
        return value.toDate().toISOString();
    }
    return value?.valueBlock?.value || "";
}

function pdfDate(value) {
    const match = String(value || "").match(
        /^D:(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(Z|([+-])(\d{2})'?(\d{2})'?)?/
    );
    if (!match) {
        return "";
    }
    const utc = Date.UTC(
        Number(match[1]),
        Number(match[2]) - 1,
        Number(match[3]),
        Number(match[4]),
        Number(match[5]),
        Number(match[6])
    );
    const offsetMinutes = match[7] && match[7] !== "Z" ? Number(match[9]) * 60 + Number(match[10]) : 0;
    const adjusted = match[8] === "+" ? utc - offsetMinutes * 60_000 : utc + offsetMinutes * 60_000;
    return new Date(adjusted).toISOString();
}

function distinguishedName(name) {
    const values = {};
    for (const item of name?.typesAndValues || []) {
        const value = item.value?.valueBlock?.value || "";
        if (value) {
            values[item.type] = value;
        }
    }
    const parts = [
        values[OIDS.commonName],
        values[OIDS.organization],
        values[OIDS.organizationalUnit],
        values[OIDS.locality],
        values[OIDS.country],
    ].filter(Boolean);
    return {
        label: parts.join(", ") || "Certificate name unavailable",
        commonName: values[OIDS.commonName] || "",
        organization: values[OIDS.organization] || "",
        email: values[OIDS.email] || "",
    };
}

function certificateSummary(certificate) {
    if (!certificate) {
        return null;
    }
    return {
        subject: distinguishedName(certificate.subject),
        issuer: distinguishedName(certificate.issuer),
        serialNumber: hex(certificate.serialNumber.valueBlock.valueHexView),
        notBefore: certificate.notBefore.value.toISOString(),
        notAfter: certificate.notAfter.value.toISOString(),
        publicKeyAlgorithm:
            ALGORITHMS[certificate.subjectPublicKeyInfo.algorithm.algorithmId] ||
            certificate.subjectPublicKeyInfo.algorithm.algorithmId,
        certificateSignatureAlgorithm:
            ALGORITHMS[certificate.signatureAlgorithm.algorithmId] ||
            certificate.signatureAlgorithm.algorithmId,
        hasQualifiedCertificateStatements: Boolean(
            certificate.extensions?.some((extension) => extension.extnID === OIDS.qcStatements)
        ),
    };
}

function certificatesFrom(signedData) {
    return (signedData.certificates || []).filter(
        (certificate) => certificate instanceof pkijs.Certificate
    );
}

async function certificatePathSummary(certificates, leaf, checkDate) {
    if (!leaf) {
        return {
            count: certificates.length,
            internallyConsistent: false,
            completeToSelfSignedRoot: false,
            validAtSigningTime: null,
            verifiedIssuerLinks: 0,
        };
    }
    const path = [leaf];
    const seen = new Set();
    let current = leaf;
    let internallyConsistent = true;
    let completeToSelfSignedRoot = false;
    let verifiedIssuerLinks = 0;
    while (current && path.length <= certificates.length + 1) {
        const key = `${distinguishedName(current.subject).label}:${hex(
            current.serialNumber.valueBlock.valueHexView
        )}`;
        if (seen.has(key)) {
            internallyConsistent = false;
            break;
        }
        seen.add(key);
        if (current.subject.isEqual(current.issuer)) {
            completeToSelfSignedRoot = await current.verify(current);
            internallyConsistent &&= completeToSelfSignedRoot;
            verifiedIssuerLinks += completeToSelfSignedRoot ? 1 : 0;
            break;
        }
        const issuer = certificates.find(
            (candidate) => candidate !== current && candidate.subject.isEqual(current.issuer)
        );
        if (!issuer) {
            break;
        }
        const linkVerified = await current.verify(issuer);
        internallyConsistent &&= linkVerified;
        verifiedIssuerLinks += linkVerified ? 1 : 0;
        path.push(issuer);
        current = issuer;
    }
    const moment = checkDate || new Date();
    const validAtSigningTime = path.every(
        (certificate) => certificate.notBefore.value <= moment && certificate.notAfter.value >= moment
    );
    return {
        count: certificates.length,
        pathLength: path.length,
        internallyConsistent,
        completeToSelfSignedRoot,
        validAtSigningTime,
        verifiedIssuerLinks,
    };
}

function encapsulatedContentBytes(signedData) {
    const content = signedData.encapContentInfo.eContent;
    if (!content) {
        return new Uint8Array();
    }
    if (content.valueBlock.valueHexView?.byteLength) {
        return asBytes(content.valueBlock.valueHexView);
    }
    const parts = content.valueBlock.value || [];
    const length = parts.reduce(
        (total, part) => total + (part.valueBlock.valueHexView?.byteLength || 0),
        0
    );
    const result = new Uint8Array(length);
    let offset = 0;
    for (const part of parts) {
        const bytes = asBytes(part.valueBlock.valueHexView || new Uint8Array());
        result.set(bytes, offset);
        offset += bytes.length;
    }
    return result;
}

function readCms(contents) {
    const decoded = asn1js.fromBER(copyBuffer(contents));
    if (decoded.offset === -1) {
        throw new Error("The CMS signature container is not valid ASN.1 data.");
    }
    const contentInfo = new pkijs.ContentInfo({schema: decoded.result});
    if (contentInfo.contentType !== pkijs.ContentInfo.SIGNED_DATA) {
        throw new Error("The PDF signature container is not CMS SignedData.");
    }
    return new pkijs.SignedData({schema: contentInfo.content});
}

async function verifyTimestampAttribute(signerInfo) {
    const attribute = signerInfo.unsignedAttrs?.attributes?.find(
        (item) => item.type === OIDS.timestampToken
    );
    if (!attribute?.values?.length) {
        return {present: false, valid: null};
    }
    try {
        const contentInfo = new pkijs.ContentInfo({schema: attribute.values[0]});
        const timestamp = new pkijs.SignedData({schema: contentInfo.content});
        const result = await timestamp.verify({
            signer: 0,
            data: copyBuffer(signerInfo.signature.valueBlock.valueHexView),
            checkChain: false,
            extendedMode: true,
        });
        return {
            present: true,
            valid: result.signatureVerified === true,
            time: result.date?.toISOString() || "",
            authority: certificateSummary(result.signerCertificate)?.subject.label || "",
        };
    } catch (error) {
        return {present: true, valid: false, error: error.message};
    }
}

async function inspectSignature(bytes, parsed) {
    const result = {...parsed};
    delete result.contents;
    if (!parsed.byteRangeValid || parsed.error) {
        return {...result, cryptoValid: false, validationError: parsed.error || parsed.byteRangeMessage};
    }
    const verificationMode = signatureVerificationMode(parsed.subFilter);
    if (verificationMode === "unsupported") {
        return {
            ...result,
            cryptoValid: null,
            validationError: `The ${parsed.subFilter || "unknown"} PDF signature format is not supported by this browser check.`,
        };
    }
    try {
        const signedData = readCms(parsed.contents);
        if (signedData.signerInfos.length !== 1) {
            return {
                ...result,
                cryptoValid: null,
                validationError:
                    "This browser check does not support multiple signers in one PDF signature container.",
            };
        }
        const detachedData = joinedSignedBytes(bytes, parsed.byteRange);
        const verification = await signedData.verify({
            signer: 0,
            data:
                verificationMode === "encapsulated-sha1"
                    ? undefined
                    : copyBuffer(detachedData),
            checkChain: false,
            extendedMode: true,
        });
        let contentBindingValid = true;
        if (verificationMode === "encapsulated-sha1") {
            contentBindingValid = await encapsulatedSha1Matches(
                detachedData,
                encapsulatedContentBytes(signedData)
            );
        }
        const signerInfo = signedData.signerInfos[0];
        const signedAt =
            attributeValue(
                signerInfo.signedAttrs?.attributes?.find(
                    (attribute) => attribute.type === OIDS.signingTime
                )
            ) ||
            pdfDate(parsed.claimedSigningTime) ||
            verification.date?.toISOString() ||
            "";
        const certificates = certificatesFrom(signedData);
        const certificate = verification.signerCertificate || certificates[0] || null;
        const certificateSummaryValue = certificateSummary(certificate);
        if (certificate) {
            certificateSummaryValue.sha256 = await sha256(certificate.toSchema().toBER(false));
        }
        const timestamp = await verifyTimestampAttribute(signerInfo);
        return {
            ...result,
            cryptoValid: verification.signatureVerified === true && contentBindingValid,
            validationError:
                verification.signatureVerified !== true
                    ? verification.message || "Cryptographic verification failed."
                    : contentBindingValid
                    ? ""
                    : "The encapsulated SHA-1 digest does not match the signed PDF bytes.",
            weakAlgorithm: verificationMode === "encapsulated-sha1",
            signatureKind:
                signedData.encapContentInfo.eContentType === pkijs.id_eContentType_TSTInfo
                    ? "Document timestamp"
                    : "Document signature",
            digestAlgorithm:
                ALGORITHMS[signerInfo.digestAlgorithm.algorithmId] ||
                signerInfo.digestAlgorithm.algorithmId,
            signatureAlgorithm:
                ALGORITHMS[signerInfo.signatureAlgorithm.algorithmId] ||
                signerInfo.signatureAlgorithm.algorithmId,
            signedAt,
            certificate: certificateSummaryValue,
            certificatePath: await certificatePathSummary(
                certificates,
                certificate,
                signedAt ? new Date(signedAt) : new Date()
            ),
            timestamp,
            embeddedRevocationMaterial: Boolean(
                signedData.crls?.length || signedData.ocsps?.length
            ),
        };
    } catch (error) {
        const inconclusive = /not supported|unsupported|not implemented|unable to find signer certificate|no certificates attached|algorithm.+(?:unknown|not found)/i.test(
            error.message || ""
        );
        return {
            ...result,
            cryptoValid: inconclusive ? null : false,
            validationError: error.message,
        };
    }
}

async function inspectPdf(bytes, name) {
    const parsed = parsePdfSignatures(bytes);
    const signatures = [];
    for (const signature of parsed) {
        signatures.push(await inspectSignature(bytes, signature));
    }
    return {
        name,
        size: bytes.byteLength,
        sha256: await sha256(bytes),
        signatures,
        hasDssMaterial: /\/DSS\b/.test(new TextDecoder("latin1").decode(bytes)),
    };
}

function fromBase64(value) {
    const binary = atob(value);
    return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

async function verifyDetachedManifest(wrapper) {
    if (wrapper?.format !== "usl-sign-detached-manifest-signature-v1") {
        return null;
    }
    const manifestBytes = fromBase64(wrapper.manifest || "");
    const manifestSha256 = await sha256(manifestBytes);
    const certificates = (wrapper.certificate_chain || []).map((encoded) =>
        pkijs.Certificate.fromBER(copyBuffer(fromBase64(encoded)))
    );
    const leaf = certificates[0];
    let signatureValid = false;
    let error = "";
    try {
        if (!leaf) {
            throw new Error("The signed manifest contains no certificate.");
        }
        const key = await leaf.getPublicKey();
        const signature = fromBase64(wrapper.signature || "");
        const algorithm = String(wrapper.signature_algorithm || "").toUpperCase();
        if (algorithm.includes("ECDSA")) {
            const decoded = asn1js.fromBER(copyBuffer(signature));
            const curveOid = leaf.subjectPublicKeyInfo.algorithm.algorithmParams?.valueBlock?.toString();
            const pointSize = {
                "1.2.840.10045.3.1.7": 32,
                "1.3.132.0.34": 48,
                "1.3.132.0.35": 66,
            }[curveOid];
            if (!pointSize) {
                throw new Error(`Unsupported manifest certificate curve: ${curveOid || "unknown"}.`);
            }
            const webCryptoSignature = pkijs.createECDSASignatureFromCMS(
                decoded.result,
                pointSize
            );
            signatureValid = await crypto.subtle.verify(
                {name: "ECDSA", hash: "SHA-256"},
                key,
                webCryptoSignature,
                manifestBytes
            );
        } else if (algorithm.includes("RSA")) {
            signatureValid = await crypto.subtle.verify(
                {name: "RSASSA-PKCS1-v1_5"},
                key,
                signature,
                manifestBytes
            );
        } else {
            throw new Error(`Unsupported manifest signature algorithm: ${algorithm || "unknown"}.`);
        }
    } catch (manifestError) {
        error = manifestError.message;
    }
    let manifest = null;
    try {
        manifest = JSON.parse(new TextDecoder().decode(manifestBytes));
    } catch {
        error ||= "The signed manifest payload is not valid JSON.";
    }
    return {
        hashValid: manifestSha256 === String(wrapper.manifest_sha256 || "").toLowerCase(),
        signatureValid,
        error,
        manifest,
        certificate: certificateSummary(leaf),
        certificatePath: await certificatePathSummary(certificates, leaf, new Date()),
    };
}

function matchManifestArtifacts(manifest, attachments) {
    if (!manifest) {
        return {checked: 0, matched: 0, mismatches: []};
    }
    const expected = [...(manifest.artifacts || [])];
    if (manifest.final_sha256) {
        expected.push({kind: "signed", name: "final signed PDF", sha256: manifest.final_sha256});
    }
    const mismatches = [];
    let matched = 0;
    for (const item of expected) {
        const candidate =
            item.kind === "authentication"
                ? attachments.find(
                      (attachment) =>
                          attachment.name.startsWith("authentication-summary-") &&
                          attachment.json?.artifact_sha256 === item.sha256
                  )
                : attachments.find(
                      (attachment) =>
                          attachment.name === item.name ||
                          attachment.name.endsWith(`-${item.name}`) ||
                          (item.kind === "signed" && attachment.name.startsWith("final-"))
                  );
        if (!candidate) {
            mismatches.push(`${item.name}: not represented in the dossier`);
        } else if (
            item.kind !== "authentication" &&
            candidate.sha256 !== String(item.sha256 || "").toLowerCase()
        ) {
            mismatches.push(`${candidate.name}: SHA-256 does not match the manifest`);
        } else {
            matched++;
        }
    }
    return {checked: expected.length, matched, mismatches};
}

async function inspect(payload) {
    if (!globalThis.crypto?.subtle) {
        throw new Error("Secure browser cryptography is unavailable. Open Odoo over HTTPS or localhost and try again.");
    }
    const bytes = asBytes(payload.document);
    if (!isPdf(bytes)) {
        throw new Error("This file is not a PDF document.");
    }
    const document = await inspectPdf(bytes, safeAttachmentName(payload.name));
    const attachments = [];
    let signedManifest = null;
    for (const [index, input] of (payload.attachments || []).entries()) {
        const content = asBytes(input.content);
        const attachment = {
            name: safeAttachmentName(input.name),
            description: String(input.description || "").slice(0, 500),
            size: content.byteLength,
            sha256: await sha256(content),
            pdf: null,
            index,
        };
        if (isPdf(content)) {
            attachment.pdf = await inspectPdf(content, attachment.name);
        } else if (attachment.name.toLowerCase().endsWith(".json")) {
            try {
                const parsed = JSON.parse(new TextDecoder().decode(content));
                attachment.json = parsed;
                signedManifest ||= await verifyDetachedManifest(parsed);
            } catch {
                // Other JSON evidence remains listed and hashable without being interpreted.
            }
        }
        attachments.push(attachment);
    }
    const artifactChecks = matchManifestArtifacts(signedManifest?.manifest, attachments);
    for (const attachment of attachments) {
        delete attachment.json;
    }
    if (signedManifest?.manifest) {
        signedManifest.summary = {
            requestName: signedManifest.manifest.request_name || "",
            requestedTrust: signedManifest.manifest.requested_trust || "",
            achievedTrust: signedManifest.manifest.achieved_trust || "",
            finalSha256: signedManifest.manifest.final_sha256 || "",
        };
        delete signedManifest.manifest;
    }
    return {
        document,
        dossier: {
            detected: attachments.length > 0,
            attachmentCount: attachments.length,
            attachments,
            signedManifest,
            artifactChecks,
        },
    };
}

self.onmessage = async (event) => {
    try {
        self.postMessage({ok: true, result: await inspect(event.data)});
    } catch (error) {
        self.postMessage({ok: false, error: error.message || "The PDF could not be inspected."});
    }
};
