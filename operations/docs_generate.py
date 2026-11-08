"""Turn journey records into the committed pages and screenshots.

``JourneyCase`` (``custom-addons/usl_docs/tests/journey.py``) writes one
``journey.json`` per journey and viewport, with the reader-facing sentence of
every documented step and the screen captured at it. This module renders
those records into ``docs/users/how-to/<slug>.md`` (or ``TUTORIAL.md``) and
``docs/users/how-to/<slug>/NN-step.png``, deterministically, so the page can
be committed and a CI check can prove it is current.

Screenshots are compared by pixels, never by bytes: Chromium's PNG encoder is
not byte-stable across runs, and a hash would replace every image every time.
A committed image is kept unless the new capture differs materially, so a
pull request shows a screenshot change only when the screen changed.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

from operations.user_docs import ROOT, USERS, frontmatter

RECORD_SCHEMA = "usl-docs-journey/v1"
GENERATED_NOTE = "<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->"
#: Fraction of pixels that may differ before a screenshot counts as changed,
#: and the per-channel distance below which a pixel counts as equal. The
#: first absorbs a font-hinting or antialiasing drift; the second absorbs
#: sub-pixel colour noise. A one-line text change in a form clears both.
PIXEL_RATIO_THRESHOLD = 0.004
PIXEL_CHANNEL_TOLERANCE = 24
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class DocsGenerateError(RuntimeError):
    """A record is malformed or two records disagree."""


@dataclass(frozen=True)
class Rendered:
    page_path: Path
    page_text: str
    images: dict  # relative image path -> PNG bytes
    sidecar_path: Path
    sidecar_text: str


# --- Records -----------------------------------------------------------------


def load_records(records_root):
    """Return the journey records under ``records_root``, keyed by journey id.

    Only the desktop viewport renders into the page today; other viewports
    are kept in the evidence. A journey recorded twice is an error.
    """
    records = {}
    for path in sorted(Path(records_root).rglob("journey.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("schema") != RECORD_SCHEMA:
            raise DocsGenerateError(f"{path}: unexpected schema {record.get('schema')!r}")
        record["_directory"] = path.parent
        key = (record["journey"], record["viewport"])
        if key in records:
            raise DocsGenerateError(f"journey {record['journey']} was recorded twice for {record['viewport']}")
        records[key] = record
    return records


def desktop_records(records):
    return {journey: record for (journey, viewport), record in records.items() if viewport == "desktop"}


# --- Rendering ---------------------------------------------------------------


def page_path_for(record):
    if record["type"] == "tutorial":
        return Path("TUTORIAL.md")
    if not SLUG_RE.match(record["journey"]):
        raise DocsGenerateError(f"journey id {record['journey']!r} is not a kebab-case slug")
    return Path("how-to") / f"{record['journey']}.md"


def image_directory_for(record):
    return Path("how-to" if record["type"] == "how-to" else "tutorial") / record["journey"]


def _screenshot_manifest(record, images):
    return {name.name: hashlib.sha256(data).hexdigest() for name, data in sorted(images.items())}


def render(record, existing_images=None):
    """Render one desktop record into a page and its images.

    ``existing_images`` maps relative image paths to the bytes already
    committed; an existing image is kept when the new capture is within the
    pixel tolerance, so the sha in the front matter is the committed file's.
    """
    existing_images = existing_images or {}
    page_path = page_path_for(record)
    image_dir = image_directory_for(record)
    images = {}
    lines = []
    for step in record["steps"]:
        text = step["text"].strip()
        lines.append(f"{step['index']}. {text}")
        shot = step.get("screenshot")
        if shot:
            source = record["_directory"] / shot["file"]
            data = source.read_bytes()
            relative = image_dir / shot["file"]
            previous = existing_images.get(relative)
            if previous is not None and not materially_different(previous, data):
                data = previous
            images[relative] = data
            alt = _alt_text(text)
            lines.append("")
            lines.append(f"   ![{alt}]({_relative_to_page(relative, page_path).as_posix()})")
        lines.append("")
    front = {
        "title": record["title"],
        "type": record["type"],
        "description": record["description"],
        "lang": record["lang"],
        "persona": record["persona"],
        "journey": record["journey"],
        "tour": record["tour"],
        "source": [_source_path(record)],
        "screenshots": _screenshot_manifest(record, images),
        "generated": True,
    }
    body = "\n".join([
        GENERATED_NOTE,
        "",
        f"# {record['title']}",
        "",
        record["description"].strip(),
        "",
        *lines,
    ]).rstrip("\n") + "\n"
    sidecar_path = image_dir / "journey.json"
    return Rendered(page_path, _dump_front(front) + "\n" + body, images, sidecar_path, _sidecar(record, images))


def _sidecar(record, images):
    """The facts the test record page shows, without anything that changes per run.

    Timestamps, the commit and the browser belong to the evidence a release
    carries; the sidecar carries what the journey *is*: its steps, their
    assertions and triggers, where the tour and the test live, and the
    digests of the screenshots the page shows.
    """
    digests = {path.name: hashlib.sha256(data).hexdigest() for path, data in images.items()}
    steps = []
    for step in record["steps"]:
        shot = step.get("screenshot") or {}
        steps.append({
            "index": step["index"],
            "id": step["id"],
            "text": step["text"],
            "assertion": step.get("assertion", ""),
            "trigger": step.get("trigger", ""),
            "run": step.get("run", ""),
            "screenshot": shot.get("file") if shot else None,
            "sha256": digests.get(shot.get("file")) if shot else None,
        })
    tour_source = record.get("source_tour_file") or {}
    test_source = record.get("source_test_file") or {}
    payload = {
        "schema": "usl-docs-journey-sidecar/v1",
        "journey": record["journey"],
        "tour": record["tour"],
        "type": record["type"],
        "title": record["title"],
        "persona": record["persona"],
        "lang": record["lang"],
        "viewport": record["viewport"],
        "viewport_size": record.get("viewport_size", ""),
        "login": record.get("login", ""),
        "source_test": record["source_test"],
        "source_test_file": {"path": test_source.get("path", ""), "line": test_source.get("line")},
        "source_tour_file": {
            "path": tour_source.get("path", ""),
            "line": tour_source.get("line"),
            "steps": tour_source.get("steps", {}),
        },
        "steps": steps,
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _relative_to_page(image, page_path):
    parent = page_path.parent
    return image if parent == Path(".") else image.relative_to(parent)


def _dump_front(front):
    # Nested mapping for screenshots is outside the flat subset; flatten it
    # into ``screenshot_<file>`` keys so the parser stays tiny.
    flat = {}
    for key, value in front.items():
        if key == "screenshots":
            for name, digest in value.items():
                flat[f"screenshot_{name.replace('.', '_')}"] = digest
        else:
            flat[key] = value
    return frontmatter().dump(flat)


def _source_path(record):
    module = record["source_test"].split(".")[2] if record["source_test"].startswith("odoo.addons.") else None
    if module:
        return f"custom-addons/{module}/tests/{record['source_test'].split('.')[4]}.py"
    return record["source_test"]


def _alt_text(text):
    """The first sentence of the step, without Markdown, as the image's alt."""
    plain = re.sub(r"[*_`\[\]()>#]", "", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    first = re.split(r"(?<=[.!?])\s", plain, maxsplit=1)[0]
    return first.rstrip(".!?")[:120]


# --- Pixel comparison --------------------------------------------------------


def materially_different(old, new, *, ratio=PIXEL_RATIO_THRESHOLD, tolerance=PIXEL_CHANNEL_TOLERANCE):
    """Return True when ``new`` shows a different screen than ``old``.

    Without Pillow the comparison degrades to bytes and says so; the
    qualification job installs Pillow so its verdict is the pixel one.
    """
    if old == new:
        return False
    try:
        from PIL import Image, ImageChops  # noqa: PLC0415 - optional
    except ImportError:
        return True
    a = Image.open(io.BytesIO(old)).convert("RGB")
    b = Image.open(io.BytesIO(new)).convert("RGB")
    if a.size != b.size:
        return True
    diff = ImageChops.difference(a, b).convert("L").point(lambda value: 255 if value > tolerance else 0)
    changed = diff.histogram()[255]
    return changed / (a.size[0] * a.size[1]) > ratio


def pillow_available():
    try:
        import PIL  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


# --- Entry points ------------------------------------------------------------


def _display(path):
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _existing_images(docs_root, rendered_dir):
    directory = docs_root / rendered_dir
    if not directory.is_dir():
        return {}
    return {rendered_dir / path.name: path.read_bytes() for path in sorted(directory.glob("*.png"))}


def render_all(records_root, docs_root=USERS):
    """Write every desktop record's page and images. Returns the paths written."""
    written = []
    for record in desktop_records(load_records(records_root)).values():
        existing = _existing_images(docs_root, image_directory_for(record))
        result = render(record, existing)
        page = docs_root / result.page_path
        if not page.exists() or page.read_text(encoding="utf-8") != result.page_text:
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text(result.page_text, encoding="utf-8")
            written.append(page)
        for relative, data in result.images.items():
            target = docs_root / relative
            if not target.exists() or target.read_bytes() != data:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                written.append(target)
        sidecar = docs_root / result.sidecar_path
        if not sidecar.exists() or sidecar.read_text(encoding="utf-8") != result.sidecar_text:
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(result.sidecar_text, encoding="utf-8")
            written.append(sidecar)
    return written


def check_all(records_root, docs_root=USERS):
    """Return ``(path, problem)`` for every page or image that is stale."""
    findings = []
    for record in desktop_records(load_records(records_root)).values():
        existing = _existing_images(docs_root, image_directory_for(record))
        result = render(record, existing)
        page = docs_root / result.page_path
        rel = _display(page)
        if not page.exists():
            findings.append((rel, "missing; run `make docs`"))
        elif page.read_text(encoding="utf-8") != result.page_text:
            findings.append((rel, "differs from what the journey produces; run `make docs`"))
        for relative, data in result.images.items():
            target = docs_root / relative
            if not target.exists():
                findings.append((_display(target), "missing; run `make docs`"))
            elif target.read_bytes() != data:
                findings.append((_display(target), "the screen changed materially; run `make docs`"))
        sidecar = docs_root / result.sidecar_path
        if not sidecar.exists():
            findings.append((_display(sidecar), "missing; run `make docs`"))
        elif sidecar.read_text(encoding="utf-8") != result.sidecar_text:
            findings.append((_display(sidecar), "differs from what the journey produces; run `make docs`"))
    return findings
