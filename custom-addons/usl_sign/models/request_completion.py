"""Final validation, completion certificate, evidence dossier, archive reconciliation and completed-dossier delivery."""

import base64
import hashlib
import json
import zipfile
from io import BytesIO

from cryptography import x509

from odoo import (
    SUPERUSER_ID,
    _,
    fields,
    models,
)
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import format_datetime

from .constants import INTERNAL_OPERATION, TRUST_LEVELS
from odoo.addons.usl_sign.services import (
    DSSServiceError,
    base64_text,
    field_content,
    field_value,
)


class SignRequest(models.Model):
    _inherit = "sign.oca.request"

    def _start_final_validation(self):
        self.ensure_one()
        if self.state not in {"sent", "viewed", "partial", "validating"}:
            msg = "This request is not ready for final validation."
            raise ValidationError(msg)
        if self.state != "validating":
            self._transition("validating", "validation_started")
        working = field_content(self.data)
        try:
            sealed = self._sign_dss_client().seal(
                working,
                request_reference=f"USL-SIGN-{self.id}",
                timestamp=self.company_id.sign_rfc3161_enabled,
            )
            final_data = base64.b64decode(sealed["document"])
            validation = self._sign_dss_client().validate(
                final_data, expected_level=self.requested_trust,
            )
            report_evidence = self._store_dss_reports(validation)
            if validation.get("status") != "valid":
                self._record_validation_failure(
                    validation.get("summary") or "DSS rejected the PDF.",
                    validation=validation,
                )
                return False
            return self._complete_validated_document(
                final_data, validation, report_evidence=report_evidence,
            )
        except DSSServiceError as error:
            self.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                {
                    "validation_status": "indeterminate",
                    "last_error": str(error),
                    "recovery_action": "Restore the local signature services and retry validation.",
                },
            )
            self._transition(
                "action_required", "validation_service_error", payload={"reason": str(error)},
            )
            return False

    def _store_dss_reports(self, validation):
        self.ensure_one()
        reports = validation.get("reports") or {}
        summary_payload = {key: value for key, value in validation.items() if key != "reports"}
        summary_raw = json.dumps(
            summary_payload, sort_keys=True, indent=2, ensure_ascii=False,
        ).encode()
        summary_digest = hashlib.sha256(summary_raw).hexdigest()[:12]
        summary_evidence = self._create_evidence(
            "validation",
            f"{self.name}-dss-validation-summary-{summary_digest}.json",
            summary_raw,
            mimetype="application/json",
            metadata={"engine": "EU DSS", "version": validation.get("engineVersion") or "6.4"},
        )
        for report_name in ("diagnostic", "detailed", "simple", "etsi"):
            report = reports.get(report_name)
            if not report:
                continue
            raw = report.encode() if isinstance(report, str) else bytes(report)
            digest = hashlib.sha256(raw).hexdigest()[:12]
            self._create_evidence(
                "validation",
                f"{self.name}-dss-{report_name}-{digest}.xml",
                raw,
                mimetype="application/xml",
                metadata={
                    "engine": "EU DSS",
                    "version": validation.get("engineVersion") or "6.4",
                    "report": report_name,
                },
            )
        return summary_evidence

    def _store_pdf_certificate_chains(self, cross_validation):
        """Preserve the exact certificates embedded in every PDF signature.

        pyHanko only extracts the CMS certificate bytes here. EU DSS remains
        authoritative for trust, qualification, timestamps and revocation.
        """
        self.ensure_one()
        signatures = cross_validation.get("signatures") or []
        if not signatures:
            msg = "The completed PDF contains no extractable signature certificate."
            raise DSSServiceError(msg)
        payload = {
            "format": "usl-sign-pdf-certificate-chains-v1",
            "extracted_by": cross_validation.get("engine") or "pyHanko",
            "engine_version": cross_validation.get("engine_version") or "0.36.2",
            "trust_authority": "EU DSS validation reports",
            "signatures": [],
        }
        try:
            for signature in signatures:
                encoded_chain = signature.get("certificate_chain") or []
                if not encoded_chain:
                    msg = "A completed PDF signature has no embedded certificate chain."
                    raise DSSServiceError(msg)
                chain = []
                for encoded in encoded_chain:
                    der = base64.b64decode(encoded, validate=True)
                    certificate = x509.load_der_x509_certificate(der)
                    not_before = (
                        certificate.not_valid_before_utc
                        if hasattr(certificate, "not_valid_before_utc")
                        else certificate.not_valid_before
                    )
                    not_after = (
                        certificate.not_valid_after_utc
                        if hasattr(certificate, "not_valid_after_utc")
                        else certificate.not_valid_after
                    )
                    chain.append(
                        {
                            "der": encoded,
                            "sha256": hashlib.sha256(der).hexdigest(),
                            "subject": certificate.subject.rfc4514_string(),
                            "issuer": certificate.issuer.rfc4514_string(),
                            "serial_number": format(certificate.serial_number, "x"),
                            "not_before": not_before.isoformat(),
                            "not_after": not_after.isoformat(),
                        },
                    )
                payload["signatures"].append(
                    {
                        "field_name": signature.get("field_name"),
                        "certificate_chain": chain,
                    },
                )
        except (TypeError, ValueError) as error:
            msg = "The completed PDF contains malformed certificate evidence."
            raise DSSServiceError(msg) from error
        return self._create_evidence(
            "certificate",
            f"{self.name}-embedded-pdf-certificate-chains.json",
            json.dumps(payload, sort_keys=True, indent=2).encode(),
            mimetype="application/json",
            metadata={
                "operation": "CMS certificate extraction",
                "trust_authority": "EU DSS",
                "signature_count": len(payload["signatures"]),
            },
        )

    def _expected_final_signature_count(self):
        """Return the PDF-signature count required by the selected workflow.

        Standard signers create attestations in the evidence trail; the PDF has
        one platform integrity seal. Strong signers each append one personal
        PAdES revision before the platform adds its final integrity seal.
        External providers own their PDF-signature topology, so no product
        count is imposed beyond independent validator agreement.
        """
        self.ensure_one()
        if self.requested_trust == "standard":
            return 1
        if self.requested_trust == "strong_personal":
            return len(self.signer_ids) + 1
        return None

    def _assert_pdf_signature_counts(
        self,
        validation,
        cross_validation,
        *,
        expected_count=None,
        phase="completed document",
    ):
        self.ensure_one()
        dss_count = validation.get("signatureCount")
        pyhanko_count = cross_validation.get("signature_count")
        valid_counts = (
            type(dss_count) is int
            and dss_count > 0
            and type(pyhanko_count) is int
            and pyhanko_count == dss_count
            and (expected_count is None or dss_count == expected_count)
        )
        if cross_validation.get("status") != "valid" or not valid_counts:
            expected = (
                f" exactly {expected_count}" if expected_count is not None else " matching"
            )
            msg = (
                f"EU DSS and pyHanko must report{expected} valid PDF signature(s) "
                f"for the {phase}."
            )
            raise DSSServiceError(msg)

    def _cross_validate_pdf(self, document):
        self.ensure_one()
        try:
            cross_validation = self._sign_dss_client().cross_validate(document)
        except DSSServiceError as error:
            cross_validation = {
                "engine": "pyHanko",
                "engine_version": "0.36.2",
                "status": "error",
                "summary": str(error),
            }
        self._create_evidence(
            "validation",
            f"{self.name}-pyhanko-cross-validation.json",
            json.dumps(
                cross_validation, sort_keys=True, indent=2, ensure_ascii=False,
            ).encode(),
            mimetype="application/json",
            metadata={"engine": "pyHanko", "version": "0.36.2"},
        )
        return cross_validation

    def _assert_strong_personal_revision(
        self,
        signer,
        certificate_serial,
        validation,
        cross_validation,
    ):
        self.ensure_one()
        if signer.request_id != self or self.requested_trust != "strong_personal":
            msg = "This personal signature does not belong to the Strong request."
            raise DSSServiceError(msg)
        prior_signers = self.signer_ids.filtered(
            lambda row: row.state == "signed" and row != signer,
        )
        if (
            not certificate_serial
            or certificate_serial in prior_signers.mapped("certificate_serial")
        ):
            msg = "Every Strong signer must use a distinct personal certificate."
            raise DSSServiceError(msg)
        expected_count = len(prior_signers) + 1
        self._assert_pdf_signature_counts(
            validation,
            cross_validation,
            expected_count=expected_count,
            phase="Strong signer revision",
        )
        return expected_count

    def _complete_validated_document(self, final_data, validation, *, report_evidence=None):
        self.ensure_one()
        achieved = validation.get("achievedTrust")
        if achieved != self.requested_trust:
            self._record_validation_failure(
                "The validated trust level does not meet the requested trust level.",
                validation=validation,
            )
            return False
        cross_validation = self._cross_validate_pdf(final_data)
        try:
            self._assert_pdf_signature_counts(
                validation,
                cross_validation,
                expected_count=self._expected_final_signature_count(),
            )
        except DSSServiceError as error:
            self.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                {
                    "validation_status": "indeterminate",
                    "last_error": str(error),
                    "recovery_action": "Have an evidence reviewer inspect both validation reports.",
                },
            )
            self._transition(
                "action_required",
                "cross_validation_disagreement",
                payload={
                    "expected_signature_count": self._expected_final_signature_count(),
                    "dss_signature_count": validation.get("signatureCount"),
                    "pyhanko_signature_count": cross_validation.get("signature_count"),
                    "pyhanko_status": cross_validation.get("status"),
                },
            )
            return False
        try:
            self._store_pdf_certificate_chains(cross_validation)
        except DSSServiceError as error:
            self.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                {
                    "validation_status": "indeterminate",
                    "last_error": str(error),
                    "recovery_action": "Have an evidence reviewer inspect the embedded PDF certificates.",
                },
            )
            self._transition(
                "action_required",
                "certificate_evidence_incomplete",
                payload={"reason": str(error)},
            )
            return False
        report_evidence = report_evidence or self._store_dss_reports(validation)
        if self.requested_trust == "qualified_external":
            now = fields.Datetime.now()
            for signer in self.signer_ids:
                signer.with_context(usl_sign_signer_transition=INTERNAL_OPERATION).write(
                    {
                        "state": "signed",
                        "signed_on": now,
                        "signature_hash": hashlib.sha256(final_data).hexdigest(),
                        "signed_document_sha256": hashlib.sha256(final_data).hexdigest(),
                        "authentication_method": "external_provider",
                        "consent_text": "Consent and signing authorization captured by the external qualified provider; see the imported proof package.",
                        "consent_version": "external-provider-proof-v1",
                        "consented_at": now,
                        "access_revoked": True,
                    },
                )
                self._create_evidence(
                    "consent",
                    f"{self.name}-{signer.id}-external-signing-result.json",
                    json.dumps(
                        {
                            "signer_id": signer.id,
                            "partner_id": signer.partner_id.id,
                            "name": signer.partner_id.name,
                            "authentication_method": "external_provider",
                            "validated_at": fields.Datetime.to_string(now),
                            "document_sha256": hashlib.sha256(final_data).hexdigest(),
                            "proof": "See external provider proof package and DSS reports.",
                        },
                        sort_keys=True,
                    ).encode(),
                    mimetype="application/json",
                    signer=signer,
                )
        validation_record = self.env["usl.sign.validation"].with_context(
            usl_sign_validation_create=INTERNAL_OPERATION,
        ).create(
            {
                "request_id": self.id,
                "engine_version": validation.get("engineVersion") or "6.4",
                "expected_trust": self.requested_trust,
                "achieved_trust": achieved,
                "status": "valid",
                "signature_count": validation.get("signatureCount", 0),
                "qualified_provider": validation.get("qualifiedProvider"),
                "certificate_summary": validation.get("certificates") or {},
                "timestamp_summary": validation.get("timestamps") or {},
                "revocation_summary": validation.get("revocation") or {},
                "summary": validation.get("summary") or "DSS validation passed.",
                "report_evidence_id": report_evidence.id,
            },
        )
        digest = hashlib.sha256(final_data).hexdigest()
        self.with_context(usl_sign_freeze=INTERNAL_OPERATION).write(
            {
                "final_data": field_value(final_data),
                "final_filename": f"{self.name}-signed.pdf",
                "final_sha256": digest,
                "data": field_value(final_data),
                "current_hash": digest,
                "achieved_trust": achieved,
                "validation_status": "valid",
                "evidence_status": "building",
                "last_error": False,
                "recovery_action": False,
            },
        )
        self._create_evidence(
            "signed",
            self.final_filename,
            final_data,
            mimetype="application/pdf",
        )
        self._append_event(
            "validation_passed",
            payload={"validation_id": validation_record.id, "sha256": digest},
        )
        try:
            self._build_completion_evidence()
        except (DSSServiceError, UserError) as error:
            self.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                {
                    "evidence_status": "incomplete",
                    "last_error": str(error),
                    "recovery_action": "Restore the evidence services and retry validation.",
                },
            )
            self._transition(
                "action_required",
                "evidence_build_failed",
                payload={"reason": type(error).__name__},
            )
            return False
        self._transition("evidence_incomplete", "evidence_package_built")
        self._archive_dossier()
        return validation_record

    def _completion_certificate_locale(self):
        self.ensure_one()
        language = self.company_id.partner_id.lang or self.env.lang or "en_US"
        return "fr_FR" if language.startswith("fr") else "en_US"

    def _completion_certificate_payload(self, locale=None):
        self.ensure_one()
        locale = locale or self._completion_certificate_locale()
        document = self.with_context(lang=locale, tz="UTC")
        french = locale == "fr_FR"

        def translated(english, french_text):
            return french_text if french else english

        def date_label(value):
            if not value:
                return translated("Not recorded", "Non enregistré")
            return f"{format_datetime(document.env, value, tz='UTC', dt_format='medium')} UTC"

        event_labels = {
            "request_created": translated("Request created", "Demande créée"),
            "document_frozen": translated(
                "Document frozen for signing", "Document figé pour signature",
            ),
            "request_sent": translated("Invitations made available", "Invitations mises à disposition"),
            "document_viewed": translated("Document viewed", "Document consulté"),
            "email_otp_verified": translated("Email code verified", "Code reçu par e-mail vérifié"),
            "strong_identity_verified": translated("Strong identity verified", "Identité renforcée vérifiée"),
            "standard_signature_applied": translated("Signer attestation recorded", "Attestation du signataire enregistrée"),
            "strong_personal_signature_applied": translated("Personal PDF signature applied", "Signature PDF personnelle apposée"),
            "validation_started": translated("Final validation started", "Validation finale démarrée"),
            "validation_passed": translated("Cryptographic validation passed", "Validation cryptographique réussie"),
            "evidence_package_built": translated("Evidence package built", "Dossier de preuves créé"),
            "request_completed": translated("Request completed", "Demande terminée"),
        }
        location_labels = {
            "granted": translated(
                "Browser-reported location provided (not authoritative)",
                "Localisation transmise par le navigateur (non vérifiée)",
            ),
            "refused": translated("Location permission declined", "Autorisation de localisation refusée"),
            "unavailable": translated("Location unavailable", "Localisation indisponible"),
            "unsupported": translated("Location unsupported by the browser", "Localisation non prise en charge par le navigateur"),
            "timeout": translated("Location request timed out", "Délai de localisation dépassé"),
        }
        location_by_signer = {
            evidence.signer_id.id: (evidence.metadata or {}).get("location_status")
            for evidence in self.evidence_ids.filtered(
                lambda row: row.kind == "authentication" and row.signer_id,
            )
            if (evidence.metadata or {}).get("location_status")
        }
        head = self.event_ids.verify_chain()
        if not head:
            raise UserError(_("The completion certificate requires an intact event history."))

        source_documents = self.document_ids.sorted(lambda row: (row.sequence, row.id))
        files = [
            {"name": source.filename, "sha256": source.source_sha256}
            for source in source_documents
        ]
        if not files:
            files = [
                {
                    "name": self.original_filename or self.filename or self.name,
                    "sha256": self.original_sha256,
                },
            ]

        authentication_labels = {
            "secure_link": translated(
                "Secure invitation link", "Lien d’invitation sécurisé",
            ),
            "email_otp": translated(
                "Secure link plus email verification code",
                "Lien sécurisé et code de vérification reçu par e-mail",
            ),
            "pocket_id": "Pocket ID",
            "portal": translated("Odoo portal account", "Compte portail Odoo"),
            "pocket_id_passkey": translated(
                "Fresh Pocket ID passkey", "Clé d’accès Pocket ID récente",
            ),
            "external_provider": translated(
                "External qualified-signature provider",
                "Prestataire externe de signature qualifiée",
            ),
            "external_record": translated(
                "Recorded by an external signing system",
                "Enregistré par un système de signature externe",
            ),
        }
        signer_state_labels = {
            "draft": translated("Draft", "Brouillon"),
            "notified": translated("Notified", "Notifié"),
            "viewed": translated("Viewed", "Consulté"),
            "authorized": translated("Authorized", "Autorisé"),
            "signed": translated("Signed", "Signé"),
            "external_recorded": translated(
                "Recorded as signed externally", "Signature externe enregistrée",
            ),
            "declined": translated("Declined", "Refusé"),
            "expired": translated("Expired", "Expiré"),
            "cancelled": translated("Cancelled", "Annulé"),
        }
        signers = []
        for signer in self.signer_ids.sorted(lambda row: (row.sequence, row.id)):
            verification = authentication_labels.get(
                signer.authentication_method,
                signer.authentication_method
                or translated("Secure invitation link", "Lien d’invitation sécurisé"),
            )
            if self.requested_trust == "strong_personal":
                certificate = signer.certificate_serial or translated("serial not recorded", "numéro non enregistré")
                verification = translated(
                    f"{verification}; personal certificate {certificate}",
                    f"{verification} ; certificat personnel {certificate}",
                )
                signature_kind = translated("Personal PAdES signature", "Signature PAdES personnelle")
            elif self.requested_trust == "qualified_external":
                signature_kind = translated("External provider signature", "Signature du prestataire externe")
            else:
                signature_kind = translated(
                    "Signer attestation; not a personal PDF certificate",
                    "Attestation du signataire ; pas un certificat PDF personnel",
                )
            signers.append(
                {
                    "name": signer.partner_id.name,
                    "email": signer.partner_id.email or "",
                    "role": signer.role_id.with_context(lang=locale).name,
                    "verification": verification,
                    "signed_at": date_label(signer.signed_on),
                    "status": signer_state_labels.get(signer.state, signer.state),
                    "signature_kind": signature_kind,
                },
            )

        visible_events = self.event_ids.filtered(
            lambda event: event.event_type in event_labels or event.signer_id,
        ).sorted(lambda event: event.sequence)
        events = []
        for event in visible_events:
            actor_partner = event.signer_id.partner_id or event.actor_id.partner_id
            actor = actor_partner.name if actor_partner else translated("System", "Système")
            if actor_partner and actor_partner.email:
                actor = f"{actor} · {actor_partner.email}"
            event_payload = (event.payload or {}).get("payload") or {}
            location_status = event_payload.get("location_status") or (
                location_by_signer.get(event.signer_id.id) if event.signer_id else None
            )
            events.append(
                {
                    "action": event_labels.get(
                        event.event_type,
                        event.event_type.replace("_", " ").capitalize(),
                    ),
                    "actor": actor,
                    "timestamp": date_label(event.occurred_at),
                    "network_address": event.ip_address or "",
                    "location": location_labels.get(location_status, ""),
                    "proof_hash": event.event_hash,
                },
            )

        if self.requested_trust == "strong_personal":
            signature_structure = translated(
                f"{len(signers)} personal PAdES signature(s), followed by 1 final platform integrity seal",
                f"{len(signers)} signature(s) PAdES personnelle(s), puis 1 sceau d’intégrité final de la plateforme",
            )
        elif self.requested_trust == "qualified_external":
            signature_structure = translated(
                "External provider signature independently validated by the platform",
                "Signature du prestataire externe validée indépendamment par la plateforme",
            )
        else:
            signature_structure = translated(
                f"{len(signers)} signer attestation(s) and 1 final platform integrity seal",
                f"{len(signers)} attestation(s) de signataire et 1 sceau d’intégrité final de la plateforme",
            )

        validation = self.validation_ids.sorted(lambda row: (row.create_date, row.id))[-1:]
        validation_summary = translated(
            "No validation run recorded",
            "Aucune validation enregistrée",
        )
        if validation:
            validation_summary = translated(
                f"{validation.engine} {validation.engine_version}: {validation.status}; {validation.signature_count} PDF signature(s)",
                f"{validation.engine} {validation.engine_version} : {validation.status} ; {validation.signature_count} signature(s) PDF",
            )
        created_event = self.event_ids.filtered(lambda event: event.event_type == "request_created")[:1]
        created_by = {
            "name": self.user_id.name,
            "email": self.user_id.email or "",
        }
        if created_event and created_event.ip_address:
            created_by["network_address"] = created_event.ip_address
        return {
            "reference": self.name,
            "title": self.name,
            "created_at": date_label(self.create_date),
            "created_by": created_by,
            "completed_at": date_label(self.completed_at or fields.Datetime.now()),
            "summary": translated(
                "All required participants completed the request and the final PDF passed cryptographic validation. This certificate reports retained evidence without making a legal qualification decision.",
                "Tous les participants requis ont terminé la demande et le PDF final a passé la validation cryptographique. Ce certificat décrit les preuves conservées sans décider de leur qualification juridique.",
            ),
            "files": files,
            "final_document": {
                "name": self.final_filename,
                "sha256": self.final_sha256,
                "signature_structure": signature_structure,
            },
            "signers": signers,
            "events": events,
            "evidence": [
                {
                    "label": translated(
                        "Signed document SHA-256", "SHA-256 du document signé",
                    ),
                    "value": self.final_sha256,
                },
                {"label": translated("Event-chain head", "Tête de chaîne des événements"), "value": head.event_hash},
                {"label": translated("Signature structure", "Structure des signatures"), "value": signature_structure},
                {"label": translated("Cryptographic validation", "Validation cryptographique"), "value": validation_summary},
                {
                    "label": translated("Evidence manifest", "Manifeste des preuves"),
                    "value": translated(
                        "Generated and platform-signed after this certificate",
                        "Généré et signé par la plateforme après ce certificat",
                    ),
                },
            ],
            "delivery": [
                {
                    "label": translated("Signed document", "Document signé"),
                    "status": translated(
                        "Prepared for Odoo Documents and the configured private Paperless archive",
                        "Préparé pour Documents Odoo et l’archive Paperless privée configurée",
                    ),
                },
                {
                    "label": translated("Evidence dossier", "Dossier de preuves"),
                    "status": translated(
                        "This certificate becomes its visible cover; signed evidence files are embedded in PDF/A-3",
                        "Ce certificat devient sa couverture visible ; les preuves signées sont intégrées au PDF/A-3",
                    ),
                },
            ],
            "disclaimer": translated(
                "This certificate is a readable index of retained evidence. The signed PDF, signed evidence manifest, validation reports and protected raw evidence are the authoritative technical record. Current trust-list, qualification and revocation decisions require a maintained authoritative validator.",
                "Ce certificat est un index lisible des preuves conservées. Le PDF signé, le manifeste de preuves signé, les rapports de validation et les preuves brutes protégées constituent le dossier technique de référence. Les décisions actuelles de confiance, de qualification et de révocation nécessitent un validateur faisant autorité et maintenu à jour.",
            ),
        }

    def _completion_certificate_render(self):
        self.ensure_one()
        if not self.company_id.usl_document_renderer_enabled:
            raise UserError(_("The governed document renderer is disabled for this company."))
        locale = self._completion_certificate_locale()
        company, assets = self.company_id._usl_document_renderer_company_payload(locale)
        template = self.env.ref("usl_document_templates.template_sign_completion_v1")
        return self.env["usl.document.renderer"].render(
            template,
            company,
            self._completion_certificate_payload(locale),
            locale,
            assets=assets,
        )

    def _stamp_completion_renderer_provenance(self, rendered, evidence):
        self.ensure_one()
        template = self.env.ref("usl_document_templates.template_sign_completion_v1")
        attachments = self.env["ir.attachment"].with_user(SUPERUSER_ID).search(
            [
                ("res_model", "=", evidence._name),
                ("res_id", "=", evidence.id),
                ("res_field", "=", "data"),
            ],
        )
        attachments |= self.env["ir.attachment"].with_user(SUPERUSER_ID).search(
            [
                ("res_model", "=", self._name),
                ("res_id", "=", self.id),
                ("res_field", "=", "completion_certificate"),
            ],
        )
        attachments.write(
            {
                "usl_document_template_id": template.id,
                "usl_document_template_revision": rendered["template_revision"],
                "usl_document_payload_sha256": rendered["payload_sha256"],
                "usl_document_renderer_version": rendered["renderer_version"],
                "usl_document_company_id": self.company_id.id,
                "usl_document_rendered_at": fields.Datetime.now(),
            },
        )

    @staticmethod
    def _dossier_artifact(kind, name, content, mimetype, description):
        return {
            "kind": kind,
            "name": name,
            "content": content,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
            "mimetype": mimetype,
            "relationship": "Data" if kind == "signed" else "Supplement",
            "description": description,
        }

    def _technical_validation_reports_zip(self):
        self.ensure_one()
        reports = self.evidence_ids.filtered(lambda row: row.kind == "validation").sorted(
            lambda row: (
                str((row.metadata or {}).get("engine") or ""),
                str((row.metadata or {}).get("report") or ""),
                row.sha256,
                row.name,
            ),
        )
        stream = BytesIO()
        index = []
        entries = []
        for sequence, evidence in enumerate(reports, start=1):
            extension = {
                "application/json": "json",
                "application/xml": "xml",
                "text/xml": "xml",
            }.get(evidence.mimetype, "bin")
            metadata = evidence.metadata or {}
            engine = str(metadata.get("engine") or "validation").lower()
            engine = "".join(character if character.isalnum() else "-" for character in engine)
            engine = "-".join(filter(None, engine.split("-"))) or "validation"
            report = str(metadata.get("report") or "report").lower()
            report = "".join(character if character.isalnum() else "-" for character in report)
            report = "-".join(filter(None, report.split("-"))) or "report"
            filename = f"{sequence:02d}-{engine}-{report}.{extension}"
            content = field_content(evidence.data)
            entries.append((filename, content))
            index.append(
                {
                    "name": filename,
                    "engine": metadata.get("engine") or "Not recorded",
                    "report": metadata.get("report") or "validation result",
                    "sha256": evidence.sha256,
                    "mimetype": evidence.mimetype,
                },
            )
        with zipfile.ZipFile(
            stream,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            index_info = zipfile.ZipInfo("index.json", date_time=(1980, 1, 1, 0, 0, 0))
            index_info.compress_type = zipfile.ZIP_DEFLATED
            index_info.external_attr = 0o100644 << 16
            archive.writestr(
                index_info,
                json.dumps(
                    {
                        "format": "usl-sign-technical-validation-index-v1",
                        "reports": index,
                    },
                    sort_keys=True,
                    indent=2,
                    ensure_ascii=False,
                ).encode(),
            )
            for filename, content in entries:
                info = zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, content)
        return stream.getvalue()

    def _dossier_artifacts_v2(self):
        self.ensure_one()
        source_documents = self.document_ids.sorted(lambda row: (row.sequence, row.id))
        original = (
            field_content(source_documents.data)
            if len(source_documents) == 1
            else field_content(self.original_data)
        )
        original = original or field_content(self.original_data) or field_content(self.final_data)
        frozen = field_content(self.original_data) or original
        completed_signers = self.signer_ids.sorted(lambda row: (row.sequence, row.id))
        signing_summary = {
            "format": "usl-sign-signing-summary-v2",
            "request": self.name,
            "company": self.company_id.name,
            "completed_at": fields.Datetime.to_string(self.completed_at),
            "method": self.requested_trust,
            "achieved_trust": self.achieved_trust,
            "policy": {
                "name": (self.policy_snapshot or {}).get("name"),
                "version": self.policy_version,
            },
            "proof": {
                "signer_attestations": (
                    len(completed_signers) if self.requested_trust == "standard" else 0
                ),
                "personal_pades_signatures": (
                    len(completed_signers)
                    if self.requested_trust == "strong_personal"
                    else 0
                ),
                "platform_integrity_seals": (
                    1 if self.requested_trust in {"standard", "strong_personal"} else 0
                ),
                "expected_pdf_signature_count": self._expected_final_signature_count(),
            },
            "signers": [
                {
                    "name": signer.partner_id.name,
                    "role": signer.role_id.name,
                    "order": sequence,
                    "status": signer.state,
                    "signed_at": fields.Datetime.to_string(signer.signed_on),
                    "authentication": signer.authentication_method or None,
                    "pdf_proof": (
                        "personal_pades_signature"
                        if self.requested_trust == "strong_personal"
                        else "signer_attestation"
                    ),
                    "certificate_serial": (
                        signer.certificate_serial
                        if self.requested_trust == "strong_personal"
                        else None
                    ),
                }
                for sequence, signer in enumerate(completed_signers, start=1)
            ],
            "documents": [
                {
                    "name": document.filename,
                    "order": sequence,
                    "annex": document.is_annex,
                    "sha256": document.source_sha256,
                }
                for sequence, document in enumerate(source_documents, start=1)
            ],
            "consent_sha256": hashlib.sha256(
                (self.consent_text_snapshot or "").encode(),
            ).hexdigest(),
            "privacy": (
                "Raw authentication, network, browser, and optional location evidence "
                "remains in reviewer-only Odoo storage and is not copied into this dossier."
            ),
        }
        signer_names = {
            signer.id: signer.partner_id.name for signer in completed_signers
        }
        event_history = {
            "format": "usl-sign-event-history-v2",
            "sanitized": True,
            "chain_head": self.event_ids.verify_chain().event_hash or None,
            "events": [
                {
                    "sequence": event.sequence,
                    "event": event.event_type,
                    "occurred_at": fields.Datetime.to_string(event.occurred_at),
                    "signer": signer_names.get(event.signer_id.id),
                    "authentication": event.authentication_method or None,
                    "state_from": event.state_from or None,
                    "state_to": event.state_to or None,
                    "previous_hash": event.previous_hash or None,
                    "event_hash": event.event_hash,
                }
                for event in self.event_ids.sorted(lambda row: row.sequence)
            ],
        }
        validation_summary = {
            "format": "usl-sign-validation-summary-v2",
            "authority": "EU DSS",
            "independent_cross_check": "pyHanko",
            "trust_limit": (
                "The browser checker verifies integrity offline. Current trust-list, "
                "qualification, and revocation decisions require maintained authoritative data."
            ),
            "runs": [
                {
                    "engine": validation.engine,
                    "engine_version": validation.engine_version,
                    "expected_trust": validation.expected_trust,
                    "achieved_trust": validation.achieved_trust,
                    "status": validation.status,
                    "signature_count": validation.signature_count,
                    "qualified_provider": validation.qualified_provider or None,
                    "summary": validation.summary,
                }
                for validation in self.validation_ids.sorted(
                    lambda row: (row.create_date, row.id),
                )
            ],
        }
        chain_evidence = self.evidence_ids.filtered(
            lambda row: row.kind == "certificate"
            and (row.metadata or {}).get("operation") == "CMS certificate extraction",
        ).sorted(
            lambda row: ((row.metadata or {}).get("signature_count") or 0, row.id),
            reverse=True,
        )[:1]
        if not chain_evidence:
            msg = "The validated PDF certificate chains are unavailable for the proof package."
            raise UserError(msg)
        artifacts = [
            self._dossier_artifact(
                "original",
                "original-document.pdf",
                original,
                "application/pdf",
                "Original document supplied for signing",
            ),
            self._dossier_artifact(
                "frozen",
                "frozen-document.pdf",
                frozen,
                "application/pdf",
                "Frozen document revision reviewed by the signers",
            ),
            self._dossier_artifact(
                "signed",
                "signed-document.pdf",
                field_content(self.final_data),
                "application/pdf",
                "Final independently validated signed document",
            ),
            self._dossier_artifact(
                "completion",
                "completion-certificate.pdf",
                field_content(self.completion_certificate),
                "application/pdf",
                "Human-readable completion receipt",
            ),
            self._dossier_artifact(
                "signing_summary",
                "signing-summary.json",
                json.dumps(signing_summary, sort_keys=True, indent=2, ensure_ascii=False).encode(),
                "application/json",
                "Plain signing and authentication summary",
            ),
            self._dossier_artifact(
                "event_history",
                "event-history.json",
                json.dumps(event_history, sort_keys=True, indent=2, ensure_ascii=False).encode(),
                "application/json",
                "Sanitized lifecycle history and integrity hashes",
            ),
            self._dossier_artifact(
                "validation_summary",
                "validation-summary.json",
                json.dumps(
                    validation_summary, sort_keys=True, indent=2, ensure_ascii=False,
                ).encode(),
                "application/json",
                "Plain validation summary and trust limits",
            ),
            self._dossier_artifact(
                "certificate_chains",
                "certificate-chains.json",
                field_content(chain_evidence.data),
                "application/json",
                "Certificates embedded in every PDF signature",
            ),
            self._dossier_artifact(
                "technical_validation_reports",
                "technical-validation-reports.zip",
                self._technical_validation_reports_zip(),
                "application/zip",
                "Detailed validation reports with a readable index",
            ),
        ]
        timestamp_evidence = self.evidence_ids.filtered(
            lambda row: row.kind == "timestamp",
        ).sorted(lambda row: (row.created_at, row.sha256))
        for sequence, evidence in enumerate(timestamp_evidence, start=1):
            extension = "json" if evidence.mimetype == "application/json" else "ots"
            artifacts.append(
                self._dossier_artifact(
                    "timestamp_receipt",
                    f"timestamp-receipt-{sequence:02d}.{extension}",
                    field_content(evidence.data),
                    evidence.mimetype,
                    "Optional independent timestamp receipt",
                ),
            )
        return artifacts

    def _build_completion_evidence(self):
        self.ensure_one()
        rendered = self._completion_certificate_render()
        certificate = rendered["pdf"]
        certificate_evidence = self._create_evidence(
            "completion",
            f"{self.name}-completion-certificate.pdf",
            certificate,
            mimetype="application/pdf",
            metadata={
                "template": "sign_completion.v1",
                "template_revision": rendered["template_revision"],
                "payload_sha256": rendered["payload_sha256"],
                "renderer_version": rendered["renderer_version"],
            },
        )
        self.with_context(usl_sign_freeze=INTERNAL_OPERATION).write(
            {
                "completion_certificate": field_value(certificate),
                "completion_filename": certificate_evidence.name,
            },
        )
        self._stamp_completion_renderer_provenance(rendered, certificate_evidence)
        head = self.event_ids.verify_chain()
        snapshot_payload = {
            "format": "usl-sign-frozen-snapshot-v1",
            "request_id": self.id,
            "template_version": self.template_version,
            "documents": [
                {
                    "sequence": document.sequence,
                    "filename": document.filename,
                    "sha256": document.source_sha256,
                    "annex": document.is_annex,
                }
                for document in self.document_ids.sorted(lambda row: (row.sequence, row.id))
            ],
            "page_map": self.page_map,
            "field_layout": self.frozen_layout,
            "policy": self.policy_snapshot,
            "signers": self.signer_snapshot,
            "consent_text": self.consent_text_snapshot,
        }
        self._create_evidence(
            "snapshot",
            f"{self.name}-frozen-snapshot.json",
            json.dumps(
                snapshot_payload, sort_keys=True, indent=2, ensure_ascii=False,
            ).encode(),
            mimetype="application/json",
        )
        lifecycle_payload = {
            "format": "usl-sign-event-chain-v1",
            "request_id": self.id,
            "events": [
                {
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "occurred_at": fields.Datetime.to_string(event.occurred_at),
                    "actor_id": event.actor_id.id or None,
                    "signer_id": event.signer_id.id or None,
                    "authentication_method": event.authentication_method or None,
                    "ip_address": event.ip_address or None,
                    "user_agent": event.user_agent or None,
                    "state_from": event.state_from or None,
                    "state_to": event.state_to or None,
                    "payload": event.payload,
                    "previous_hash": event.previous_hash or None,
                    "payload_sha256": event.payload_sha256,
                    "event_hash": event.event_hash,
                }
                for event in self.event_ids.sorted(lambda row: row.sequence)
            ],
        }
        self._create_evidence(
            "lifecycle",
            f"{self.name}-event-chain.json",
            json.dumps(
                lifecycle_payload, sort_keys=True, indent=2, ensure_ascii=False,
            ).encode(),
            mimetype="application/json",
        )
        artifacts = self._dossier_artifacts_v2()
        manifest_payload = {
            "format": "usl-sign-evidence-manifest-v2",
            "request_name": self.name,
            "company": self.company_id.name,
            "requested_trust": self.requested_trust,
            "achieved_trust": self.achieved_trust,
            "original_sha256": next(
                artifact["sha256"] for artifact in artifacts if artifact["kind"] == "original"
            ),
            "frozen_sha256": next(
                artifact["sha256"] for artifact in artifacts if artifact["kind"] == "frozen"
            ),
            "final_sha256": self.final_sha256,
            "event_head": head.event_hash if head else None,
            "policy_version": self.policy_version,
            "consent_sha256": hashlib.sha256(
                (self.consent_text_snapshot or "").encode(),
            ).hexdigest(),
            "artifacts": [
                {
                    "kind": artifact["kind"],
                    "name": artifact["name"],
                    "sha256": artifact["sha256"],
                    "size": artifact["size"],
                    "mimetype": artifact["mimetype"],
                }
                for artifact in artifacts
            ],
        }
        manifest = json.dumps(
            manifest_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode()
        try:
            signed = self._sign_dss_client().sign_manifest(manifest)
        except DSSServiceError as error:
            raise UserError(f"The evidence manifest could not be signed: {error}") from error
        manifest = json.dumps(
            {
                "format": "usl-sign-detached-manifest-signature-v1",
                "manifest": base64.b64encode(manifest).decode(),
                "manifest_sha256": signed["manifestSha256"],
                "signature": signed["signature"],
                "signature_algorithm": signed["signatureAlgorithm"],
                "certificate_chain": signed["certificateChain"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        manifest_evidence = self._create_evidence(
            "manifest",
            "evidence-manifest.json",
            manifest,
            mimetype="application/json",
        )
        self.with_context(usl_sign_freeze=INTERNAL_OPERATION).write(
            {"evidence_manifest": manifest_evidence.data},
        )
        dossier = self._build_dossier_pdf(manifest, artifacts=artifacts)
        try:
            preflight = self._sign_dss_client().validate_pdfa(dossier)
            if not preflight.get("compliant"):
                msg = "veraPDF rejected the archival dossier preflight as non-conformant."
                raise DSSServiceError(  # noqa: TRY301 - normalized to an Odoo archival error below
                    msg,
                )
            self._create_evidence(
                "validation",
                f"{self.name}-verapdf-pdfa-3b-preflight.json",
                json.dumps(preflight, sort_keys=True, indent=2, ensure_ascii=False).encode(),
                mimetype="application/json",
                metadata={"engine": "veraPDF", "version": "1.30.2", "profile": "PDF/A-3b"},
            )
            # Rebuild from the exact same signed artifact set. The preflight
            # result is retained in reviewer-only Odoo evidence so the dossier
            # cannot invalidate its own already-signed manifest.
            dossier = self._build_dossier_pdf(manifest, artifacts=artifacts)
            sealed = self._sign_dss_client().seal(
                dossier,
                request_reference=f"USL-SIGN-DOSSIER-{self.id}",
                timestamp=self.company_id.sign_rfc3161_enabled,
            )
            dossier = base64.b64decode(sealed["document"])
            pdfa_validation = self._sign_dss_client().validate_pdfa(dossier)
            if not pdfa_validation.get("compliant"):
                msg = "veraPDF rejected the sealed archival dossier as non-conformant."
                raise DSSServiceError(  # noqa: TRY301 - normalized to an Odoo archival error below
                    msg,
                )
        except DSSServiceError as error:
            raise UserError(f"The archival dossier could not be sealed: {error}") from error
        self._create_evidence(
            "validation",
            f"{self.name}-verapdf-pdfa-3b-final-report.json",
            json.dumps(
                pdfa_validation,
                sort_keys=True,
                indent=2,
                ensure_ascii=False,
            ).encode(),
            mimetype="application/json",
            metadata={"engine": "veraPDF", "version": "1.30.2", "profile": "PDF/A-3b"},
        )
        dossier_evidence = self._create_evidence(
            "dossier",
            f"{self.name}-proof-package.pdf",
            dossier,
            mimetype="application/pdf",
        )
        self.with_context(usl_sign_freeze=INTERNAL_OPERATION).write(
            {
                "dossier_data": dossier_evidence.data,
                "dossier_filename": dossier_evidence.name,
                "evidence_status": "complete",
            },
        )

    def _build_dossier_pdf(self, manifest, *, artifacts=None):
        self.ensure_one()
        trust_labels = dict(TRUST_LEVELS)
        artifacts = list(artifacts or self._dossier_artifacts_v2())
        artifacts.append(
            self._dossier_artifact(
                "manifest",
                "evidence-manifest.json",
                manifest,
                "application/json",
                "Platform-signed canonical evidence manifest",
            ),
        )
        result = self._sign_dss_client().build_dossier(
            title=f"{self.company_id.name} signing evidence - {self.name}",
            summary=[
                "Purpose: human-auditable proof package; signed-document.pdf is the document of record",
                "Signature proof: see signing-summary.json for signer attestations, personal signatures, and the platform seal",
                "Validation: see validation-summary.json for results and the limits of offline checks",
                "Privacy: raw network, browser, location, and authentication evidence stays in restricted Odoo storage",
                "Files use stable names; open the PDF attachments panel to extract them",
                f"Company: {self.company_id.name}",
                f"Requested trust: {trust_labels.get(self.requested_trust, self.requested_trust)}",
                f"Achieved trust: {trust_labels.get(self.achieved_trust, self.achieved_trust or 'Not established')}",
                f"Original SHA-256: {next(item['sha256'] for item in artifacts if item['kind'] == 'original')}",
                f"Final SHA-256: {self.final_sha256}",
                f"Policy version: {self.policy_version}",
                "PDF signature validation authority: EU DSS 6.4",
                "Archive format: PDF/A-3b with associated evidence files",
            ],
            artifacts=artifacts,
            cover=field_content(self.completion_certificate),
        )
        return base64.b64decode(result["document"])

    def _archive_dossier(self, force=False):
        for request in self:
            if not request.final_data or not request.dossier_data:
                msg = "Build the signed document and complete proof package before archival."
                raise ValidationError(msg)
            if request.archive_status in {"pending", "processing", "archived"} and not force:
                continue
            # Attribute the controlled server-side upload to the requester so
            # private Documents ownership remains meaningful. ``sudo`` below
            # supplies the service privilege; the signer never needs archive
            # permissions.
            archive_actor = request.user_id
            artifacts = (
                (
                    "signed_document",
                    "archive_operation_id",
                    "archive_document_id",
                    request.final_filename,
                    request.final_data,
                ),
                (
                    "proof_package",
                    "dossier_archive_operation_id",
                    "archive_dossier_document_id",
                    request.dossier_filename,
                    request.dossier_data,
                ),
            )
            queued_states = {}
            try:
                for (
                    artifact_key,
                    operation_field,
                    document_field,
                    filename,
                    data,
                ) in artifacts:
                    if request[document_field]:
                        queued_states[artifact_key] = "already_archived"
                        continue
                    operation = request[operation_field]
                    if operation and operation.state in {"uploading", "processing"}:
                        queued_states[artifact_key] = operation.state
                        continue
                    # These files are generated by the controlled Sign service,
                    # not by the last signer. Keep archival under Odoo's system
                    # identity so a signer never needs Documents permissions.
                    result = (
                        self.env["usl.document"]
                        .with_user(archive_actor)
                        .sudo()
                        .with_company(request.company_id)
                        .upload_from_odoo(
                            filename,
                            base64_text(field_content(data)),
                            "application/pdf",
                            res_model=request._name,
                            res_id=request.id,
                            company_id=request.company_id.id,
                            confidentiality="private",
                            source="odoo_generated",
                        )
                    )
                    if not isinstance(result, dict) or result.get("state") not in {
                        "duplicate",
                        "pending",
                        "processing",
                    }:
                        msg = "Unexpected Paperless archival response."
                        raise ValueError(msg)  # noqa: TRY301
                    artifact_values = {
                        "archive_last_error": False,
                        "last_error": False,
                        "recovery_action": False,
                    }
                    if result["state"] == "duplicate" and result.get("document_id"):
                        artifact_values.update(
                            {
                                operation_field: False,
                                document_field: result["document_id"],
                            },
                        )
                    elif result.get("operation_id"):
                        artifact_values[operation_field] = result["operation_id"]
                    else:
                        msg = "Paperless returned no canonical document or operation relationship."
                        raise ValueError(msg)  # noqa: TRY301
                    request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                        artifact_values,
                    )
                    queued_states[artifact_key] = result["state"]
            # The Paperless boundary may raise connector, HTTP, ORM or response-shape
            # errors. All must become the same safe, recoverable product state.
            except Exception as error:  # noqa: BLE001
                request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                    {
                        "archive_status": "failed",
                        "archive_last_error": "Paperless rejected or could not accept the signed document or proof package.",
                        "last_error": "Paperless archival failed.",
                        "recovery_action": (
                            "Check that Paperless is available, then retry final storage."
                        ),
                        "evidence_status": "incomplete",
                    },
                )
                request._append_event(
                    "archive_failed", payload={"error": type(error).__name__},
                )
                continue
            request.invalidate_recordset()
            archive_complete = bool(
                request.archive_document_id and request.archive_dossier_document_id,
            )
            request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                {
                    "archive_status": "archived" if archive_complete else "processing",
                    "archive_last_error": False,
                    "last_error": False,
                    "recovery_action": False,
                    "evidence_status": "complete",
                },
            )
            request._append_event("archive_queued", payload=queued_states)
            request._reconcile_archive()

    def _reconcile_archive(self):
        for request in self:
            failed_operation = False
            for operation_field, document_field in (
                ("archive_operation_id", "archive_document_id"),
                ("dossier_archive_operation_id", "archive_dossier_document_id"),
            ):
                operation = request.sudo()[operation_field]
                if operation and operation.state == "processing":
                    try:
                        operation.poll()
                        operation.invalidate_recordset()
                    # Connector implementations expose different transport
                    # exceptions; fail closed while keeping archival retryable.
                    except Exception as error:  # noqa: BLE001
                        request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                            {
                                "archive_status": "failed",
                                "archive_last_error": "Paperless archival status could not be confirmed.",
                                "last_error": "Paperless archival reconciliation failed.",
                                "recovery_action": (
                                    "Check that Paperless is available, then retry final storage."
                                ),
                                "evidence_status": "incomplete",
                            },
                        )
                        request._append_event(
                            "archive_reconciliation_failed",
                            payload={
                                "artifact": document_field,
                                "error": type(error).__name__,
                            },
                        )
                        failed_operation = True
                        break
                if operation and operation.state == "archived" and operation.document_id:
                    request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                        {document_field: operation.document_id.id},
                    )
                elif operation and operation.state in {"duplicate", "failed"}:
                    request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                        {
                            "archive_status": "failed",
                            "archive_last_error": (
                                operation.error_message
                                or "The archived file needs an administrator to classify it."
                            ),
                            "last_error": "Paperless archival needs attention.",
                            "recovery_action": (
                                "A matching file in Paperless needs classification. "
                                "Resolve it there, then retry final storage."
                            ),
                            "evidence_status": "incomplete",
                        },
                    )
                    failed_operation = True
                    break
            if failed_operation:
                continue
            request.invalidate_recordset()
            archive_complete = bool(
                request.archive_document_id and request.archive_dossier_document_id,
            )
            if archive_complete:
                try:
                    request._share_archived_files_with_participants()
                except Exception as error:  # noqa: BLE001
                    request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                        {
                            "archive_status": "failed",
                            "archive_last_error": (
                                "The final files are stored, but participant access "
                                "could not be synchronized with Paperless."
                            ),
                            "last_error": "Final archive access synchronization failed.",
                            "recovery_action": (
                                "Check Paperless user mappings and permissions, then "
                                "retry final storage."
                            ),
                            "evidence_status": "incomplete",
                        },
                    )
                    request._append_event(
                        "archive_reconciliation_failed",
                        payload={
                            "artifact": "participant_access",
                            "error": type(error).__name__,
                        },
                    )
                    continue
            request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                {
                    "archive_status": "archived" if archive_complete else "processing",
                    "archive_last_error": False,
                    "last_error": False,
                    "recovery_action": False,
                    "evidence_status": "complete",
                },
            )
            if request.archive_status == "archived" and request.state == "evidence_incomplete":
                complete_signers = all(
                    signer.state == "signed" for signer in request.signer_ids
                )
                completion_ready = (
                    complete_signers
                    and request.validation_status == "valid"
                    and request.achieved_trust == request.requested_trust
                    and request.evidence_status == "complete"
                    and request.archive_document_id
                    and request.archive_dossier_document_id
                )
                if not completion_ready:
                    request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                        {
                            "last_error": "The completion gate found missing signatures, validation, evidence, or archive linkage.",
                            "recovery_action": "Have an evidence reviewer inspect the completion gate.",
                        },
                    )
                    request._transition("action_required", "completion_gate_failed")
                    continue
                request.with_context(usl_sign_transition=INTERNAL_OPERATION).write(
                    {
                        "completed_at": fields.Datetime.now(),
                        "last_error": False,
                        "recovery_action": False,
                    },
                )
                request._transition("completed", "request_completed")
                request._send_completed_dossier()

    def _send_completed_dossier(self):
        """Queue the final archived dossier once, using OCA's delivery setting."""
        for request in self:
            if (
                request.state != "completed"
                or not request.company_id.sign_oca_send_sign_request_copy
                or request.event_ids.filtered(
                    lambda event: event.event_type == "completed_dossier_queued",
                )
            ):
                continue
            attachment = self.env["ir.attachment"].sudo().search(
                [
                    ("res_model", "=", request._name),
                    ("res_id", "=", request.id),
                    ("res_field", "=", "dossier_data"),
                ],
                limit=1,
            )
            if not attachment:
                msg = "The completed evidence dossier attachment is missing."
                raise ValidationError(msg)
            partners = request.signer_ids.mapped("partner_id")
            body = self.env["ir.qweb"]._render(
                "usl_sign.sign_completion_delivery_body",
                {"record": request},
                engine="ir.qweb",
                minimal_qcontext=True,
            )
            self.env["mail.thread"].message_notify(
                body=body,
                partner_ids=partners.ids,
                subject=self.env._("Completed signature dossier: %(name)s", name=request.name),
                subtype_id=self.env.ref("mail.mt_comment").id,
                mail_auto_delete=False,
                email_layout_xmlid="mail.mail_notification_light",
                attachment_ids=attachment.ids,
            )
            request._append_event(
                "completed_dossier_queued",
                payload={
                    "partner_ids": partners.ids,
                    "filename": request.dossier_filename,
                    "sha256": hashlib.sha256(field_content(request.dossier_data)).hexdigest(),
                },
            )
