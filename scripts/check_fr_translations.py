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
    "Receipt": "Justificatif",
    "Tax Package Line": "Ligne de liasse fiscale",
}
REQUIRED_OCCURRENCES = {
    "Accounting": "model:ir.ui.menu,name:account.menu_finance",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("addons", nargs="?", default="custom-addons", type=Path)
    args = parser.parse_args()

    errors: list[str] = []
    entries_by_source: dict[str, list[tuple[Path, str]]] = {}
    occurrences_by_source: dict[str, set[str]] = {}
    catalogues = sorted(args.addons.glob("*/i18n/fr.po"))
    if not catalogues:
        errors.append(f"no French catalogues found below {args.addons}")

    for path in catalogues:
        catalogue = polib.pofile(path)
        for entry in catalogue:
            if not entry.msgid:
                continue
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
                entries_by_source.setdefault(entry.msgid, []).append((path, translation))

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
