"""Bounded offline cross-validation invoked only by the mTLS DSS service."""

import base64
import json
import sys

from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.validation import validate_pdf_signature
from pyhanko_certvalidator import ValidationContext


def main():
    if len(sys.argv) != 2:
        msg = "A PDF path is required."
        raise SystemExit(msg)
    with open(sys.argv[1], "rb") as stream:
        reader = PdfFileReader(stream, strict=True)
        signatures = list(reader.embedded_regular_signatures)
        context = ValidationContext(allow_fetching=False)
        rows = []
        for signature in signatures:
            status = validate_pdf_signature(
                signature,
                signer_validation_context=context,
                ts_validation_context=context,
            )
            embedded_certificates = [
                signature.signer_cert,
                *signature.other_embedded_certs,
            ]
            rows.append(
                {
                    "field_name": signature.field_name,
                    "intact": bool(status.intact),
                    "cryptographically_valid": bool(status.valid),
                    "trusted": bool(status.trusted),
                    "docmdp_ok": status.docmdp_ok is not False,
                    "coverage": getattr(status.coverage, "name", str(status.coverage)),
                    "summary": status.summary(),
                    # Certificate extraction is evidence collection only. EU DSS
                    # remains authoritative for trust and qualification.
                    "certificate_chain": [
                        base64.b64encode(certificate.dump()).decode()
                        for certificate in embedded_certificates
                    ],
                },
            )
    valid = bool(rows) and all(
        row["intact"] and row["cryptographically_valid"] and row["docmdp_ok"]
        for row in rows
    )
    json.dump(
        {
            "engine": "pyHanko",
            "engine_version": "0.36.2",
            "status": "valid" if valid else "invalid",
            "signature_count": len(rows),
            "signatures": rows,
            "scope": "Secondary CMS, byte-range and PDF modification cross-validation",
        },
        sys.stdout,
        sort_keys=True,
        separators=(",", ":"),
    )


if __name__ == "__main__":
    main()
