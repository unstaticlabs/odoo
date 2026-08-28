package com.unstaticlabs.sign.dss;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.util.Base64;
import java.util.List;
import java.util.Map;

import eu.europa.esig.dss.pades.PAdESSignatureParameters;
import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.pdmodel.PDPage;
import org.apache.pdfbox.pdmodel.interactive.form.PDAcroForm;
import org.junit.jupiter.api.Test;

final class ApplicationTest {
    @Test
    void certificateIdentityMatchesAllSignerTokensRegardlessOfOrderOrAccents() {
        assertTrue(Application.identityMatches(
                "José Martin",
                "CN=MARTIN, JOSE, SERIALNUMBER=FR-123"));
    }

    @Test
    void certificateIdentityCannotMatchANameAsASubstring() {
        assertFalse(Application.identityMatches("Joan Martin", "CN=Joanne Martin"));
    }

    @Test
    void certificateIdentityFailsClosedForMissingOrPartialNames() {
        assertFalse(Application.identityMatches("", "CN=Alice Dupont"));
        assertFalse(Application.identityMatches("Alice Dupont", "CN=Alice Durant"));
        assertFalse(Application.identityMatches("Alice Dupont", ""));
    }

    @Test
    void signingFieldsAreReservedBeforeTheFirstSignature() throws Exception {
        byte[] base = blankPdf();
        Application.PdfFormField reserved = new Application.PdfFormField(
                "usl_sign_7", 0, 12, 24, 30, 8, null);

        byte[] prepared = Application.prepareSigningFields(base, List.of(reserved));
        try (PDDocument parsed = org.apache.pdfbox.Loader.loadPDF(prepared)) {
            PDAcroForm form = parsed.getDocumentCatalog().getAcroForm();
            assertNotNull(form);
            assertNotNull(form.getField("usl_sign_7"));
        }

    }

    @Test
    void unsignedDocumentHasNoSignatureRevision() throws Exception {
        byte[] base = blankPdf();

        assertFalse(Application.hasSignatureRevision(base));
    }

    @Test
    void visiblePersonalSignatureIsConfiguredAsNativePadesAppearance() throws Exception {
        PAdESSignatureParameters parameters = new PAdESSignatureParameters();
        byte[] png = Base64.getDecoder().decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=");

        Application.configureSignatureAppearance(
                parameters,
                blankPdf(),
                Map.of(
                        "image", Base64.getEncoder().encodeToString(png),
                        "page", 1,
                        "position_x", 12,
                        "position_y", 36,
                        "width", 30,
                        "height", 8));

        assertNotNull(parameters.getImageParameters().getImage());
        assertEquals(1, parameters.getImageParameters().getFieldParameters().getPage());
        assertTrue(parameters.getImageParameters().getFieldParameters().getWidth() > 0);
        assertTrue(parameters.getImageParameters().getFieldParameters().getHeight() > 0);
    }

    private static byte[] blankPdf() throws Exception {
        try (PDDocument document = new PDDocument();
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            document.addPage(new PDPage());
            document.save(output);
            return output.toByteArray();
        }
    }
}
