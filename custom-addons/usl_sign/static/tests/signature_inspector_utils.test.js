import {expect, test} from "@odoo/hoot";

import {
    bytesEqual,
    encapsulatedSha1Matches,
    formatBytes,
    isPdf,
    overallStatus,
    parsePdfSignatures,
    signatureVerificationMode,
} from "../src/js/signature_inspector_utils.esm";

function syntheticSignedPdf({
    firstLengthAdjustment = 0,
    secondStartAdjustment = 0,
    secondLengthAdjustment = 0,
} = {}) {
    const placeholder = "0000000000";
    let source = [
        "%PDF-1.7",
        "1 0 obj",
        "<< /Type /Sig /Filter /Adobe.PPKLite /SubFilter /adbe.pkcs7.detached",
        "/Name (Alice Example) /Reason (Approval) /Location (Paris)",
        `/ByteRange [0 ${placeholder} ${placeholder} ${placeholder}]`,
        "/Contents <3000>",
        ">>",
        "endobj",
        "%%EOF",
    ].join("\n");
    const contentsStart = source.indexOf("<3000>");
    const secondStart = contentsStart + "<3000>".length;
    const adjustedSecondStart = secondStart + secondStartAdjustment;
    const secondLength = source.length - adjustedSecondStart + secondLengthAdjustment;
    const values = [
        contentsStart + firstLengthAdjustment,
        adjustedSecondStart,
        secondLength,
    ].map((value) =>
        String(value).padStart(placeholder.length, "0")
    );
    values.forEach((value) => {
        source = source.replace(placeholder, value);
    });
    return new TextEncoder().encode(source);
}

test("strict PDF signature parsing exposes the signed revision and claimed metadata", () => {
    const [signature] = parsePdfSignatures(syntheticSignedPdf());

    expect(signature.byteRangeValid).toBe(true);
    expect(signature.coversCurrentFile).toBe(true);
    expect(signature.subFilter).toBe("adbe.pkcs7.detached");
    expect(signature.claimedName).toBe("Alice Example");
    expect(signature.claimedReason).toBe("Approval");
    expect(signature.claimedLocation).toBe("Paris");
    expect([...signature.contents]).toEqual([0x30, 0x00]);
});

test("a byte range outside the file is rejected before cryptographic validation", () => {
    const [signature] = parsePdfSignatures(syntheticSignedPdf({secondLengthAdjustment: 20}));

    expect(signature.byteRangeValid).toBe(false);
    expect(signature.byteRangeMessage).toMatch(/malformed/);
});

test("the unsigned byte-range gap may contain only the signature container", () => {
    const [earlyGap] = parsePdfSignatures(syntheticSignedPdf({firstLengthAdjustment: -1}));
    const [lateGap] = parsePdfSignatures(syntheticSignedPdf({secondStartAdjustment: 1}));

    expect(earlyGap.byteRangeValid).toBe(false);
    expect(lateGap.byteRangeValid).toBe(false);
    expect(earlyGap.byteRangeMessage).toMatch(/malformed/);
    expect(lateGap.byteRangeMessage).toMatch(/malformed/);
});

test("supported signature modes and legacy SHA-1 content binding are explicit", async () => {
    const signedBytes = new TextEncoder().encode("signed revision");
    const digest = new Uint8Array([1, 2, 3, 4]);
    const subtle = {
        digest: async (_algorithm, value) =>
            new TextDecoder().decode(value) === "signed revision"
                ? digest.slice().buffer
                : new Uint8Array([9, 9, 9, 9]).buffer,
    };

    expect(signatureVerificationMode("ETSI.CAdES.detached")).toBe("detached");
    expect(signatureVerificationMode("ETSI.RFC3161")).toBe("timestamp");
    expect(signatureVerificationMode("adbe.pkcs7.sha1")).toBe("encapsulated-sha1");
    expect(signatureVerificationMode("adbe.x509.rsa_sha1")).toBe("unsupported");
    expect(await encapsulatedSha1Matches(signedBytes, digest, subtle)).toBe(true);
    expect(
        await encapsulatedSha1Matches(new TextEncoder().encode("tampered"), digest, subtle)
    ).toBe(false);
    expect(bytesEqual(digest, digest.slice())).toBe(true);
});

test("signature-like text inside an embedded PDF stream is ignored", () => {
    const streamPayload = "/ByteRange [0 12 34 56] /Contents <3000>";
    const source = new TextEncoder().encode(
        `%PDF-1.7\n1 0 obj\n<< /Length ${streamPayload.length} >>\nstream\n${streamPayload}\nendstream\nendobj\n%%EOF`
    );

    expect(parsePdfSignatures(source)).toEqual([]);
});

test("plain-language overall status never overstates certificate trust", () => {
    expect(overallStatus([]).title).toBe("No digital signatures found");
    expect(
        overallStatus([{byteRangeValid: true, cryptoValid: true, coversCurrentFile: true}])
    ).toEqual({
        tone: "success",
        title: "All detected signatures are intact",
        detail: "The cryptographic signatures and signed byte ranges check out. Certificate trust still needs an authoritative validator.",
    });
    expect(
        overallStatus([{byteRangeValid: true, cryptoValid: true, coversCurrentFile: false}]).tone
    ).toBe("warning");
    expect(overallStatus([{byteRangeValid: true, cryptoValid: false}]).tone).toBe("danger");
    expect(
        overallStatus([
            {
                byteRangeValid: true,
                cryptoValid: true,
                coversCurrentFile: true,
                weakAlgorithm: true,
            },
        ]).tone
    ).toBe("warning");
});

test("file helpers recognize PDFs and present bounded readable sizes", () => {
    expect(isPdf(new TextEncoder().encode("%PDF-1.7\n"))).toBe(true);
    expect(isPdf(new TextEncoder().encode("not a pdf"))).toBe(false);
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(2 * 1024 * 1024)).toBe("2.0 MB");
});
