import os
import time
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError

from .constants import INTERNAL_OPERATION
from odoo.addons.usl_sign.services import DSSClient, OpenTimestampsClient, StepCAClient

CAPABILITIES = (
    ("standard", "Standard documents", 10),
    ("strong", "Strong personal signatures", 20),
    ("qualified", "External qualified signatures", 30),
    ("daily_proof", "Daily timestamps", 40),
    ("rfc3161", "PDF signing timestamps", 50),
)

CAPABILITY_GUIDANCE = {
    "standard": (
        "Send everyday documents and keep a checked final copy.",
        "Checks PDF sealing, signature verification, and final storage.",
        False,
    ),
    "strong": (
        "Let known signers confirm each signature with a Pocket ID passkey.",
        "Checks Pocket ID, personal signature certificates, PDF verification, and final storage.",
        False,
    ),
    "qualified": (
        "Check a qualified signature returned by an external provider.",
        "Checks European trusted providers, signature verification, and final storage.",
        False,
    ),
    "daily_proof": (
        "Add an independent Bitcoin timestamp to each day's completed documents.",
        "Checks daily record signing, timestamp scheduling, recent jobs, and final storage.",
        False,
    ),
    "rfc3161": (
        "Add an independent timestamp directly inside a signed PDF.",
        "Optional. Checks whether an external timestamp service is enabled.",
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
            msg = "Only a Sign administrator can inspect signing services."
            raise AccessError(msg)

    @api.model
    def _ensure_company(self, company):
        company.ensure_one()
        existing = {
            row.code: row
            for row in self.sudo().search([("company_id", "=", company.id)])
        }
        for code, label, sequence in CAPABILITIES:
            row = existing.get(code)
            if row and (row.name != label or row.sequence != sequence):
                row.with_context(usl_sign_health_write=INTERNAL_OPERATION).write(
                    {"name": label, "sequence": sequence},
                )
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
            self.env["auth.oauth.provider"]._usl_pocketid_sign_configuration,
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
                "summary": "Final document storage is not configured.",
                "next_action": "Connect the Paperless service before sending documents.",
                "diagnostic_code": "paperless_not_configured",
                "version": f"DSS {dss.get('engineVersion', '6.4')}",
            }
        if self.code == "standard":
            return {
                "status": "ready",
                "summary": "Signed PDFs can be sealed, checked, and stored.",
                "next_action": False,
                "diagnostic_code": False,
                "version": f"DSS {dss.get('engineVersion', '6.4')} · Paperless {paperless.get('server_version', '')}",
            }
        if self.code == "strong":
            self._pocket(cache)
            self._step_ca(cache)
            return {
                "status": "ready",
                "summary": "Pocket ID confirmation and personal document signatures are available.",
                "next_action": False,
                "diagnostic_code": False,
                "version": f"DSS {dss.get('engineVersion', '6.4')} · Pocket ID fresh passkey",
            }
        if self.code == "qualified":
            if not dss.get("qualifiedTrustReady"):
                return {
                    "status": "action_required",
                    "summary": "The European trusted-provider list is not ready.",
                    "next_action": "Restore the trusted-provider list before importing a qualified signature.",
                    "diagnostic_code": "qualified_trust_unavailable",
                    "version": f"DSS {dss.get('engineVersion', '6.4')}",
                }
            return {
                "status": "ready",
                "summary": "Returned qualified signatures can be checked before acceptance.",
                "next_action": False,
                "diagnostic_code": False,
                "version": f"DSS {dss.get('engineVersion', '6.4')}",
            }
        if self.code == "daily_proof":
            if not self.company_id.sign_opentimestamps_enabled:
                return {
                    "status": "not_configured",
                    "summary": "Daily Bitcoin timestamps are disabled for this company.",
                    "next_action": "Enable Daily Bitcoin timestamps in Sign settings.",
                    "diagnostic_code": "daily_proof_disabled",
                    "version": "OpenTimestamps 0.4.5",
                }
            if not paperless:
                return {
                    "status": "not_configured",
                    "summary": "Daily timestamp records cannot be stored because Paperless is not configured.",
                    "next_action": "Connect the Paperless service before relying on daily timestamps.",
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
                    "summary": "A daily timestamp job is disabled or failing.",
                    "next_action": "Review the scheduled jobs and retry failed timestamps.",
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
                    "summary": "Daily timestamps are configured, but the latest day needs attention.",
                    "next_action": "Open Daily Timestamps and retry the failed operation.",
                    "diagnostic_code": latest.failure_code or "latest_manifest_failed",
                    "version": "OpenTimestamps 0.4.5",
                }
            if latest and latest.anchoring_status == "confirmed" and latest.archive_status != "archived":
                return {
                    "status": "degraded",
                    "summary": "The latest Bitcoin timestamp is confirmed but its final copy was not stored.",
                    "next_action": "Open Daily Timestamps and retry final storage.",
                    "diagnostic_code": "latest_manifest_archive_incomplete",
                    "version": "OpenTimestamps 0.4.5",
                }
            return {
                "status": "ready",
                "summary": "Completed documents are scheduled for a daily Bitcoin timestamp.",
                "next_action": False,
                "diagnostic_code": False,
                "version": "OpenTimestamps 0.4.5",
            }
        if not self.company_id.sign_rfc3161_enabled:
            return {
                "status": "not_configured",
                "summary": "PDF signing timestamps are optional and disabled.",
                "next_action": "Enable them only after configuring and reviewing an independent timestamp service.",
                "diagnostic_code": "tsa_disabled",
                "version": False,
            }
        if not os.getenv("USL_DSS_TSA_URL"):
            return {
                "status": "action_required",
                "summary": "PDF signing timestamps are enabled without a timestamp service.",
                "next_action": "Configure the timestamp service or disable PDF signing timestamps.",
                "diagnostic_code": "tsa_endpoint_missing",
                "version": False,
            }
        return {
            "status": "degraded",
            "summary": "The timestamp service is configured but has not yet been confirmed by a signed PDF.",
            "next_action": "Review the next timestamped signature before relying on this service.",
            "diagnostic_code": "tsa_not_yet_exercised",
            "version": f"DSS {dss.get('engineVersion', '6.4')}",
        }

    @api.model_create_multi
    def create(self, values_list):
        if self.env.context.get("usl_sign_health_write") is not INTERNAL_OPERATION:
            msg = "Signing capability rows are managed by health checks."
            raise AccessError(msg)
        return super().create(values_list)

    def write(self, values):
        if self.env.context.get("usl_sign_health_write") is not INTERNAL_OPERATION:
            msg = "Signing capability status is read-only."
            raise AccessError(msg)
        return super().write(values)

    def unlink(self):
        msg = "Signing capability status rows cannot be deleted."
        raise AccessError(msg)
