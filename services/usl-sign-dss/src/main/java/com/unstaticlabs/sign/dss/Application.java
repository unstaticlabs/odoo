package com.unstaticlabs.sign.dss;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import com.sun.net.httpserver.HttpsConfigurator;
import com.sun.net.httpserver.HttpsParameters;
import com.sun.net.httpserver.HttpsServer;
import eu.europa.esig.dss.diagnostic.DiagnosticData;
import eu.europa.esig.dss.diagnostic.PDFRevisionWrapper;
import eu.europa.esig.dss.diagnostic.SignatureWrapper;
import eu.europa.esig.dss.enumerations.DigestAlgorithm;
import eu.europa.esig.dss.enumerations.Indication;
import eu.europa.esig.dss.enumerations.SignatureAlgorithm;
import eu.europa.esig.dss.enumerations.SignatureLevel;
import eu.europa.esig.dss.enumerations.SignatureQualification;
import eu.europa.esig.dss.model.DSSDocument;
import eu.europa.esig.dss.model.InMemoryDocument;
import eu.europa.esig.dss.model.SignatureValue;
import eu.europa.esig.dss.model.ToBeSigned;
import eu.europa.esig.dss.model.x509.CertificateToken;
import eu.europa.esig.dss.pades.PAdESSignatureParameters;
import eu.europa.esig.dss.pades.signature.PAdESService;
import eu.europa.esig.dss.pades.validation.PDFDocumentValidator;
import eu.europa.esig.dss.pades.validation.PdfRevision;
import eu.europa.esig.dss.pdf.PdfSignatureRevision;
import eu.europa.esig.dss.service.http.commons.FileCacheDataLoader;
import eu.europa.esig.dss.service.tsp.OnlineTSPSource;
import eu.europa.esig.dss.simplereport.SimpleReport;
import eu.europa.esig.dss.spi.validation.CommonCertificateVerifier;
import eu.europa.esig.dss.spi.x509.CertificateSource;
import eu.europa.esig.dss.spi.x509.CommonTrustedCertificateSource;
import eu.europa.esig.dss.spi.x509.KeyStoreCertificateSource;
import eu.europa.esig.dss.spi.tsl.TrustedListsCertificateSource;
import eu.europa.esig.dss.token.DSSPrivateKeyEntry;
import eu.europa.esig.dss.token.Pkcs12SignatureToken;
import eu.europa.esig.dss.tsl.function.OfficialJournalSchemeInformationURI;
import eu.europa.esig.dss.tsl.job.TLValidationJob;
import eu.europa.esig.dss.tsl.sha2.Sha2FileCacheDataLoader;
import eu.europa.esig.dss.tsl.source.LOTLSource;
import eu.europa.esig.dss.tsl.sync.ExpirationAndSignatureCheckStrategy;
import eu.europa.esig.dss.validation.SignedDocumentValidator;
import eu.europa.esig.dss.validation.reports.Reports;

import org.apache.pdfbox.cos.COSArray;
import org.apache.pdfbox.cos.COSName;
import org.apache.pdfbox.cos.COSString;
import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.pdmodel.PDDocumentCatalog;
import org.apache.pdfbox.pdmodel.PDDocumentInformation;
import org.apache.pdfbox.pdmodel.PDDocumentNameDictionary;
import org.apache.pdfbox.pdmodel.PDPage;
import org.apache.pdfbox.pdmodel.PDPageContentStream;
import org.apache.pdfbox.pdmodel.common.PDMetadata;
import org.apache.pdfbox.pdmodel.common.PDRectangle;
import org.apache.pdfbox.pdmodel.common.filespecification.PDComplexFileSpecification;
import org.apache.pdfbox.pdmodel.common.filespecification.PDEmbeddedFile;
import org.apache.pdfbox.pdmodel.font.PDType0Font;
import org.apache.pdfbox.pdmodel.graphics.color.PDOutputIntent;
import org.apache.pdfbox.pdmodel.PDEmbeddedFilesNameTreeNode;
import org.apache.xmpbox.XMPMetadata;
import org.apache.xmpbox.schema.AdobePDFSchema;
import org.apache.xmpbox.schema.DublinCoreSchema;
import org.apache.xmpbox.schema.PDFAIdentificationSchema;
import org.apache.xmpbox.schema.XMPBasicSchema;
import org.apache.xmpbox.xml.XmpSerializer;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.InputStream;
import java.io.IOException;
import java.awt.color.ColorSpace;
import java.awt.color.ICC_Profile;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyStore.PasswordProtection;
import java.security.KeyStore;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.text.Normalizer;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.Calendar;
import java.util.Collections;
import java.util.GregorianCalendar;
import java.util.HashSet;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

import javax.net.ssl.KeyManagerFactory;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLParameters;
import javax.net.ssl.TrustManagerFactory;

public final class Application {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final int MAX_REQUEST_BYTES = Integer.parseInt(
            System.getenv().getOrDefault("USL_DSS_MAX_REQUEST_BYTES", "33554432"));
    private static final Duration CONTEXT_TTL = Duration.ofMinutes(5);
    private static final Map<String, SigningContext> SIGNING_CONTEXTS = new ConcurrentHashMap<>();

    private final String platformStore = requiredEnv("USL_DSS_PLATFORM_KEYSTORE");
    private final char[] platformPassword = requiredEnv("USL_DSS_PLATFORM_KEYSTORE_PASSWORD").toCharArray();
    private final String manifestStore = requiredEnv("USL_DSS_MANIFEST_KEYSTORE");
    private final char[] manifestPassword = requiredEnv("USL_DSS_MANIFEST_KEYSTORE_PASSWORD").toCharArray();
    private final CertificateSource localTrust;
    private volatile TrustedListsCertificateSource qualifiedTrust;
    private volatile Instant qualifiedTrustRefreshedAt;
    private volatile String qualifiedTrustError;
    private final ScheduledExecutorService trustRefresh = Executors.newSingleThreadScheduledExecutor(runnable -> {
        Thread thread = new Thread(runnable, "usl-dss-qualified-trust-refresh");
        thread.setDaemon(true);
        return thread;
    });

    private Application() throws Exception {
        enforceSigningKeySeparation();
        localTrust = loadLocalTrust();
        refreshQualifiedTrust();
    }

