import {animationFrame, expect, test} from "@odoo/hoot";
import {defineMailModels} from "@mail/../tests/mail_test_helpers";
import {mountWithCleanup} from "@web/../tests/web_test_helpers";

import {SignatureInspector} from "../src/js/signature_inspector.esm";

defineMailModels();

test("signature inspector starts with a local-processing promise and one clear action", async () => {
    await mountWithCleanup(SignatureInspector);

    expect(".usl_sign_inspector h1").toHaveText("Check Signatures");
    expect(".usl_sign_inspector_header").toHaveClass("border-bottom");
    expect(".usl_sign_inspector_privacy").toHaveText(/stays in this browser/);
    expect(".usl_sign_inspector_privacy").toHaveClass("alert-info");
    expect(".usl_sign_inspector_dropzone").toHaveCount(1);
    expect(".usl_sign_inspector_dropzone").toHaveClass("btn");
    expect(".usl_sign_inspector_dropzone").toHaveText(/signed PDF or evidence dossier/);
    expect("input[type='file']").toHaveAttribute("accept", "application/pdf,.pdf");
});

test("result view leads with integrity and keeps trust limitations explicit", async () => {
    const component = await mountWithCleanup(SignatureInspector);
    Object.assign(component.state, {
        stage: "ready",
        fileName: "agreement-signed.pdf",
        fileSize: 2048,
        result: {
            document: {
                name: "agreement-signed.pdf",
                sha256: "a".repeat(64),
                signatures: [
                    {
                        index: 1,
                        byteRangeValid: true,
                        byteRangeMessage: "This signature covers the current end of the PDF.",
                        coversCurrentFile: true,
                        cryptoValid: true,
                        signatureKind: "Document signature",
                        signedAt: "2026-08-27T10:00:00Z",
                        subFilter: "ETSI.CAdES.detached",
                        digestAlgorithm: "SHA-256",
                        signatureAlgorithm: "RSA with SHA-256",
                        certificate: {
                            subject: {label: "Alice Example", commonName: "Alice Example"},
                            issuer: {label: "Example Issuing CA"},
                            notBefore: "2026-01-01T00:00:00Z",
                            notAfter: "2027-01-01T00:00:00Z",
                            sha256: "b".repeat(64),
                        },
                        certificatePath: {internallyConsistent: true},
                        timestamp: {present: false, valid: null},
                    },
                ],
            },
            dossier: {
                detected: false,
                attachmentCount: 0,
                attachments: [],
                signedManifest: null,
                artifactChecks: {checked: 0, matched: 0, mismatches: []},
            },
        },
    });
    await animationFrame();

    expect(".usl_sign_inspector_verdict.is-success").toHaveText(/signatures are intact/i);
    expect(".usl_sign_inspector_verdict").toHaveClass("alert");
    expect(".usl_sign_inspector_verdict_icon .fa-check-circle").toHaveCount(1);
    expect(".usl_sign_inspector_filebar").toHaveClass("card");
    expect(".usl_sign_inspector_section").toHaveClass("card");
    expect(".usl_sign_inspector_limits").toHaveClass("card");
    expect(".usl_sign_signature_result").toHaveText(/Alice Example/);
    expect(".usl_sign_check_list").toHaveText(/public key verifies this signature/);
    expect(".usl_sign_inspector_limits").toHaveText(/current EU or company trust list/);
    expect(".usl_sign_inspector_limits").toHaveText(/advanced or qualified/);
});

test("USL certificates are labelled by their actual proof role", async () => {
    const component = await mountWithCleanup(SignatureInspector);
    expect(
        component.signatureKindLabel({
            certificate: {subject: {commonName: "USL Sign Personal: Alice Example"}},
        })
    ).toBe("Personal signer PAdES signature");
    expect(
        component.signatureKindLabel({
            certificate: {subject: {commonName: "USL Sign Platform Seal"}},
        })
    ).toBe("Platform integrity seal");
    expect(component.signatureKindLabel({signatureKind: "Document timestamp"})).toBe(
        "Document timestamp"
    );
});

test("unsupported and oversized files fail before PDF parsing", async () => {
    const component = await mountWithCleanup(SignatureInspector);

    await component.inspectFile(new File(["notes"], "notes.txt", {type: "text/plain"}));
    await animationFrame();
    expect(".usl_sign_inspector_error").toHaveText(/Choose a PDF file/);

    await component.inspectFile({
        size: 101 * 1024 * 1024,
        name: "too-large.pdf",
        type: "application/pdf",
    });
    await animationFrame();
    expect(".usl_sign_inspector_error").toHaveText(/too large/);
});

test("an intact dossier manifest cannot hide a failed embedded PDF signature", async () => {
    const component = await mountWithCleanup(SignatureInspector);
    Object.assign(component.state, {
        stage: "ready",
        result: {
            document: {name: "evidence-dossier.pdf", signatures: []},
            dossier: {
                detected: true,
                attachmentCount: 1,
                signedManifest: {hashValid: true, signatureValid: true},
                artifactChecks: {matched: 1, mismatches: []},
                attachments: [
                    {
                        index: 0,
                        pdf: {
                            name: "signed-document.pdf",
                            signatures: [
                                {byteRangeValid: true, cryptoValid: false, coversCurrentFile: true},
                            ],
                        },
                    },
                ],
            },
        },
    });

    expect(component.dossierVerdict.tone).toBe("danger");
    expect(component.dossierVerdict.title).toBe("The evidence dossier has integrity problems");
    await animationFrame();
    expect(".usl_sign_inspector_verdict.is-danger").toHaveText(
        /evidence dossier has integrity problems/i
    );
    expect(".usl_sign_inspector_limits .fa-check").toHaveCount(0);
});

test("a failed evidence manifest controls the overall file verdict", async () => {
    const component = await mountWithCleanup(SignatureInspector);
    Object.assign(component.state, {
        stage: "ready",
        result: {
            document: {name: "evidence-dossier.pdf", signatures: []},
            dossier: {
                detected: true,
                attachmentCount: 0,
                signedManifest: {hashValid: false, signatureValid: true},
                artifactChecks: {matched: 0, mismatches: []},
                attachments: [],
            },
        },
    });

    expect(component.verdict.tone).toBe("danger");
    expect(component.verdict.title).toBe("The evidence dossier has integrity problems");
});
