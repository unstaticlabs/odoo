"""Validate French catalogues shipped by USL product add-ons."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import polib

FORMAT_TOKEN = re.compile(
    r"%%|%\([^)]+\)[#0+\-]?(?:\d+|\*)?(?:\.\d+|\.\*)?[hlL]?[diouxXeEfFgGcrs]"
    r"|%[#0+\-]?(?:\d+|\*)?(?:\.\d+|\.\*)?[hlL]?[diouxXeEfFgGcrs]",
)
HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
VISIBLE_HTML_ATTRIBUTE = re.compile(
    r"(?P<prefix>\b(?:alt|aria-label|title)\s*=\s*)"
    r"(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE,
)
# "Match" is deliberately not a one-word glossary entry: accounting actions use
# rapprochement/lettrage while metadata predicates use correspondance. The exact
# entries below cover only surfaces whose business context is unambiguous.
GLOSSARY = {
    "Accounting": "Comptabilité",
    "Accounting Hygiene": "Hygiène comptable",
    "Accounting Overview": "Vue d’ensemble comptable",
    "Accounting Review": "Vérification comptable",
    "Accountant Review": "Vérification comptable",
    "Approved Platform": "Plateforme agréée",
    "Bank and Cash Balance": "Solde de trésorerie",
    "Bank Matching": "Rapprochement bancaire",
    "BIC/IS Normal": "BIC/IS — régime réel normal",
    "BIC/IS Simplified": "BIC/IS — régime réel simplifié",
    "BIC/IS Simplified (RSI)": "BIC/IS — régime réel simplifié (RSI)",
    "Cash and bank": "Trésorerie",
    "Cash on banks": "Trésorerie disponible",
    "Closing": "Clôture",
    "Closing Package": "Dossier de clôture",
    "Current closing workspace": "Espace de clôture en cours",
    "Draft Bills": "Factures fournisseurs en brouillon",
    "Draft Invoices": "Factures clients en brouillon",
    "Estimated amount": "Montant estimé dû par",
    "Estimated amount USL owes": "Montant estimé dû par USL à",
    "Exact match": "Correspondance exacte",
    "French VAT number": "Numéro de TVA français",
    "General Reconciliation": "Lettrage général",
    "Generated Journal Items": "Lignes comptables générées",
    "HR": "RH",
    "Matched Items and Undo": "Écritures lettrées / Annuler le lettrage",
    "Mark Reviewed": "Marquer comme vérifié",
    "Mark reviewed": "Marquer comme vérifié",
    "Needs review": "À vérifier",
    "Odoo Approved Platform": "Plateforme agréée Odoo",
    "Open matches": "Opérations correspondantes à traiter",
    "Open Vendor Bill": "Ouvrir la facture fournisseur",
    "Post": "Comptabiliser",
    "Ready for production": "Prêt pour la production",
    "Ready for Review": "Prêt pour vérification",
    "Ready for review": "Prêt pour vérification",
    "Review": "Vérifier",
    "Review Required": "Vérification requise",
    "Review State": "État de la vérification",
    "Review Status": "État de la vérification",
    "Review status": "État de la vérification",
    "Reviewed": "Vérifié",
    "Still to match": "Reste à rapprocher",
    "Tax Package Line": "Ligne de liasse fiscale",
    "To match": "À rapprocher",
    "Undo Match": "Annuler le rapprochement",
    "Unstatic Labs": "Unstatic Labs",
    "Upload": "Importer",
    "USL Accountant Review": "Vérification comptable USL",
    "VAT Accounts": "Comptes de TVA",
    "VAT Exemption / Franchise": "Franchise en base de TVA",
    "VAT Ledger Accounts": "Comptes généraux de TVA",
    "VAT Normal": "TVA — régime réel normal",
    "VAT Normal (CA3)": "TVA — régime réel normal (CA3)",
    "VAT Regime": "Régime de TVA",
    "VAT Report Wizard": "Assistant de déclaration de TVA",
    "VAT Simplified": "TVA — régime réel simplifié",
    "VAT Simplified (RSI / CA12)": "TVA — régime réel simplifié (RSI / CA12)",
    "VAT and Tax Report": "Rapport de TVA et autres taxes",
    "accounts": "comptes",
    "unnamed": "sans nom",
    "owes USL": "à USL",
    '<span class="badge text-bg-success">Matched</span>': (
        '<span class="badge text-bg-success">Rapproché</span>'
    ),
    "<strong>TESE and HR differ</strong>": (
        "<strong>Écart entre TESE et les données RH</strong>"
    ),
}
MODULE_GLOSSARY = {
    ("usl_accounting", "Best match"): "Meilleur rapprochement",
    ("usl_documents", "Best match"): "Meilleure correspondance",
}
BAD_TRANSLATION_PATTERNS = {
    re.compile(r"\bVAT\b"): "use the French abbreviation 'TVA'",
    re.compile(r"\bUsl\b"): "write the company abbreviation as 'USL'",
    re.compile(r"\bAccountant Review\b", re.IGNORECASE): (
        "translate the visible accounting-review role name"
    ),
    re.compile(r"\bprojets? de loi\b", re.IGNORECASE): (
        "translate supplier bills as 'factures fournisseurs'"
    ),
    re.compile(r"\bHEURE\b"): "translate Human Resources as 'RH'",
    re.compile(r"\bmatchs?\b", re.IGNORECASE): (
        "use contextual French: 'rapprochement', 'lettrage', or 'correspondance'"
    ),
    re.compile(
        r"\b(?:la|une|cette|meilleure|aucune)\s+rapprochement\b",
        re.IGNORECASE,
    ): "'rapprochement' is masculine",
    re.compile(
        r"\brapprochement\s+(?:partielle|exacte|totale)\b",
        re.IGNORECASE,
    ): "use masculine agreement after 'rapprochement'",
    re.compile(r"\bdans\s+rapprochement bancaire\b", re.IGNORECASE): (
        "use 'dans le Rapprochement bancaire'"
    ),
    re.compile(r"[\u200b\u200c\u200d\ufeff]"): "remove invisible formatting characters",
    re.compile(r"\bqualité comptable\b", re.IGNORECASE): (
        "use the product term 'hygiène comptable'"
    ),
    re.compile(r"\bseau d['’]hygiène\b", re.IGNORECASE): (
        "translate the accounting concept, not the English metaphor"
    ),
    re.compile(r"\bLaboratoires non statiques\b", re.IGNORECASE): (
        "keep the company name 'Unstatic Labs' unchanged"
    ),
    re.compile(r"\bl['’]PDF\b", re.IGNORECASE): "use 'le PDF'",
    re.compile(r"\bune\s+contrôle\b", re.IGNORECASE): "'contrôle' is masculine",
    re.compile(
        r"\b(?:éléments?|postes?)\s+(?:du|de)\s+journal\b",
        re.IGNORECASE,
    ): "translate Odoo journal items as 'lignes comptables'",
    re.compile(r"\bl['’]société\b", re.IGNORECASE): "use 'la société'",
    re.compile(r"\bl['’]test\b", re.IGNORECASE): "use 'le test'",
    re.compile(r"\bmagicien\b", re.IGNORECASE): "translate 'wizard' as 'assistant'",
    re.compile(r"\bjumelage bancaire\b", re.IGNORECASE): "use 'rapprochement bancaire'",
    re.compile(r"\bsécuritaire\b", re.IGNORECASE): "prefer 'sûr' or 'fiable'",
    re.compile(r"\bprêt pour la fabrication\b", re.IGNORECASE): "use 'prêt pour la production'",
    re.compile(r"\bsuivant\s*:", re.IGNORECASE): "use 'étape suivante :'",
}
REQUIRED_OCCURRENCES = {
    "Accounting": "model:ir.ui.menu,name:account.menu_finance",
}
CONTEXTUAL_GLOSSARY = {
    (
        "Receipt",
        "model:ir.model.fields,field_description:"
        "rebuild_account_migration.field_hr_expense__rebuild_receipt_state",
    ): "Justificatif",
}


def _html_structure(value: str) -> list[str]:
    """Ignore translated accessibility copy while preserving HTML structure."""

    return sorted(
        VISIBLE_HTML_ATTRIBUTE.sub(
            lambda match: (
                f"{match.group('prefix')}{match.group('quote')}__TEXT__"
                f"{match.group('quote')}"
            ),
            tag,
        )
        for tag in HTML_TAG.findall(value)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("addons", nargs="?", default="custom-addons", type=Path)
    parser.add_argument(
        "--reference-po",
        type=Path,
        help="optional clean-registry export used to detect missing catalogue entries",
    )
    args = parser.parse_args()

    errors: list[str] = []
    entries_by_source: dict[str, list[tuple[Path, str]]] = {}
    entries_by_module: dict[str, set[str]] = {}
    occurrences_by_source: dict[str, set[str]] = {}
    translations_by_occurrence: dict[tuple[str, str], list[tuple[Path, str]]] = {}
    catalogues = sorted(args.addons.glob("*/i18n/fr.po"))
    if not catalogues:
        errors.append(f"no French catalogues found below {args.addons}")

    for path in catalogues:
        module_name = path.parent.parent.name
        entries_by_module[module_name] = set()
        catalogue = polib.pofile(path)
        for entry in catalogue:
            if not entry.msgid:
                continue
            if "fuzzy" in entry.flags:
                errors.append(f"{path}: fuzzy translation for {entry.msgid!r}")
            translations = list(entry.msgstr_plural.values()) if entry.msgstr_plural else [entry.msgstr]
            occurrences_by_source.setdefault(entry.msgid, set()).update(
                occurrence for occurrence, _line in entry.occurrences
            )
            if not translations or any(not value.strip() for value in translations):
                errors.append(f"{path}: empty translation for {entry.msgid!r}")
                continue
            for translation in translations:
                if sorted(FORMAT_TOKEN.findall(entry.msgid)) != sorted(
                    FORMAT_TOKEN.findall(translation),
                ):
                    errors.append(f"{path}: formatting tokens changed for {entry.msgid!r}")
                if _html_structure(entry.msgid) != _html_structure(translation):
                    errors.append(f"{path}: HTML structure changed for {entry.msgid!r}")
                for pattern, guidance in BAD_TRANSLATION_PATTERNS.items():
                    if pattern.search(translation):
                        errors.append(
                            f"{path}: poor French for {entry.msgid!r}: {guidance}",
                        )
                entries_by_source.setdefault(entry.msgid, []).append((path, translation))
                entries_by_module[module_name].add(entry.msgid)
                for occurrence, _line in entry.occurrences:
                    translations_by_occurrence.setdefault(
                        (entry.msgid, occurrence),
                        [],
                    ).append((path, translation))

    if args.reference_po:
        if not args.reference_po.is_file():
            errors.append(f"reference PO does not exist: {args.reference_po}")
            reference = []
        else:
            reference = polib.pofile(str(args.reference_po))
        for entry in reference:
            if entry.obsolete or not entry.msgid:
                continue
            modules = set(re.findall(r"(?:^|\n)module:\s*([^\s]+)", entry.comment or ""))
            if modules:
                for module_name in modules:
                    if entry.msgid not in entries_by_module.get(module_name, set()):
                        errors.append(
                            f"{args.reference_po}: {module_name} has no maintained "
                            f"French translation for {entry.msgid!r}",
                        )
            elif entry.msgid not in entries_by_source:
                errors.append(
                    f"{args.reference_po}: no maintained French translation for {entry.msgid!r}",
                )

    for source, expected in GLOSSARY.items():
        for path, translation in entries_by_source.get(source, []):
            if translation != expected:
                errors.append(
                    f"{path}: {source!r} must use the product term {expected!r}, "
                    f"not {translation!r}",
                )

    for (module_name, source), expected in MODULE_GLOSSARY.items():
        for path, translation in entries_by_source.get(source, []):
            if path.parent.parent.name == module_name and translation != expected:
                errors.append(
                    f"{path}: {source!r} must use the contextual product term "
                    f"{expected!r}, not {translation!r}",
                )

    for source, occurrence in REQUIRED_OCCURRENCES.items():
        if occurrence not in occurrences_by_source.get(source, set()):
            errors.append(f"{source!r} must translate {occurrence}")

    for key, expected in CONTEXTUAL_GLOSSARY.items():
        source, occurrence = key
        translations = translations_by_occurrence.get(key, [])
        if not translations:
            errors.append(f"{source!r} must translate {occurrence}")
        for path, translation in translations:
            if translation != expected:
                errors.append(
                    f"{path}: {source!r} at {occurrence} must use {expected!r}, "
                    f"not {translation!r}",
                )

    if errors:
        print("French translation validation failed:")  # noqa: T201
        for error in errors:
            print(f"- {error}")  # noqa: T201
        return 1
    print(  # noqa: T201
        f"French translation validation passed: {len(catalogues)} catalogues",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