    private void enforceSigningKeySeparation() throws Exception {
        if (Path.of(platformStore).toAbsolutePath().normalize()
                .equals(Path.of(manifestStore).toAbsolutePath().normalize())) {
            throw new IllegalStateException(
                    "Platform sealing and evidence manifests require separate keystores.");
        }
        try (Pkcs12SignatureToken platformToken = new Pkcs12SignatureToken(
                    platformStore, new PasswordProtection(platformPassword));
             Pkcs12SignatureToken manifestToken = new Pkcs12SignatureToken(
                    manifestStore, new PasswordProtection(manifestPassword))) {
            DSSPrivateKeyEntry platformKey = platformToken.getKeys().stream().findFirst()
                    .orElseThrow(() -> new IllegalStateException(
                            "The platform keystore contains no private key."));
            DSSPrivateKeyEntry manifestKey = manifestToken.getKeys().stream().findFirst()
                    .orElseThrow(() -> new IllegalStateException(
                            "The manifest keystore contains no private key."));
            if (Arrays.equals(
                    platformKey.getCertificate().getEncoded(),
                    manifestKey.getCertificate().getEncoded())) {
                throw new IllegalStateException(
                        "Platform sealing and evidence manifests require different certificates.");
            }
        }
    }

    public static void main(String[] args) throws Exception {
        Application application = new Application();
        int port = Integer.parseInt(System.getenv().getOrDefault("USL_DSS_PORT", "8080"));
        HttpServer server = application.server(port);
        server.createContext("/v1/health", application::health);
        server.createContext("/v1/pades/seal", application::seal);
        server.createContext("/v1/pades/data-to-sign", application::dataToSign);
        server.createContext("/v1/pades/embed", application::embed);
        server.createContext("/v1/pades/validate", application::validate);
        server.createContext("/v1/pades/revision-match", application::revisionMatch);
        server.createContext("/v1/pades/cross-validate", application::crossValidate);
        server.createContext("/v1/manifest/sign", application::signManifest);
        server.createContext("/v1/dossier/build", application::buildDossier);
        server.createContext("/v1/pdfa/validate", application::validatePdfA);
        server.setExecutor(Executors.newVirtualThreadPerTaskExecutor());
        server.start();
        application.trustRefresh.scheduleWithFixedDelay(
                application::refreshQualifiedTrust, 24, 24, TimeUnit.HOURS);
        System.out.println("USL Sign DSS 6.4 listening on " + port);
    }

