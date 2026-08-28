"""Render the governed 31-report accounting PDF acceptance pack.

Run with ``odoo shell`` against the isolated ``odoo_dev`` database.  Source
rows are read through the same report sessions as the browser and XLSX.  Four
families that are not represented in the migrated dump use conspicuously
labelled synthetic fixtures so visual QA never masquerades as accounting
evidence.
"""

# ruff: noqa: EM101, F821, T201

import os
from datetime import date
from pathlib import Path

OUTPUT_DIR = Path(os.environ.get(
    "USL_ACCOUNTING_REVIEW_OUTPUT",
    "/tmp/usl-accounting-review-v2",
))
PERIOD_FROM = date(2025, 10, 1)
PERIOD_TO = date(2026, 9, 30)

REPORTS = (
    ("trial_balance", "01-balance-generale.pdf"),
    ("general_ledger", "02-grand-livre.pdf"),
    ("journal_report", "03-journal-comptable.pdf"),
    ("partner_ledger", "04-grand-livre-auxiliaire.pdf"),
    ("customer_statement", "05-releve-client.pdf"),
    ("open_items", "06-ecritures-ouvertes.pdf"),
    ("aged_receivable", "07-balance-agee-clients.pdf"),
    ("aged_payable", "08-balance-agee-fournisseurs.pdf"),
    ("balance_sheet", "09-bilan.pdf"),
    ("profit_loss", "10-compte-resultat.pdf"),
    ("tax_report", "11-tva-et-taxes.pdf"),
    ("tax_report_group_account_tax", "12-taxes-compte-taxe.pdf"),
    ("tax_report_group_tax_account", "13-taxes-taxe-compte.pdf"),
    ("ec_sales_list", "14-etat-recapitulatif-tva-ue.pdf"),
    ("oss_sales", "15-ventes-oss.pdf"),
    ("oss_imports", "16-importations-oss.pdf"),
    ("bank_reconciliation", "17-rapprochement-bancaire.pdf"),
    ("currency_report", "18-change.pdf"),
    ("cash_flow", "19-flux-tresorerie.pdf"),
    ("executive_summary", "20-synthese-gestion.pdf"),
    ("analytic_report", "21-compte-resultat-analytique.pdf"),
    ("analytic_pivot", "22-analyse-analytique.pdf"),
    ("fixed_assets", "23-registre-immobilisations.pdf"),
    ("fixed_asset_group_account", "24-immobilisations-compte.pdf"),
    ("depreciation_schedule", "25-plan-amortissement.pdf"),
    ("deferred_schedule", "26-charges-produits-constates-avance.pdf"),
    ("french_annual", "27-etats-financiers-francais.pdf"),
    ("french_balance_sheet_2024", "28-bilan-detaille.pdf"),
    ("sig_caf_2024", "29-sig-caf.pdf"),
    ("french_tax_package", "30-liasse-fiscale-francaise.pdf"),
    ("closing_package", "31-dossier-revue-cloture.pdf"),
)

SYNTHETIC_ROWS = {
    "oss_sales": [
        {
            "period_key": "2026-T3",
            "country_code": "DE",
            "partner_name": "Client démonstration Berlin",
            "tax_name": "TVA OSS DE 19 %",
            "tax_treatment": "Régime UE — vente à distance",
            "taxable_amount": "12500.00",
            "tax_amount": "2375.00",
            "review_status": "À contrôler",
        },
        {
            "period_key": "2026-T3",
            "country_code": "ES",
            "partner_name": "Client démonstration Madrid",
            "tax_name": "TVA OSS ES 21 %",
            "tax_treatment": "Régime UE — vente à distance",
            "taxable_amount": "8400.00",
            "tax_amount": "1764.00",
            "review_status": "À contrôler",
        },
    ],
    "oss_imports": [
        {
            "period_key": "2026-T3",
            "country_code": "FR",
            "partner_name": "Destinataire démonstration Paris",
            "tax_name": "TVA IOSS FR 20 %",
            "tax_treatment": "Régime d’importation IOSS",
            "taxable_amount": "3250.00",
            "tax_amount": "650.00",
            "review_status": "À contrôler",
        },
        {
            "period_key": "2026-T3",
            "country_code": "BE",
            "partner_name": "Destinataire démonstration Bruxelles",
            "tax_name": "TVA IOSS BE 21 %",
            "tax_treatment": "Régime d’importation IOSS",
            "taxable_amount": "2100.00",
            "tax_amount": "441.00",
            "review_status": "À contrôler",
        },
    ],
    "deferred_schedule": [
        {
            "section": "Charges constatées d’avance",
            "deferred_date": "2026-07-01",
            "deferred_account_code": "486000",
            "source_original_name": "Assurance annuelle — démonstration",
            "amount": "4800.00",
            "deferred_account_balance": "2400.00",
            "review_status": "Planifié",
        },
        {
            "section": "Charges constatées d’avance",
            "deferred_date": "2026-09-01",
            "deferred_account_code": "486000",
            "source_original_name": "Abonnement logiciel — démonstration",
            "amount": "1200.00",
            "deferred_account_balance": "1000.00",
            "review_status": "Planifié",
        },
        {
            "section": "Produits constatés d’avance",
            "deferred_date": "2026-08-01",
            "deferred_account_code": "487000",
            "source_original_name": "Contrat annuel — démonstration",
            "amount": "9600.00",
            "deferred_account_balance": "8000.00",
            "review_status": "À comptabiliser",
        },
    ],
    "french_tax_package": [
        {
            "form_code": "2050",
            "section": "Bilan — Actif",
            "field_code": "CO",
            "field_label": "Total général actif",
            "amount": "286450.31",
            "rounded_amount": "286450.00",
            "review_status": "À valider",
            "source_reference": "Bilan détaillé — ACTIF_TOTAL",
        },
        {
            "form_code": "2051",
            "section": "Bilan — Passif",
            "field_code": "EE",
            "field_label": "Total général passif",
            "amount": "286450.31",
            "rounded_amount": "286450.00",
            "review_status": "À valider",
            "source_reference": "Bilan détaillé — PASSIF_TOTAL",
        },
        {
            "form_code": "2052",
            "section": "Compte de résultat",
            "field_code": "HN",
            "field_label": "Bénéfice ou perte",
            "amount": "18425.66",
            "rounded_amount": "18426.00",
            "review_status": "Source à confirmer",
            "source_reference": "Compte de résultat — CR_RESULTAT_NET",
        },
        {
            "form_code": "2065",
            "section": "Identification",
            "field_code": "REGIME",
            "field_label": "Régime fiscal",
            "value_text": "Réel normal — démonstration",
            "review_status": "Non déposé",
            "source_reference": "Paramètres fiscaux de la société",
        },
    ],
}

