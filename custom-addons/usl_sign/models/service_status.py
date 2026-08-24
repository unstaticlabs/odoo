import os
import time
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError

from ..services import DSSClient, OpenTimestampsClient, StepCAClient
from .constants import INTERNAL_OPERATION


CAPABILITIES = (
    ("standard", "Standard signing", 10),
    ("strong", "Strong personal signing", 20),
    ("qualified", "Qualified external validation", 30),
    ("daily_proof", "Daily Bitcoin proof", 40),
    ("rfc3161", "Independent RFC 3161 timestamp", 50),
)

CAPABILITY_GUIDANCE = {
    "standard": (
        "Send routine documents and produce a sealed, independently validated result.",
        "Checks DSS PDF services, the platform seal, validation, and Paperless archival.",
        False,
    ),
    "strong": (
        "Use a fresh Pocket ID passkey to authorize a personal document signature.",
        "Checks Pocket ID, the short-lived certificate authority, DSS, and Paperless.",
        False,
    ),
    "qualified": (
        "Import and verify a qualified signature completed with an external provider.",
        "Checks DSS trusted-list validation and Paperless archival.",
        False,
    ),
    "daily_proof": (
        "Anchor each closed day's completed-document hashes to Bitcoin.",
        "Checks manifest signing, OpenTimestamps scheduling, recent jobs, and proof archival.",
        False,
    ),
    "rfc3161": (
        "Add an independent timestamp directly to a PDF signature when configured.",
        "Optional. Checks whether an external RFC 3161 timestamp authority is enabled.",
        True,
    ),
}


