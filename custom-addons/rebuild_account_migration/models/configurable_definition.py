from odoo import fields, models


ACCOUNTING_DEFINITION_ORIGINS = [
    ("odoo", "Standard Odoo"),
    ("oca", "OCA Community"),
    ("localization", "Localization"),
    ("usl", "Unstatic Labs"),
    ("company", "Company-specific"),
]

ACCOUNTING_DEFINITION_LIFECYCLES = [
    ("draft", "Draft"),
    ("current", "Current"),
    ("deprecated", "Deprecated"),
]


class RebuildAccountConfigurableDefinitionMixin(models.AbstractModel):
    _name = "rebuild.account.configurable.definition.mixin"
    _description = "Configurable Accounting Definition"

    origin = fields.Selection(
        ACCOUNTING_DEFINITION_ORIGINS,
        required=True,
        default="usl",
        index=True,
    )
    source_module = fields.Char(
        required=True,
        default="rebuild_account_migration",
        readonly=True,
    )
    definition_version = fields.Char(
        required=True,
        default="1",
        index=True,
        help=(
            "Business version frozen into operational results and exports. "
            "Change it when applicability, calculations, or presentation "
            "semantics materially change."
        ),
    )
    lifecycle = fields.Selection(
        ACCOUNTING_DEFINITION_LIFECYCLES,
        required=True,
        default="current",
        index=True,
    )
    business_purpose = fields.Text(
        help="Why this definition exists and which business decision it supports.",
    )
    expected_outcome = fields.Text(
        help="What a successful result means to an Accounting user.",
    )
    effective_from = fields.Date(index=True)
    effective_to = fields.Date(index=True)
    technical_model = fields.Char(readonly=True)
    technical_summary = fields.Text(
        readonly=True,
        help="Installed implementation boundary for Technical Administrators.",
    )

    def _definition_snapshot(self):
        self.ensure_one()
        values = {
            "model": self._name,
            "id": self.id,
            "display_name": self.display_name,
            "origin": self.origin,
            "source_module": self.source_module,
            "definition_version": self.definition_version,
            "lifecycle": self.lifecycle,
            "business_purpose": self.business_purpose or "",
            "expected_outcome": self.expected_outcome or "",
            "effective_from": fields.Date.to_string(self.effective_from)
            if self.effective_from else "",
            "effective_to": fields.Date.to_string(self.effective_to)
            if self.effective_to else "",
            "technical_model": self.technical_model or "",
        }
        if "company_id" in self._fields:
            values["company_id"] = self.company_id.id
            values["company_name"] = self.company_id.display_name
        if "code" in self._fields:
            values["code"] = self.code
        return values
