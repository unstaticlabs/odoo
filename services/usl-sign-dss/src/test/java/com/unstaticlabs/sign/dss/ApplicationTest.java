package com.unstaticlabs.sign.dss;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.awt.Color;
import java.awt.Graphics2D;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import javax.imageio.ImageIO;
import java.util.Base64;
import java.util.List;
import java.util.Map;

import eu.europa.esig.dss.enumerations.ImageScaling;
import eu.europa.esig.dss.model.DSSDocument;
import eu.europa.esig.dss.model.InMemoryDocument;
import eu.europa.esig.dss.pades.PAdESSignatureParameters;
import eu.europa.esig.dss.pades.signature.PAdESService;
import eu.europa.esig.dss.spi.validation.CommonCertificateVerifier;
import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.pdmodel.PDPage;
import org.apache.pdfbox.pdmodel.common.PDRectangle;
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
        // Sign positions and DSS origins are both measured from the top-left
        // corner: 36 % down a 792 pt Letter page is 285.12 pt from the top.
        assertEquals(12f / 100 * 612, parameters.getImageParameters().getFieldParameters().getOriginX(), 0.01f);
        assertEquals(36f / 100 * 792, parameters.getImageParameters().getFieldParameters().getOriginY(), 0.01f);
        assertEquals(30f / 100 * 612, parameters.getImageParameters().getFieldParameters().getWidth(), 0.01f);
        assertEquals(8f / 100 * 792, parameters.getImageParameters().getFieldParameters().getHeight(), 0.01f);
        assertEquals(ImageScaling.ZOOM_AND_CENTER, parameters.getImageParameters().getImageScaling());
    }

    @Test
    void personalSignatureAppearanceIsDrawnInsideThePlacedBox() throws Exception {
        assertInkInsidePlacedBox(blankPdf());
    }

    @Test
    void personalSignatureAppearanceHonoursAnOffsetMediaBox() throws Exception {
        // Some generated PDFs start their media box away from 0; offset x and y.
        assertInkInsidePlacedBox(pdfWithBoxes(new PDRectangle(20, 8, 612, 792), null));
    }

    @Test
    void personalSignatureAppearanceIsPlacedOnTheVisibleCropBox() throws Exception {
        // Sign places fields on the page as displayed, which is the crop box.
        assertInkInsidePlacedBox(pdfWithBoxes(
                new PDRectangle(0, 0, 612, 792), new PDRectangle(40, 60, 500, 650)));
    }

    private static void assertInkInsidePlacedBox(byte[] pdf) throws Exception {
        // Render DSS's own preview of the visible signature (the crop box, as a
        // viewer shows it) and measure where the ink lands: the box is 12 %
        // from the left, 36 % from the top, 30 % wide and 8 % high.
        BufferedImage ink = new BufferedImage(300, 80, BufferedImage.TYPE_INT_RGB);
        Graphics2D graphics = ink.createGraphics();
        graphics.setColor(Color.BLACK);
        graphics.fillRect(0, 0, 300, 80);
        graphics.dispose();
        ByteArrayOutputStream png = new ByteArrayOutputStream();
        ImageIO.write(ink, "png", png);
        PAdESSignatureParameters parameters = new PAdESSignatureParameters();
        Application.configureSignatureAppearance(
                parameters,
                pdf,
                Map.of(
                        "image", Base64.getEncoder().encodeToString(png.toByteArray()),
                        "page", 1,
                        "position_x", 12,
                        "position_y", 36,
                        "width", 30,
                        "height", 8));
        PAdESService service = new PAdESService(new CommonCertificateVerifier());
        DSSDocument preview = service.previewPageWithVisualSignature(
                new InMemoryDocument(pdf, "document.pdf"), parameters);
        BufferedImage page;
        try (var stream = preview.openStream()) {
            page = ImageIO.read(stream);
        }
        int top = page.getHeight();
        int bottom = -1;
        int left = page.getWidth();
        int right = -1;
        for (int y = 0; y < page.getHeight(); y++) {
            for (int x = 0; x < page.getWidth(); x++) {
                if ((page.getRGB(x, y) & 0xFF) < 128) {
                    top = Math.min(top, y);
                    bottom = Math.max(bottom, y);
                    left = Math.min(left, x);
                    right = Math.max(right, x);
                }
            }
        }
        double height = page.getHeight();
        double width = page.getWidth();
        assertTrue(bottom > 0, "The signature appearance was not drawn.");
        // ZOOM_AND_CENTER keeps the 300×80 ink ratio inside the 30 % × 8 % box.
        assertTrue(top / height >= 0.36 - 0.01 && (bottom + 1) / height <= 0.44 + 0.01,
                "Ink rows " + top + ".." + bottom + " of " + page.getHeight() + " leave the 36–44 % band.");
        assertTrue(left / width >= 0.12 - 0.01 && (right + 1) / width <= 0.42 + 0.01,
                "Ink columns " + left + ".." + right + " of " + page.getWidth() + " leave the 12–42 % band.");
    }

    private static byte[] pdfWithBoxes(PDRectangle mediaBox, PDRectangle cropBox) throws Exception {
        try (PDDocument document = new PDDocument();
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            PDPage page = new PDPage(mediaBox);
            if (cropBox != null) {
                page.setCropBox(cropBox);
            }
            document.addPage(page);
            document.save(output);
            return output.toByteArray();
        }
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