class SignServiceHealth(models.Model):
    _name = "usl.sign.service.health"
    _description = "Signing Capability Status"
    _order = "company_id, sequence, id"

    name = fields.Char(required=True, readonly=True)
    code = fields.Selection(
        [(code, label) for code, label, _sequence in CAPABILITIES],
        required=True,
        readonly=True,
    )
    sequence = fields.Integer(required=True, readonly=True)
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, index=True, ondelete="cascade",
    )
    status = fields.Selection(
        [
            ("ready", "Ready"),
            ("degraded", "Degraded"),
            ("not_configured", "Not configured"),
            ("unreachable", "Unreachable"),
            ("action_required", "Action required"),
        ],
        required=True,
        default="not_configured",
        readonly=True,
    )
    summary = fields.Char(required=True, readonly=True)
    version = fields.Char(readonly=True)
    latency_ms = fields.Integer(readonly=True)
    checked_at = fields.Datetime(readonly=True)
    checked_by_id = fields.Many2one("res.users", readonly=True, ondelete="set null")
    next_action = fields.Char(readonly=True)
    diagnostic_code = fields.Char(readonly=True)
    purpose = fields.Char(compute="_compute_guidance")
    checks = fields.Char(compute="_compute_guidance")
    is_optional = fields.Boolean(compute="_compute_guidance")

    _company_code_unique = models.Constraint(
        "UNIQUE(company_id, code)",
        "A company can have only one status row per signing capability.",
    )

    @api.depends("code")
    def _compute_guidance(self):
        for record in self:
            purpose, checks, optional = CAPABILITY_GUIDANCE.get(
                record.code,
                ("Signing capability.", "Checks its configured dependencies.", False),
            )
            record.purpose = purpose
            record.checks = checks
            record.is_optional = optional

    @api.model
    def _check_admin(self):
        if not self.env.user.has_group("usl_sign.group_sign_admin"):
            raise AccessError("Only a Sign administrator can inspect signing services.")

    @api.model
    def _ensure_company(self, company):
        company.ensure_one()
        existing = {
            row.code: row
            for row in self.sudo().search([("company_id", "=", company.id)])
        }
        missing = [
            {
                "name": label,
                "code": code,
                "sequence": sequence,
                "company_id": company.id,
                "summary": "Not checked yet.",
            }
            for code, label, sequence in CAPABILITIES
            if code not in existing
        ]
        if missing:
            self.sudo().with_context(usl_sign_health_write=INTERNAL_OPERATION).create(missing)
        rows = self.sudo().search(
            [("company_id", "=", company.id)],
            order="sequence, id",
        )
        return self.browse(rows.ids)

    @api.model
    def action_refresh_company(self):
        self._check_admin()
        records = self._ensure_company(self.env.company)
        records._refresh_checks()
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_refresh(self):
        self._check_admin()
        self.check_access("read")
        self._refresh_checks()
        return {"type": "ir.actions.client", "tag": "reload"}

    def _refresh_checks(self):
        cache = {}
        for record in self:
            started = time.monotonic()
            try:
                result = record._capability_result(cache)
            except Exception as error:  # noqa: BLE001 -- normalized operational status
                result = {
                    "status": "unreachable",
                    "summary": "A required service could not be checked safely.",
                    "next_action": "Inspect the service logs and environment configuration, then retry.",
                    "diagnostic_code": type(error).__name__,
                }
            result.update(
                {
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "checked_at": fields.Datetime.now(),
                    "checked_by_id": self.env.user.id,
                },
            )
            record.sudo().with_context(usl_sign_health_write=INTERNAL_OPERATION).write(result)
        return True

    def _cached(self, cache, key, callback):
        if key not in cache:
            cache[key] = callback()
        return cache[key]

    def _dss(self, cache):
        return self._cached(cache, "dss", DSSClient().health)

    def _paperless(self, cache):
        def check():
            client = self.env["usl.document"]._paperless()
            if not client.configured:
                return False
            return client.compatibility()

        return self._cached(cache, "paperless", check)

    def _pocket(self, cache):
        return self._cached(
            cache,
            "pocket",
            lambda: self.env["auth.oauth.provider"]._usl_pocketid_sign_configuration(),
        )

    def _step_ca(self, cache):
        return self._cached(cache, "step_ca", StepCAClient().health)

    def _capability_result(self, cache):
        self.ensure_one()
        dss = self._dss(cache)
        paperless = self._paperless(cache)
        if self.code in {"standard", "strong", "qualified"} and not paperless:
            return {
                "status": "not_configured",
                "summary": "Paperless archival is not configured.",
                "next_action": "Configure the Paperless service identity before signing.",
                "diagnostic_code": "paperless_not_configured",
                "version": f"DSS {dss.get('engineVersion', '6.4')}",
            }
        if self.code == "standard":
            return {
                "status": "ready",
                "summary": "PDF sealing, independent validation, and durable archival are available.",
                "next_action": False,
                "diagnostic_code": False,
                "version": f"DSS {dss.get('engineVersion', '6.4')} · Paperless {paperless.get('server_version', '')}",
            }
        if self.code == "strong":
            self._pocket(cache)
            self._step_ca(cache)
            return {
                "status": "ready",
                "summary": "Fresh Pocket ID passkey authorization and short-lived personal certificates are available.",
                "next_action": False,
                "diagnostic_code": False,
                "version": f"DSS {dss.get('engineVersion', '6.4')} · Pocket ID fresh passkey",
            }
        if self.code == "qualified":
            if not dss.get("qualifiedTrustReady"):
                return {
                    "status": "action_required",
                    "summary": "DSS trusted-list validation is not ready.",
                    "next_action": "Restore the EU trusted-list cache before importing a qualified signature.",
                    "diagnostic_code": "qualified_trust_unavailable",
                    "version": f"DSS {dss.get('engineVersion', '6.4')}",
                }
            return {
                "status": "ready",
                "summary": "Qualified-provider chains and achieved signature levels can be validated independently.",
                "next_action": False,
                "diagnostic_code": False,
                "version": f"DSS {dss.get('engineVersion', '6.4')}",
            }
        if self.code == "daily_proof":
            if not self.company_id.sign_opentimestamps_enabled:
                return {
                    "status": "not_configured",
                    "summary": "Daily Bitcoin existence proof is disabled for this company.",
                    "next_action": "Enable it in Sign settings to schedule closed-day evidence manifests.",
                    "diagnostic_code": "daily_proof_disabled",
                    "version": "OpenTimestamps 0.4.5",
                }
            if not paperless:
                return {
                    "status": "not_configured",
                    "summary": "Daily proof archival is unavailable because Paperless is not configured.",
                    "next_action": "Configure the Paperless service identity before relying on daily proof.",
                    "diagnostic_code": "paperless_not_configured",
                    "version": "OpenTimestamps 0.4.5",
                }
            OpenTimestampsClient()
            crons = self.env["ir.cron"].sudo().browse(
                [
                    self.env.ref("usl_sign.ir_cron_sign_daily_event_heads").id,
                    self.env.ref("usl_sign.ir_cron_sign_opentimestamps").id,
                ],
            )
            now = fields.Datetime.now()
            stale_before = now - timedelta(days=2)
            if any(
                not cron.active
                or cron.failure_count
                or (cron.lastcall and cron.lastcall < stale_before)
                or (cron.nextcall and cron.nextcall < now - timedelta(hours=1))
                for cron in crons
            ):
                return {
                    "status": "action_required",
                    "summary": "A daily proof scheduler is disabled or failing.",
                    "next_action": "Review the Evidence Manifest scheduled actions and retry failed proofs.",
                    "diagnostic_code": "daily_proof_cron_unhealthy",
                    "version": "OpenTimestamps 0.4.5",
                }
            latest = self.env["usl.sign.daily.manifest"].sudo().search(
                [("company_id", "=", self.company_id.id)],
                order="manifest_date desc, id desc",
                limit=1,
            )
            if latest and latest.anchoring_status == "action_required":
                return {
                    "status": "degraded",
                    "summary": "Daily proof is configured, but the latest manifest needs review.",
                    "next_action": "Open Evidence Manifests and retry the failed operation.",
                    "diagnostic_code": latest.failure_code or "latest_manifest_failed",
                    "version": "OpenTimestamps 0.4.5",
                }
            if latest and latest.anchoring_status == "confirmed" and latest.archive_status != "archived":
                return {
                    "status": "degraded",
                    "summary": "The latest Bitcoin proof is confirmed but its Paperless archive is incomplete.",
                    "next_action": "Open Evidence Manifests and retry proof archival.",
                    "diagnostic_code": "latest_manifest_archive_incomplete",
                    "version": "OpenTimestamps 0.4.5",
                }
            return {
                "status": "ready",
                "summary": "Closed UTC days are signed and scheduled for Bitcoin-backed existence proof.",
                "next_action": False,
                "diagnostic_code": False,
                "version": "OpenTimestamps 0.4.5",
            }
        if not self.company_id.sign_rfc3161_enabled:
            return {
                "status": "not_configured",
                "summary": "Independent RFC 3161 timestamping is optional and disabled.",
                "next_action": "Enable it only after configuring and reviewing an independent TSA.",
                "diagnostic_code": "tsa_disabled",
                "version": False,
            }
        if not os.getenv("USL_DSS_TSA_URL"):
            return {
                "status": "action_required",
                "summary": "RFC 3161 timestamping is enabled without a TSA endpoint.",
                "next_action": "Configure USL_DSS_TSA_URL or disable RFC 3161 timestamping.",
                "diagnostic_code": "tsa_endpoint_missing",
                "version": False,
            }
        return {
            "status": "degraded",
            "summary": "The TSA is configured; availability is verified during the next timestamped signature.",
            "next_action": "Review the next PAdES-T validation report before relying on this capability.",
            "diagnostic_code": "tsa_not_yet_exercised",
            "version": f"DSS {dss.get('engineVersion', '6.4')}",
        }

    @api.model_create_multi
    def create(self, values_list):
        if self.env.context.get("usl_sign_health_write") is not INTERNAL_OPERATION:
            raise AccessError("Signing capability rows are managed by health checks.")
        return super().create(values_list)

    def write(self, values):
        if self.env.context.get("usl_sign_health_write") is not INTERNAL_OPERATION:
            raise AccessError("Signing capability status is read-only.")
        return super().write(values)

    def unlink(self):
        raise AccessError("Signing capability status rows cannot be deleted.")
