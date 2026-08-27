package com.unstaticlabs.sign.dss;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertArrayEquals;
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
    void overlaysAreAppendedWithoutRewritingTheBaseRevision() throws Exception {
        byte[] base = blankPdf();
        byte[] overlay = blankPdf();

        byte[] result = Application.applyIncrementalOverlays(
                base,
                List.of(new Application.PdfOverlay(0, overlay)));

        assertTrue(result.length > base.length);
        assertArrayEquals(base, java.util.Arrays.copyOf(result, base.length));
        assertFalse(startsWith(
                result,
                base.length,
                "%PDF-".getBytes(java.nio.charset.StandardCharsets.US_ASCII)));
        try (PDDocument parsed = org.apache.pdfbox.Loader.loadPDF(result)) {
            assertEquals(1, parsed.getNumberOfPages());
        }
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

    private static boolean startsWith(byte[] value, int offset, byte[] expected) {
        if (value.length < offset + expected.length) {
            return false;
        }
        for (int index = 0; index < expected.length; index++) {
            if (value[offset + index] != expected[index]) {
                return false;
            }
        }
        return true;
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
