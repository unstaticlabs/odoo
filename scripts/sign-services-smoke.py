#!/usr/bin/env python3
"""End-to-end smoke test for the local USL Sign trust services."""

import base64
import importlib.util
import json
import os
from datetime import timezone
from io import BytesIO

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from reportlab.pdfgen import canvas

def load_service(name):
    path = os.path.join(
        os.environ.get("USL_SIGN_ADDONS_PATH", "/mnt/custom-addons"),
        "usl_sign",
        "services",
        f"{name}.py",
    )
    spec = importlib.util.spec_from_file_location(f"usl_sign_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dss_module = load_service("dss")
step_ca_module = load_service("step_ca")
DSSClient = dss_module.DSSClient
StepCAClient = step_ca_module.StepCAClient


def csr_and_key(common_name="USL Sign Personal: Integration Test"):
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .sign(key, hashes.SHA256())
    )
    return key, csr.public_bytes(serialization.Encoding.PEM).decode()


def minimal_pdf(label="USL Sign service integration test"):
    output = BytesIO()
    document = canvas.Canvas(output)
    document.drawString(72, 760, label)
    document.save()
    return output.getvalue()


def certificate_binding(key, document_sha256="1" * 64):
    public_key_sha256 = __import__("hashlib").sha256(
        key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    ).hexdigest()
    return {
        "usl_signer": "101",
        "usl_enrollment": "202",
        "usl_request": "303",
        "usl_role": "404",
        "usl_document_sha256": document_sha256,
        "usl_policy_sha256": "2" * 64,
        "usl_public_key_sha256": public_key_sha256,
    }