EXTRACT_LIMITS = {
    "general_ledger": 120,
    "partner_ledger": 120,
    "customer_statement": 120,
    "bank_reconciliation": 48,
}


def _review_extract(rows, limit):
    """Keep representative hierarchy blocks plus every exact control row."""
    controls = [
        row for row in rows
        if row.get("presentation_role") == "control"
    ]
    body = [
        row for row in rows
        if row.get("presentation_role") != "control"
    ]
    if len(body) <= limit:
        return rows
    selected = []
    root_groups = 0
    for row in body:
        is_root = (
            row.get("is_group") in (True, "true")
            and int(row.get("row_level") or 0) == 0
        )
        if is_root:
            root_groups += 1
            if root_groups > 4:
                break
        if len(selected) >= limit:
            break
        selected.append(row)
    return [*selected, *controls]


def _wizard_values(company, report_type, group_by):
    return {
        "report_type": report_type,
        "company_id": company.id,
        "company_ids": [(6, 0, [company.id])],
        "period_preset": "custom",
        "date_from": PERIOD_FROM,
        "date_to": PERIOD_TO,
        "target_move": "posted",
        "display_unit": "units",
        "amount_rounding": "cents",
        "hide_zero_accounts": False,
        "export_format": "pdf",
        "group_by": group_by or "none",
        "preview_limit": 100,
    }


def _render_wizard_report(company, report_type):
    definition = env["rebuild.account.report.definition"].search(
        [("report_type", "=", report_type)],
        limit=1,
    )
    wizard = env["rebuild.account.report.export.wizard"].create(
        _wizard_values(company, report_type, definition.default_group_by),
    )
    qualification = None
    try:
        rows = wizard._report_rows()
        if not rows and report_type in SYNTHETIC_ROWS:
            raw_rows = SYNTHETIC_ROWS[report_type]
            rows = wizard._group_report_rows(raw_rows)
            rows = wizard._append_shared_control_rows(rows)
            qualification = "DONNÉES SYNTHÉTIQUES — REVUE VISUELLE — SANS VALEUR COMPTABLE"
        limit = EXTRACT_LIMITS.get(report_type)
        if limit and len(rows) > limit:
            rows = _review_extract(rows, limit)
            qualification = "EXTRAIT DE REVUE VISUELLE — CONSULTER ODOO POUR L’ÉTAT COMPLET"
        renderer_wizard = wizard.with_context(
            usl_document_qualification_label=qualification,
        )
        return renderer_wizard._pdf_payload(rows), len(rows), qualification
    finally:
        wizard.unlink()


def _render_analytic_pivot(company):
    result = env["account.analytic.line"].with_context(
        allowed_company_ids=[company.id],
        lang="fr_FR",
    )._usl_analytic_pivot_document({
        "row_axes": ["account_id", "partner_id"],
        "column_axes": ["date:quarter"],
        "measures": ["rebuild_revenue", "rebuild_spending", "rebuild_net_contribution"],
        "domain": [
            ["date", ">=", PERIOD_FROM.isoformat()],
            ["date", "<=", PERIOD_TO.isoformat()],
        ],
        "context": {"lang": "fr_FR", "tz": "Europe/Paris"},
        "order": {"measure": "rebuild_net_contribution", "direction": "desc"},
        "company_id": company.id,
    })
    return result["pdf"], "ORM", None


OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
resume = os.environ.get("USL_ACCOUNTING_REVIEW_RESUME") == "1"
if not resume:
    for old_pdf in OUTPUT_DIR.glob("*.pdf"):
        old_pdf.unlink()

company = env["res.company"].search([], order="id", limit=1)
if not company:
    raise RuntimeError("The isolated QA database has no company.")

print(f"REVIEW_PACK company={company.display_name} period={PERIOD_FROM}/{PERIOD_TO}")
for report_type, filename in REPORTS:
    output_path = OUTPUT_DIR / filename
    if resume and output_path.exists():
        print("REUSED", filename, f"bytes={output_path.stat().st_size}")
        continue
    if report_type == "analytic_pivot":
        pdf, row_count, qualification = _render_analytic_pivot(company)
    else:
        pdf, row_count, qualification = _render_wizard_report(company, report_type)
    output_path.write_bytes(bytes(pdf))
    print(
        "RENDERED",
        filename,
        f"rows={row_count}",
        f"bytes={output_path.stat().st_size}",
        f"qualification={qualification or 'migrated'}",
    )

generated = sorted(OUTPUT_DIR.glob("*.pdf"))
if len(generated) != len(REPORTS):
    raise RuntimeError(
        f"Expected {len(REPORTS)} PDFs, generated {len(generated)}.",
    )
print(f"COMPLETE count={len(generated)} output={OUTPUT_DIR}")
