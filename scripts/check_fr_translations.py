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
GLOSSARY = {
    "Accounting": "Comptabilité",
    "Accounting Overview": "Vue d’ensemble comptable",
    "Approved Platform": "Plateforme agréée",
    "Bank Matching": "Rapprochement bancaire",
    "Closing": "Clôture",
    "General Reconciliation": "Lettrage général",
    "Matched Items and Undo": "Écritures lettrées / Annuler le lettrage",
    "Odoo Approved Platform": "Plateforme agréée Odoo",
    "Post": "Comptabiliser",
    "Ready for production": "Prêt pour la production",
    "Receipt": "Justificatif",
    "Review": "Vérifier",
    "Reviewed": "Vérifié",
    "Tax Package Line": "Ligne de liasse fiscale",
}
BAD_TRANSLATION_PATTERNS = {
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
                if sorted(HTML_TAG.findall(entry.msgid)) != sorted(
                    HTML_TAG.findall(translation),
                ):
                    errors.append(f"{path}: HTML structure changed for {entry.msgid!r}")
                for pattern, guidance in BAD_TRANSLATION_PATTERNS.items():
                    if pattern.search(translation):
                        errors.append(
                            f"{path}: poor French for {entry.msgid!r}: {guidance}",
                        )
                entries_by_source.setdefault(entry.msgid, []).append((path, translation))
                entries_by_module[module_name].add(entry.msgid)

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

    for source, occurrence in REQUIRED_OCCURRENCES.items():
        if occurrence not in occurrences_by_source.get(source, set()):
            errors.append(f"{source!r} must translate {occurrence}")

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