def main():
    dss = DSSClient()
    health = dss.health()
    assert health["engineVersion"] == "6.4"

    manifest = b'{"format":"usl-sign-smoke-manifest-v1"}'
    manifest_signature = dss.sign_manifest(manifest)
    manifest_certificate = x509.load_der_x509_certificate(
        base64.b64decode(manifest_signature["certificateChain"][0])
    )
    assert "USL Sign Evidence Manifest" in manifest_certificate.subject.rfc4514_string()
    manifest_certificate.public_key().verify(
        base64.b64decode(manifest_signature["signature"]),
        manifest,
        ec.ECDSA(hashes.SHA256()),
    )
    assert manifest_signature["manifestSha256"] == __import__("hashlib").sha256(
        manifest
    ).hexdigest()

    key, csr_pem = csr_and_key()
    ca = StepCAClient()
    binding = certificate_binding(key)
    sans = {
        "urn:usl:signer:101",
        "urn:usl:enrollment:202",
        "urn:usl:request:303",
        "urn:usl:role:404",
        f"urn:sha256:{binding['usl_document_sha256']}",
        f"urn:usl:policy-sha256:{binding['usl_policy_sha256']}",
        f"urn:usl:public-key-sha256:{binding['usl_public_key_sha256']}",
    }
    issued = ca.issue(
        csr_pem,
        subject="USL Sign Personal: Integration Test",
        binding=binding,
    )
    certificate = x509.load_pem_x509_certificate(issued["certificate"].encode())
    assert certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == (
        "USL Sign Personal: Integration Test"
    )
    assert certificate.public_key().public_numbers() == key.public_key().public_numbers()
    lifetime = certificate.not_valid_after_utc - certificate.not_valid_before_utc
    assert lifetime.total_seconds() <= 630
    assert certificate.not_valid_after_utc > __import__("datetime").datetime.now(timezone.utc)
    assert sans == set(
        certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.UniformResourceIdentifier)
    )
    assert certificate.extensions.get_extension_for_class(x509.KeyUsage).value.digital_signature

    replay_key, replay_csr = csr_and_key("USL Sign Personal: Replay Test")
    one_time_token, _receipt = ca._token(
        replay_csr,
        "USL Sign Personal: Replay Test",
        certificate_binding(replay_key, "3" * 64),
    )
    payload = {
        "csr": replay_csr,
        "ott": one_time_token,
        "notBefore": "-30s",
        "notAfter": "10m",
    }
    first = requests.post(
        f"{ca.base_url}/1.0/sign", json=payload, timeout=ca.timeout, verify=ca.ca_bundle
    )
    first.raise_for_status()
    replay = requests.post(
        f"{ca.base_url}/1.0/sign", json=payload, timeout=ca.timeout, verify=ca.ca_bundle
    )
    assert replay.status_code >= 400, "step-ca accepted a reused one-time token"

    source = minimal_pdf()
    personal_prepared = dss.data_to_sign(
        source,
        issued["certificate"],
        certificate_chain=issued["chain"],
        request_reference="USL-SIGN-PERSONAL-SMOKE",
    )
    personal_signature = key.sign(
        base64.b64decode(personal_prepared["dataToSign"]),
        ec.ECDSA(hashes.SHA256()),
    )
    personal_embedded = dss.embed_signature(
        source,
        issued["certificate"],
        personal_signature,
        request_reference="USL-SIGN-PERSONAL-SMOKE",
        signing_context=personal_prepared["signingContext"],
    )
    personal_pdf = base64.b64decode(personal_embedded["document"])
    personal_validation = dss.validate(
        personal_pdf,
        expected_level="strong_personal",
    )
    assert personal_validation["status"] == "valid", json.dumps(
        personal_validation, indent=2
    )[:12000]
    assert personal_validation["achievedTrust"] == "strong_personal"
    personal_cross_validation = dss.cross_validate(personal_pdf)
    assert personal_cross_validation["status"] == "valid"

    sealed = dss.seal(source, request_reference="USL-SIGN-SMOKE")
    signed = base64.b64decode(sealed["document"])
    validation = dss.validate(signed, expected_level="standard")
    assert validation["status"] == "valid"
    assert validation["achievedTrust"] == "standard"
    cross_validation = dss.cross_validate(signed)
    assert cross_validation["status"] == "valid"
    assert cross_validation["signature_count"] == validation["signatureCount"]
    revision = dss.revision_matches(source, signed)
    assert revision["matches"]
    assert revision["method"] == "dss_first_signature_previous_revision_exact_bytes"
    different_source = minimal_pdf("USL Sign intentionally different source")
    assert different_source != source
    assert not dss.revision_matches(different_source, signed)["matches"]
    altered = signed[:-1] + bytes([signed[-1] ^ 1])
    assert dss.validate(altered, expected_level="standard")["status"] == "invalid"

    dossier_arguments = {
        "title": "USL Sign deterministic evidence dossier",
        "summary": ["Requested trust: standard", "Validation authority: EU DSS 6.4"],
        "artifacts": [
            {
                "name": "source.pdf",
                "content": source,
                "mimetype": "application/pdf",
                "relationship": "Source",
                "description": "Frozen source",
            },
            {
                "name": "validation.json",
                "content": b'{"status":"valid"}',
                "mimetype": "application/json",
                "relationship": "Supplement",
                "description": "DSS validation result",
            },
        ],
    }
    dossier_one = base64.b64decode(dss.build_dossier(**dossier_arguments)["document"])
    dossier_two = base64.b64decode(dss.build_dossier(**dossier_arguments)["document"])
    assert dossier_one == dossier_two, "PDF/A-3 dossier construction is not deterministic"
    dossier_validation = dss.validate_pdfa(dossier_one)
    assert dossier_validation["compliant"], json.dumps(
        dossier_validation["report"], indent=2
    )[:12000]
    sealed_dossier = base64.b64decode(
        dss.seal(dossier_one, request_reference="USL-SIGN-DOSSIER-SMOKE")["document"]
    )
    sealed_dossier_validation = dss.validate_pdfa(sealed_dossier)
    assert sealed_dossier_validation["compliant"], json.dumps(
        sealed_dossier_validation["report"], indent=2
    )[:12000]

    print("USL Sign CA, DSS, separate manifest signing, pyHanko, deterministic PDF/A-3 dossier, veraPDF, sealing, replay, and alteration checks passed.")


if __name__ == "__main__":
    main()