    private HttpServer server(int port) throws Exception {
        String tlsStore = System.getenv("USL_DSS_TLS_KEYSTORE");
        if (tlsStore == null || tlsStore.isBlank()) {
            if (!Boolean.parseBoolean(System.getenv().getOrDefault("USL_DSS_ALLOW_PLAINTEXT", "false"))) {
                throw new IllegalStateException("Mutual TLS is required unless test-only plaintext mode is explicit.");
            }
            return HttpServer.create(new InetSocketAddress("0.0.0.0", port), 0);
        }
        char[] tlsPassword = requiredEnv("USL_DSS_TLS_KEYSTORE_PASSWORD").toCharArray();
        char[] clientTrustPassword = requiredEnv("USL_DSS_CLIENT_TRUSTSTORE_PASSWORD").toCharArray();
        KeyStore keyStore = keyStore(tlsStore, tlsPassword);
        KeyStore clientTrust = keyStore(requiredEnv("USL_DSS_CLIENT_TRUSTSTORE"), clientTrustPassword);
        KeyManagerFactory keyManagers = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());
        keyManagers.init(keyStore, tlsPassword);
        TrustManagerFactory trustManagers = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        trustManagers.init(clientTrust);
        SSLContext context = SSLContext.getInstance("TLSv1.3");
        context.init(keyManagers.getKeyManagers(), trustManagers.getTrustManagers(), null);
        HttpsServer server = HttpsServer.create(new InetSocketAddress("0.0.0.0", port), 0);
        server.setHttpsConfigurator(new HttpsConfigurator(context) {
            @Override
            public void configure(HttpsParameters parameters) {
                SSLParameters ssl = getSSLContext().getDefaultSSLParameters();
                ssl.setNeedClientAuth(true);
                ssl.setProtocols(new String[] {"TLSv1.3"});
                parameters.setSSLParameters(ssl);
            }
        });
        return server;
    }

    private static KeyStore keyStore(String path, char[] password) throws Exception {
        KeyStore store = KeyStore.getInstance("PKCS12");
        try (InputStream input = Files.newInputStream(new File(path).toPath())) {
            store.load(input, password);
        }
        return store;
    }

    private void health(HttpExchange exchange) throws IOException {
        handle(exchange, payload -> Map.of(
                "engine", "EU DSS",
                "engineVersion", "6.4",
                "qualifiedTrustReady", qualifiedTrustReady(),
                "qualifiedTrustRefreshedAt", qualifiedTrustRefreshedAt == null
                        ? "" : qualifiedTrustRefreshedAt.toString(),
                "qualifiedTrustError", qualifiedTrustError == null ? "" : qualifiedTrustError));
    }

    private void seal(HttpExchange exchange) throws IOException {
        handle(exchange, payload -> {
            byte[] document = document(payload, "document");
            boolean timestamp = Boolean.TRUE.equals(payload.get("timestamp"));
            PAdESSignatureParameters parameters = parameters();
            parameters.setSignatureLevel(timestamp ? SignatureLevel.PAdES_BASELINE_T : SignatureLevel.PAdES_BASELINE_B);
            parameters.setReason("USL platform integrity and evidence seal");
            try (Pkcs12SignatureToken token = new Pkcs12SignatureToken(
                    platformStore, new PasswordProtection(platformPassword))) {
                DSSPrivateKeyEntry privateKey = token.getKeys().stream().findFirst()
                        .orElseThrow(() -> new IllegalStateException("The platform keystore contains no private key."));
                parameters.setSigningCertificate(privateKey.getCertificate());
                parameters.setCertificateChain(privateKey.getCertificateChain());
                PAdESService service = service(timestamp);
                DSSDocument input = pdf(document);
                ToBeSigned toBeSigned = service.getDataToSign(input, parameters);
                SignatureValue value = token.sign(toBeSigned, DigestAlgorithm.SHA256, privateKey);
                DSSDocument result = service.signDocument(input, parameters, value);
                return Map.of("document", b64(bytes(result)), "padesLevel", parameters.getSignatureLevel().name());
            }
        });
    }

    private void dataToSign(HttpExchange exchange) throws IOException {
        handle(exchange, payload -> {
            cleanupContexts();
            byte[] document = document(payload, "document");
            String certificatePem = text(payload, "certificate");
            List<String> certificateChainPem = optionalStringList(
                    payload.get("certificateChain"), 10);
            String reference = text(payload, "requestReference");
            boolean timestamp = Boolean.TRUE.equals(payload.get("timestamp"));
            CertificateToken certificate = certificate(certificatePem);
            PAdESSignatureParameters parameters = parameters();
            parameters.setSignatureLevel(
                    timestamp ? SignatureLevel.PAdES_BASELINE_T : SignatureLevel.PAdES_BASELINE_B);
            parameters.setSigningCertificate(certificate);
            List<CertificateToken> certificateChain = new ArrayList<>();
            certificateChain.add(certificate);
            for (String issuerPem : certificateChainPem) {
                CertificateToken issuer = certificate(issuerPem);
                if (!issuer.equals(certificate) && !certificateChain.contains(issuer)) {
                    certificateChain.add(issuer);
                }
            }
            parameters.setCertificateChain(certificateChain);
            parameters.setReason("Strong personal signature authorized by a document-bound passkey ceremony");
            PAdESService service = service(timestamp);
            ToBeSigned result = service.getDataToSign(pdf(document), parameters);
            String contextId = UUID.randomUUID().toString();
            SIGNING_CONTEXTS.put(contextId, new SigningContext(
                    Instant.now().plus(CONTEXT_TTL), sha256(document), sha256(certificatePem.getBytes(StandardCharsets.UTF_8)),
                    reference, parameters, timestamp));
            return Map.of(
                    "dataToSign", b64(result.getBytes()),
                    "signingContext", contextId,
                    "padesLevel", parameters.getSignatureLevel().name());
        });
    }

    private void embed(HttpExchange exchange) throws IOException {
        handle(exchange, payload -> {
            byte[] document = document(payload, "document");
            String certificatePem = text(payload, "certificate");
            String reference = text(payload, "requestReference");
            String contextId = text(payload, "signingContext");
            SigningContext context = SIGNING_CONTEXTS.remove(contextId);
            if (context == null || context.expiresAt().isBefore(Instant.now())
                    || !context.documentSha256().equals(sha256(document))
                    || !context.certificateSha256().equals(sha256(certificatePem.getBytes(StandardCharsets.UTF_8)))
                    || !context.requestReference().equals(reference)) {
                throw new IllegalArgumentException("The signing context is invalid, expired, or already used.");
            }
            byte[] signature = decode(text(payload, "signature"));
            SignatureValue signatureValue = new SignatureValue(SignatureAlgorithm.ECDSA_SHA256, signature);
            CertificateToken certificate = context.parameters().getSigningCertificate();
            PAdESService service = service(context.timestamp());
            ToBeSigned expected = service.getDataToSign(pdf(document), context.parameters());
            if (!service.isValidSignatureValue(expected, signatureValue, certificate)) {
                throw new IllegalArgumentException("The personal signature value does not match the prepared document.");
            }
            DSSDocument result = service.signDocument(pdf(document), context.parameters(), signatureValue);
            return Map.of(
                    "document", b64(bytes(result)),
                    "padesLevel", context.parameters().getSignatureLevel().name());
        });
    }

    private void validate(HttpExchange exchange) throws IOException {
        handle(exchange, payload -> {
            byte[] document = document(payload, "document");
            String expectedLevel = Objects.toString(payload.get("expectedLevel"), "standard");
            List<String> expectedSigners = optionalStringList(payload.get("expectedSigners"), 50);
            CommonCertificateVerifier verifier = verifier();
            SignedDocumentValidator validator = SignedDocumentValidator.fromDocument(pdf(document));
            validator.setCertificateVerifier(verifier);
            validator.setEnableEtsiValidationReport(true);
            Reports reports = validator.validateDocument();
            SimpleReport simple = reports.getSimpleReport();
            DiagnosticData diagnostic = reports.getDiagnosticData();
            boolean allValid = simple.getSignaturesCount() > 0;
            boolean allEtsiPassed = simple.getSignaturesCount() > 0;
            boolean qualified = false;
            boolean personal = false;
            List<Map<String, Object>> certificates = new ArrayList<>();
            Map<String, SignatureWrapper> diagnosticSignatures = new HashMap<>();
            for (SignatureWrapper signature : diagnostic.getSignatures()) {
                diagnosticSignatures.put(signature.getId(), signature);
            }
            for (String signatureId : simple.getSignatureIdList()) {
                Indication indication = simple.getIndication(signatureId);
                allEtsiPassed &= indication == Indication.TOTAL_PASSED;
                SignatureQualification qualification = simple.getSignatureQualification(signatureId);
                qualified |= qualification == SignatureQualification.QESIG;
                String signedBy = simple.getSignedBy(signatureId);
                SignatureWrapper diagnosticSignature = diagnosticSignatures.get(signatureId);
                String subject = diagnosticSignature != null && diagnosticSignature.getSigningCertificate() != null
                        ? diagnosticSignature.getSigningCertificate().getCertificateDN() : null;
                boolean internalSignature = subject != null && (subject.contains("USL Sign Personal")
                        || subject.contains("USL Sign Platform Seal"));
                boolean localShortLivedPass = internalSignature && diagnosticSignature.isSignatureIntact()
                        && diagnosticSignature.isSignatureValid() && diagnosticSignature.isTrustedChain();
                Map<String, Object> revisionSafety = pdfRevisionSafety(diagnosticSignature);
                boolean safeRevision = Boolean.TRUE.equals(revisionSafety.get("safe"));
                boolean accepted = (indication == Indication.TOTAL_PASSED || localShortLivedPass)
                        && safeRevision;
                allValid &= accepted;
                Map<String, Object> certificate = new LinkedHashMap<>();
                certificate.put("signatureId", signatureId);
                certificate.put("signedBy", signedBy);
                certificate.put("subject", subject);
                certificate.put("qualification", qualification.name());
                certificate.put("format", String.valueOf(simple.getSignatureFormat(signatureId)));
                certificate.put("indication", indication.name());
                certificate.put("acceptedVia", !safeRevision ? "rejected_pdf_revision"
                        : indication == Indication.TOTAL_PASSED
                                ? "etsi_total_passed"
                                : localShortLivedPass ? "trusted_usl_local_policy" : "rejected");
                certificate.put("pdfRevision", revisionSafety);
                certificates.add(certificate);
            }
            for (SignatureWrapper signature : diagnostic.getSignatures()) {
                if (signature.getSigningCertificate() != null) {
                    String subject = signature.getSigningCertificate().getCertificateDN();
                    personal |= subject != null && subject.contains("USL Sign Personal");
                }
            }
            String achieved = qualified ? "qualified_external" : personal ? "strong_personal" : "standard";
            allValid &= switch (expectedLevel) {
                case "standard" -> true;
                case "strong_personal" -> personal;
                case "qualified_external" -> qualified && qualifiedTrustReady()
                        && qualifiedSignersMatch(expectedSigners, certificates);
                default -> false;
            };
            Map<String, Object> reportPayload = new LinkedHashMap<>();
            reportPayload.put("diagnostic", reports.getXmlDiagnosticData());
            reportPayload.put("detailed", reports.getXmlDetailedReport());
            reportPayload.put("simple", reports.getXmlSimpleReport());
            reportPayload.put("etsi", reports.getEtsiValidationReportJaxb() == null ? null : reports.getXmlValidationReport());
            Map<String, Object> response = new LinkedHashMap<>();
            response.put("status", allValid ? "valid" : "invalid");
            response.put("achievedTrust", achieved);
            response.put("signatureCount", simple.getSignaturesCount());
            response.put("engineVersion", "6.4");
            response.put("summary", allValid
                    ? allEtsiPassed ? "Every PDF signature achieved DSS TOTAL_PASSED."
                            : "Every PDF signature is intact and accepted under the configured USL local trust policy; inspect DSS revocation diagnostics."
                    : "DSS did not establish every required signature as valid.");
            response.put("qualifiedProvider", qualified ? certificates.stream().filter(row -> "QESIG".equals(row.get("qualification"))).findFirst().map(row -> row.get("signedBy")).orElse(null) : null);
            response.put("certificates", certificates);
            response.put("timestamps", Map.of("count", diagnostic.getTimestampList().size()));
            response.put("revocation", Map.of(
                    "qualifiedTrustReady", qualifiedTrustReady(),
                    "qualifiedTrustRefreshedAt", qualifiedTrustRefreshedAt == null
                            ? "" : qualifiedTrustRefreshedAt.toString()));
            response.put("reports", reportPayload);
            return response;
        });
    }

    private void revisionMatch(HttpExchange exchange) throws IOException {
        handle(exchange, payload -> {
            byte[] frozen = document(payload, "frozenDocument");
            byte[] signed = document(payload, "signedDocument");
            PDFDocumentValidator validator = new PDFDocumentValidator(pdf(signed));
            List<byte[]> precedingSignatureRevisions = new ArrayList<>();
            for (PdfRevision revision : validator.getRevisions()) {
                if (revision instanceof PdfSignatureRevision signatureRevision) {
                    precedingSignatureRevisions.add(bytes(signatureRevision.getPreviousRevision()));
                }
            }
            if (precedingSignatureRevisions.isEmpty()) {
                throw new IllegalArgumentException("The imported PDF contains no reconstructable signature revision.");
            }
            byte[] firstPreSignatureRevision = precedingSignatureRevisions.stream()
                    .min((left, right) -> Integer.compare(left.length, right.length))
                    .orElseThrow();
            boolean matches = Arrays.equals(frozen, firstPreSignatureRevision);
            Map<String, Object> response = new LinkedHashMap<>();
            response.put("matches", matches);
            response.put("method", "dss_first_signature_previous_revision_exact_bytes");
            response.put("signatureRevisionCount", precedingSignatureRevisions.size());
            response.put("frozenSha256", sha256(frozen));
            response.put("reconstructedSha256", sha256(firstPreSignatureRevision));
            response.put("signedSha256", sha256(signed));
            return response;
        });
    }

    private static Map<String, Object> pdfRevisionSafety(SignatureWrapper signature) {
        Map<String, Object> result = new LinkedHashMap<>();
        PDFRevisionWrapper revision = signature == null ? null : signature.getPDFRevision();
        boolean byteRangeValid = revision != null && revision.isSignatureByteRangeValid();
        boolean dictionaryConsistent = revision != null && revision.isPdfSignatureDictionaryConsistent();
        boolean noPageChanges = revision != null && revision.getPdfPageDifferenceConcernedPages().isEmpty();
        boolean noAnnotationChanges = revision != null
                && revision.getPdfAnnotationsOverlapConcernedPages().isEmpty()
                && revision.getPdfAnnotationChanges().isEmpty();
        boolean noUndefinedChanges = revision != null && revision.getPdfUndefinedChanges().isEmpty();
        boolean visualChanges = revision != null && !revision.getPdfVisualDifferenceConcernedPages().isEmpty();
        boolean expectedSignatureOrExtensionChanges = revision != null
                && (!revision.getPdfSignatureOrFormFillChanges().isEmpty()
                        || !revision.getPdfExtensionChanges().isEmpty());
        boolean visualSafety = !visualChanges || expectedSignatureOrExtensionChanges;
        boolean safe = byteRangeValid && dictionaryConsistent && noPageChanges
                && noAnnotationChanges && noUndefinedChanges && visualSafety;
        result.put("safe", safe);
        result.put("byteRangeValid", byteRangeValid);
        result.put("signatureDictionaryConsistent", dictionaryConsistent);
        result.put("pageChanges", !noPageChanges);
        result.put("annotationChanges", !noAnnotationChanges);
        result.put("undefinedChanges", !noUndefinedChanges);
        result.put("visualChanges", visualChanges);
        result.put("expectedSignatureOrExtensionChanges", expectedSignatureOrExtensionChanges);
        return result;
    }

    private static boolean qualifiedSignersMatch(
            List<String> expectedSigners, List<Map<String, Object>> certificates) {
        if (expectedSigners.isEmpty()) {
            return false;
        }
        List<Map<String, Object>> available = new ArrayList<>(certificates.stream()
                .filter(row -> "QESIG".equals(row.get("qualification")))
                .toList());
        for (String expectedSigner : expectedSigners) {
            int match = -1;
            for (int index = 0; index < available.size(); index++) {
                Map<String, Object> certificate = available.get(index);
                String identity = Objects.toString(certificate.get("signedBy"), "") + " "
                        + Objects.toString(certificate.get("subject"), "");
                if (identityMatches(expectedSigner, identity)) {
                    match = index;
                    break;
                }
            }
            if (match < 0) {
                return false;
            }
            available.remove(match);
        }
        return true;
    }

    static boolean identityMatches(String expected, String certificateIdentity) {
        String normalizedExpected = normalizeIdentity(expected);
        String normalizedCertificate = normalizeIdentity(certificateIdentity);
        if (normalizedExpected.isBlank() || normalizedCertificate.isBlank()) {
            return false;
        }
        String certificateTokens = " " + normalizedCertificate + " ";
        for (String token : normalizedExpected.split(" ")) {
            if (token.length() > 1 && !certificateTokens.contains(" " + token + " ")) {
                return false;
            }
        }
        return true;
    }

    private static String normalizeIdentity(String value) {
        return Normalizer.normalize(Objects.toString(value, ""), Normalizer.Form.NFKD)
                .replaceAll("\\p{M}", "")
                .toLowerCase(Locale.ROOT)
                .replaceAll("[^\\p{Alnum}]+", " ")
                .strip()
                .replaceAll("\\s+", " ");
    }

    private void signManifest(HttpExchange exchange) throws IOException {
        handle(exchange, payload -> {
            byte[] manifest = document(payload, "manifest");
            try (Pkcs12SignatureToken token = new Pkcs12SignatureToken(
                    manifestStore, new PasswordProtection(manifestPassword))) {
                DSSPrivateKeyEntry privateKey = token.getKeys().stream().findFirst()
                        .orElseThrow(() -> new IllegalStateException("The manifest keystore contains no private key."));
                SignatureValue signature = token.sign(new ToBeSigned(manifest), DigestAlgorithm.SHA256, privateKey);
                List<String> chain = Arrays.stream(privateKey.getCertificateChain())
                        .map(certificate -> b64(certificate.getEncoded())).toList();
                return Map.of(
                        "manifestSha256", sha256(manifest),
                        "signature", b64(signature.getValue()),
                        "signatureAlgorithm", signature.getAlgorithm().name(),
                        "certificateChain", chain);
            }
        });
    }

    private void buildDossier(HttpExchange exchange) throws IOException {
        handle(exchange, payload -> {
            String title = text(payload, "title");
            List<String> lines = stringList(payload.get("summary"), 100);
            List<Artifact> artifacts = artifacts(payload.get("artifacts"));
            byte[] dossier = pdfaDossier(title, lines, artifacts);
            return Map.of(
                    "document", b64(dossier),
                    "pdfaLevel", "PDF/A-3b",
                    "sha256", sha256(dossier),
                    "artifactCount", artifacts.size());
        });
    }

    private void validatePdfA(HttpExchange exchange) throws IOException {
        handle(exchange, payload -> validatePdfABytes(document(payload, "document")));
    }

    private void crossValidate(HttpExchange exchange) throws IOException {
        handle(exchange, payload -> crossValidateBytes(document(payload, "document")));
    }

    private byte[] pdfaDossier(String title, List<String> lines, List<Artifact> artifacts) throws Exception {
        Calendar fixedDate = GregorianCalendar.from(
                ZonedDateTime.ofInstant(Instant.parse("2000-01-01T00:00:00Z"), ZoneOffset.UTC));
        try (PDDocument document = new PDDocument();
             InputStream fontInput = Files.newInputStream(
                     Path.of(requiredEnv("USL_DSS_PDFA_FONT")))) {
            document.setVersion(1.7f);
            PDDocumentCatalog catalog = document.getDocumentCatalog();
            PDDocumentInformation information = document.getDocumentInformation();
            information.setTitle(title);
            information.setSubject("Source, signed document, certificates, validation reports, and canonical evidence manifest");
            information.setAuthor("USL Sign");
            information.setCreator("USL Sign DSS 6.4");
            information.setProducer("Apache PDFBox 3 / USL Sign");
            information.setCreationDate(fixedDate);
            information.setModificationDate(fixedDate);

            XMPMetadata xmp = XMPMetadata.createXMPMetadata();
            PDFAIdentificationSchema identification = xmp.createAndAddPDFAIdentificationSchema();
            identification.setPart(3);
            identification.setConformance("B");
            DublinCoreSchema dublinCore = xmp.createAndAddDublinCoreSchema();
            dublinCore.setTitle(title);
            dublinCore.addCreator("USL Sign");
            AdobePDFSchema adobe = xmp.createAndAddAdobePDFSchema();
            adobe.setProducer("Apache PDFBox 3 / USL Sign");
            XMPBasicSchema basic = xmp.createAndAddXMPBasicSchema();
            basic.setCreatorTool("USL Sign DSS 6.4");
            basic.setCreateDate(fixedDate);
            basic.setModifyDate(fixedDate);
            ByteArrayOutputStream xmpBytes = new ByteArrayOutputStream();
            new XmpSerializer().serialize(xmp, xmpBytes, true);
            PDMetadata metadata = new PDMetadata(document);
            metadata.importXMPMetadata(xmpBytes.toByteArray());
            catalog.setMetadata(metadata);

            ICC_Profile profile = ICC_Profile.getInstance(ColorSpace.CS_sRGB);
            PDOutputIntent outputIntent = new PDOutputIntent(
                    document, new ByteArrayInputStream(profile.getData()));
            outputIntent.setInfo("sRGB IEC61966-2.1");
            outputIntent.setOutputCondition("sRGB IEC61966-2.1");
            outputIntent.setOutputConditionIdentifier("sRGB IEC61966-2.1");
            outputIntent.setRegistryName("https://www.color.org");
            catalog.addOutputIntent(outputIntent);

            PDType0Font font = PDType0Font.load(document, fontInput, true);
            List<String> coverLines = new ArrayList<>();
            coverLines.add("USL Sign evidence dossier");
            coverLines.add(title);
            coverLines.add("");
            coverLines.addAll(lines);
            coverLines.add("");
            coverLines.add("Embedded evidence artifacts: " + artifacts.size());
            addCoverPages(document, font, coverLines);

            Map<String, PDComplexFileSpecification> embeddedFiles = new LinkedHashMap<>();
            COSArray associatedFiles = new COSArray();
            for (Artifact artifact : artifacts) {
                PDEmbeddedFile embedded = new PDEmbeddedFile(
                        document, new ByteArrayInputStream(artifact.content()));
                embedded.setSubtype(artifact.mimeType());
                embedded.setSize(artifact.content().length);
                embedded.setCreationDate(fixedDate);
                embedded.setModDate(fixedDate);
                PDComplexFileSpecification specification = new PDComplexFileSpecification();
                specification.setFile(artifact.name());
                specification.setFileUnicode(artifact.name());
                specification.setFileDescription(artifact.description());
                specification.setEmbeddedFile(embedded);
                specification.getCOSObject().setName(
                        COSName.AF_RELATIONSHIP, artifact.relationship());
                embeddedFiles.put(artifact.name(), specification);
                associatedFiles.add(specification.getCOSObject());
            }
            PDEmbeddedFilesNameTreeNode tree = new PDEmbeddedFilesNameTreeNode();
            tree.setNames(embeddedFiles);
            PDDocumentNameDictionary names = new PDDocumentNameDictionary(catalog);
            names.setEmbeddedFiles(tree);
            catalog.setNames(names);
            catalog.getCOSObject().setItem(COSName.AF, associatedFiles);

            byte[] deterministicId = java.security.MessageDigest.getInstance("SHA-256")
                    .digest(artifacts.stream()
                            .map(artifact -> artifact.name() + ":" + sha256(artifact.content()))
                            .reduce(title, (left, right) -> left + "\n" + right)
                            .getBytes(StandardCharsets.UTF_8));
            COSArray identifiers = new COSArray();
            identifiers.add(new COSString(Arrays.copyOf(deterministicId, 16)));
            identifiers.add(new COSString(Arrays.copyOf(deterministicId, 16)));
            document.getDocument().getTrailer().setItem(COSName.ID, identifiers);

            ByteArrayOutputStream output = new ByteArrayOutputStream();
            document.save(output);
            return output.toByteArray();
        }
    }

    private static void addCoverPages(PDDocument document, PDType0Font font, List<String> lines)
            throws IOException {
        PDPage page = null;
        PDPageContentStream content = null;
        float y = 0;
        try {
            for (int index = 0; index < lines.size(); index++) {
                if (page == null || y < 72) {
                    if (content != null) {
                        content.close();
                    }
                    page = new PDPage(PDRectangle.A4);
                    document.addPage(page);
                    content = new PDPageContentStream(document, page);
                    y = page.getMediaBox().getHeight() - 64;
                }
                String line = lines.get(index);
                float size = index == 0 ? 18 : index == 1 ? 13 : 9;
                content.beginText();
                content.setFont(font, size);
                content.newLineAtOffset(54, y);
                content.showText(pdfText(line, font));
                content.endText();
                y -= index < 2 ? 26 : 15;
            }
        } finally {
            if (content != null) {
                content.close();
            }
        }
    }

    private static String pdfText(String value, PDType0Font font) throws IOException {
        String normalized = value == null ? "" : value.replaceAll("[\\r\\n\\t]+", " ").strip();
        StringBuilder result = new StringBuilder();
        for (int codePoint : normalized.codePoints().limit(180).toArray()) {
            result.appendCodePoint(font.hasGlyph(codePoint) ? codePoint : '?');
        }
        return result.toString();
    }

    private Map<String, Object> validatePdfABytes(byte[] document) throws Exception {
        pdf(document);
        Path directory = Files.createTempDirectory("usl-sign-pdfa-");
        Path input = directory.resolve("dossier.pdf");
        Path report = directory.resolve("report.json");
        try {
            Files.write(input, document);
            Process process = new ProcessBuilder(
                    requiredEnv("USL_DSS_VERAPDF"),
                    "--format", "json",
                    "--flavour", "3b",
                    "--loglevel", "0",
                    input.toString())
                    .redirectErrorStream(true)
                    .redirectOutput(report.toFile())
                    .start();
            if (!process.waitFor(60, TimeUnit.SECONDS)) {
                process.destroyForcibly();
                throw new IllegalStateException("veraPDF exceeded its execution limit.");
            }
            if (!Files.exists(report) || Files.size(report) > 2_000_000) {
                throw new IllegalStateException("veraPDF produced no bounded validation report.");
            }
            String raw = Files.readString(report, StandardCharsets.UTF_8)
                    .replace(input.toString(), "dossier.pdf");
            JsonNode reportJson = JSON.readTree(raw);
            boolean compliant = findCompliance(reportJson);
            Map<String, Object> response = new LinkedHashMap<>();
            response.put("compliant", compliant);
            response.put("engine", "veraPDF");
            response.put("engineVersion", "1.30.2");
            response.put("profile", "PDF/A-3b");
            response.put("report", reportJson);
            response.put("sha256", sha256(document));
            if (process.exitValue() != 0 && compliant) {
                throw new IllegalStateException("veraPDF exited unexpectedly.");
            }
            return response;
        } finally {
            Files.deleteIfExists(report);
            Files.deleteIfExists(input);
            Files.deleteIfExists(directory);
        }
    }

    private Map<String, Object> crossValidateBytes(byte[] document) throws Exception {
        pdf(document);
        Path directory = Files.createTempDirectory("usl-sign-pyhanko-");
        Path input = directory.resolve("document.pdf");
        Path report = directory.resolve("report.json");
        Path errors = directory.resolve("errors.log");
        try {
            Files.write(input, document);
            Process process = new ProcessBuilder(
                    requiredEnv("USL_DSS_PYHANKO_PYTHON"),
                    requiredEnv("USL_DSS_PYHANKO_SCRIPT"),
                    input.toString())
                    .redirectOutput(report.toFile())
                    .redirectError(errors.toFile())
                    .start();
            if (!process.waitFor(30, TimeUnit.SECONDS)) {
                process.destroyForcibly();
                throw new IllegalStateException("pyHanko exceeded its execution limit.");
            }
            if (process.exitValue() != 0 || !Files.exists(report)
                    || Files.size(report) == 0 || Files.size(report) > 2_000_000
                    || (Files.exists(errors) && Files.size(errors) > 200_000)) {
                throw new IllegalStateException("pyHanko produced no bounded validation report.");
            }
            return JSON.readValue(report.toFile(), new TypeReference<>() {});
        } finally {
            Files.deleteIfExists(errors);
            Files.deleteIfExists(report);
            Files.deleteIfExists(input);
            Files.deleteIfExists(directory);
        }
    }

    private static boolean findCompliance(JsonNode node) {
        if (node.isObject()) {
            if (node.has("isCompliant") && node.get("isCompliant").isBoolean()) {
                return node.get("isCompliant").asBoolean();
            }
            if (node.has("compliant") && node.get("compliant").isBoolean()) {
                return node.get("compliant").asBoolean();
            }
            for (JsonNode child : node) {
                if (findCompliance(child)) {
                    return true;
                }
            }
        } else if (node.isArray()) {
            for (JsonNode child : node) {
                if (findCompliance(child)) {
                    return true;
                }
            }
        }
        return false;
    }

    private static List<String> stringList(Object value, int maximum) {
        if (!(value instanceof List<?> values) || values.size() > maximum) {
            throw new IllegalArgumentException("summary must be a bounded list of strings.");
        }
        List<String> result = new ArrayList<>();
        for (Object item : values) {
            if (!(item instanceof String text)) {
                throw new IllegalArgumentException("summary entries must be strings.");
            }
            result.add(text);
        }
        return result;
    }

    private static List<String> optionalStringList(Object value, int maximum) {
        if (value == null) {
            return List.of();
        }
        return stringList(value, maximum);
    }

    private static List<Artifact> artifacts(Object value) {
        if (!(value instanceof List<?> values) || values.isEmpty() || values.size() > 200) {
            throw new IllegalArgumentException("artifacts must be a non-empty bounded list.");
        }
        List<Artifact> result = new ArrayList<>();
        HashSet<String> names = new HashSet<>();
        HashSet<String> relationships = new HashSet<>(
                List.of("Source", "Data", "Supplement", "Alternative"));
        for (Object item : values) {
            if (!(item instanceof Map<?, ?> row)) {
                throw new IllegalArgumentException("Each artifact must be an object.");
            }
            String name = Objects.toString(row.get("name"), "").strip();
            String mimeType = Objects.toString(row.get("mimeType"), "application/octet-stream").strip();
            String relationship = Objects.toString(row.get("relationship"), "Supplement").strip();
            String description = Objects.toString(row.get("description"), "Evidence artifact").strip();
            if (name.isBlank() || name.length() > 180 || name.contains("/") || name.contains("\\")
                    || !names.add(name) || !relationships.contains(relationship)) {
                throw new IllegalArgumentException("Artifact names and relationships must be safe and unique.");
            }
            Object encoded = row.get("content");
            if (!(encoded instanceof String text) || text.isBlank()) {
                throw new IllegalArgumentException("Every artifact requires Base64 content.");
            }
            result.add(new Artifact(name, decode(text), mimeType, relationship, description));
        }
        result.sort((left, right) -> left.name().compareTo(right.name()));
        return result;
    }

    private CommonCertificateVerifier verifier() {
        CommonCertificateVerifier verifier = new CommonCertificateVerifier();
        TrustedListsCertificateSource currentQualifiedTrust = qualifiedTrustReady()
                ? qualifiedTrust : null;
        if (currentQualifiedTrust == null) {
            verifier.setTrustedCertSources(localTrust);
        } else {
            verifier.setTrustedCertSources(localTrust, currentQualifiedTrust);
        }
        return verifier;
    }

    private PAdESService service(boolean timestamp) {
        PAdESService service = new PAdESService(verifier());
        if (timestamp) {
            String tsaUrl = requiredEnv("USL_DSS_TSA_URL");
            service.setTspSource(new OnlineTSPSource(tsaUrl));
        }
        return service;
    }

    private static PAdESSignatureParameters parameters() {
        PAdESSignatureParameters parameters = new PAdESSignatureParameters();
        parameters.setSignatureLevel(SignatureLevel.PAdES_BASELINE_B);
        parameters.setDigestAlgorithm(DigestAlgorithm.SHA256);
        return parameters;
    }

    private CertificateSource loadLocalTrust() throws Exception {
        CommonTrustedCertificateSource trust = new CommonTrustedCertificateSource();
        try (Pkcs12SignatureToken token = new Pkcs12SignatureToken(
                platformStore, new PasswordProtection(platformPassword))) {
            for (DSSPrivateKeyEntry key : token.getKeys()) {
                CertificateToken[] chain = key.getCertificateChain();
                if (chain.length < 2) {
                    throw new IllegalStateException(
                            "The platform seal certificate must be issued by a separate local CA.");
                }
                trust.addCertificate(chain[chain.length - 1]);
            }
        }
        String trustStore = System.getenv("USL_DSS_LOCAL_TRUSTSTORE");
        if (trustStore != null && !trustStore.isBlank()) {
            char[] password = requiredEnv("USL_DSS_LOCAL_TRUSTSTORE_PASSWORD").toCharArray();
            KeyStoreCertificateSource source = new KeyStoreCertificateSource(trustStore, "PKCS12", password);
            source.getCertificates().forEach(trust::addCertificate);
        }
        return trust;
    }

    private TrustedListsCertificateSource loadQualifiedTrust() throws Exception {
        String keyStore = System.getenv("USL_DSS_LOTL_KEYSTORE");
        if (keyStore == null || keyStore.isBlank()) {
            return null;
        }
        char[] password = requiredEnv("USL_DSS_LOTL_KEYSTORE_PASSWORD").toCharArray();
        String lotlUrl = requiredEnv("USL_DSS_LOTL_URL");
        String ojUrl = requiredEnv("USL_DSS_OJ_URL");
        File cacheDirectory = new File(System.getenv().getOrDefault("USL_DSS_TSL_CACHE", "/tmp/usl-dss-tsl"));
        Files.createDirectories(cacheDirectory.toPath());
        FileCacheDataLoader online = new FileCacheDataLoader();
        online.setFileCacheDirectory(cacheDirectory);
        online.setCacheExpirationTime(-1);
        LOTLSource source = new LOTLSource();
        source.setUrl(lotlUrl);
        source.setCertificateSource(new KeyStoreCertificateSource(keyStore, "PKCS12", password));
        source.setSigningCertificatesAnnouncementPredicate(new OfficialJournalSchemeInformationURI(ojUrl));
        source.setPivotSupport(true);
        source.setTLVersions(List.of(5, 6));
        ExpirationAndSignatureCheckStrategy strict = new ExpirationAndSignatureCheckStrategy();
        strict.setAcceptExpiredListOfTrustedLists(false);
        strict.setAcceptExpiredTrustedList(false);
        strict.setAcceptInvalidListOfTrustedLists(false);
        strict.setAcceptInvalidTrustedList(false);
        TrustedListsCertificateSource trust = new TrustedListsCertificateSource();
        TLValidationJob job = new TLValidationJob();
        job.setOnlineDataLoader(Sha2FileCacheDataLoader.initSha2DailyUpdateDataLoader(online));
        job.setOfflineDataLoader(online);
        job.setTrustedListCertificateSource(trust);
        job.setSynchronizationStrategy(strict);
        job.setListOfTrustedListSources(source);
        job.onlineRefresh();
        if (trust.getCertificates().isEmpty()) {
            throw new IllegalStateException("No qualified trust anchors were accepted from the EU LOTL.");
        }
        return trust;
    }

    private boolean qualifiedTrustReady() {
        return qualifiedTrust != null
                && qualifiedTrustRefreshedAt != null
                && qualifiedTrustRefreshedAt.isAfter(Instant.now().minus(Duration.ofHours(36)));
    }

    private synchronized void refreshQualifiedTrust() {
        try {
            TrustedListsCertificateSource refreshed = loadQualifiedTrust();
            qualifiedTrust = refreshed;
            qualifiedTrustRefreshedAt = refreshed == null ? null : Instant.now();
            qualifiedTrustError = null;
        } catch (Exception exception) {
            qualifiedTrust = null;
            qualifiedTrustRefreshedAt = null;
            qualifiedTrustError = exception.getClass().getSimpleName();
            System.err.println("Qualified trust refresh failed: " + qualifiedTrustError);
        }
    }

    private static CertificateToken certificate(String pem) throws Exception {
        CertificateFactory factory = CertificateFactory.getInstance("X.509");
        X509Certificate certificate = (X509Certificate) factory.generateCertificate(
                new ByteArrayInputStream(pem.getBytes(StandardCharsets.US_ASCII)));
        return new CertificateToken(certificate);
    }

    private static InMemoryDocument pdf(byte[] bytes) {
        if (bytes.length < 8 || !new String(bytes, 0, 5, StandardCharsets.US_ASCII).equals("%PDF-")) {
            throw new IllegalArgumentException("Only readable PDF input is accepted.");
        }
        return new InMemoryDocument(bytes, "document.pdf");
    }

    private static byte[] bytes(DSSDocument document) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        document.writeTo(output);
        return output.toByteArray();
    }

    private static void handle(HttpExchange exchange, Operation operation) throws IOException {
        try {
            if (!"POST".equals(exchange.getRequestMethod())) {
                send(exchange, 405, Map.of("ok", false, "error", "POST is required."));
                return;
            }
            byte[] body = exchange.getRequestBody().readNBytes(MAX_REQUEST_BYTES + 1);
            if (body.length > MAX_REQUEST_BYTES) {
                send(exchange, 413, Map.of("ok", false, "error", "The request exceeds the configured payload limit."));
                return;
            }
            Map<String, Object> payload = body.length == 0 ? Map.of() : JSON.readValue(body, new TypeReference<>() {});
            Map<String, Object> response = new LinkedHashMap<>();
            response.put("ok", true);
            response.putAll(operation.run(payload));
            send(exchange, 200, response);
        } catch (IllegalArgumentException exception) {
            send(exchange, 422, Map.of("ok", false, "error", exception.getMessage()));
        } catch (Exception exception) {
            System.err.println("DSS operation failed: " + exception.getClass().getSimpleName());
            send(exchange, 503, Map.of("ok", false, "error", "The DSS operation could not be completed."));
        } finally {
            exchange.close();
        }
    }

    private static void send(HttpExchange exchange, int status, Map<String, Object> body) throws IOException {
        byte[] encoded = JSON.writeValueAsBytes(body);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.getResponseHeaders().set("Cache-Control", "no-store");
        exchange.getResponseHeaders().set("X-Content-Type-Options", "nosniff");
        exchange.sendResponseHeaders(status, encoded.length);
        exchange.getResponseBody().write(encoded);
    }

    private static byte[] document(Map<String, Object> payload, String name) {
        return decode(text(payload, name));
    }

    private static String text(Map<String, Object> payload, String name) {
        Object value = payload.get(name);
        if (!(value instanceof String text) || text.isBlank()) {
            throw new IllegalArgumentException(name + " is required.");
        }
        return text;
    }

    private static byte[] decode(String value) {
        try {
            return Base64.getDecoder().decode(value);
        } catch (IllegalArgumentException exception) {
            throw new IllegalArgumentException("A binary value is not valid Base64.");
        }
    }

    private static String b64(byte[] value) {
        return Base64.getEncoder().encodeToString(value);
    }

    private static String sha256(byte[] value) {
        try {
            return java.util.HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest(value));
        } catch (Exception exception) {
            throw new IllegalStateException(exception);
        }
    }

    private static String requiredEnv(String name) {
        String value = System.getenv(name);
        if (value == null || value.isBlank()) {
            throw new IllegalStateException(name + " is required.");
        }
        return value;
    }

    private static void cleanupContexts() {
        Instant now = Instant.now();
        SIGNING_CONTEXTS.entrySet().removeIf(entry -> entry.getValue().expiresAt().isBefore(now));
    }

    @FunctionalInterface
    private interface Operation {
        Map<String, Object> run(Map<String, Object> payload) throws Exception;
    }

    private record SigningContext(
            Instant expiresAt,
            String documentSha256,
            String certificateSha256,
            String requestReference,
            PAdESSignatureParameters parameters,
            boolean timestamp) {}

    private record Artifact(
            String name,
            byte[] content,
            String mimeType,
            String relationship,
            String description) {}
}
