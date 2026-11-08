"""The user guide as data: pages, navigation, evidence, and the agent views.

``docs/users`` travels with every release (the distribution image copies it and
development stacks bind-mount it), so the guide a reader opens from the Help
menu always describes the release they are using. Pages are Markdown with a
small front matter block (``frontmatter.py``); everything the viewer shows is
derived from those blocks at request time: the navigation, the pills, the
``llms.txt`` and ``index.json`` an agent reads, and the page the Help menu
opens for the screen a person is on. Nothing is committed twice.

This module holds no HTML. The controller passes what it returns to QWeb
templates (``views/templates.xml``); the ``usl.docs.guide`` model exposes the
same functions to agents through the JSON-2 API.
"""

from __future__ import annotations

import functools
import json
import os
import posixpath
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from markdown_it import MarkdownIt
from markupsafe import Markup

from odoo.tools import html_sanitize

from . import frontmatter

DOCS_ROUTE = "/usl/user-docs"
DOCS_ENV_VAR = "USL_USER_DOCS_PATH"
EVIDENCE_ENV_VAR = "USL_DOCS_EVIDENCE_PATH"
EVIDENCE_JSON_ENV_VAR = "USL_DOCS_EVIDENCE_JSON"
EVIDENCE_PARAMETER = "usl.docs.evidence"
RELEASE_MANIFEST_ENV_VAR = "USL_RELEASE_MANIFEST_JSON"
READER_GROUP = "base.group_user"
REPOSITORY_URL = "https://github.com/unstaticlabs/odoo"
RELEASE_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->\s*", re.DOTALL)
SIDECAR_SCHEMA = "usl-docs-journey-sidecar/v1"
INDEX_SCHEMA = "usl-docs-index/v1"
PAGE_SCHEMA = "usl-docs-page/v1"

#: The order the guide presents its sections in: learn, then do, then look
#: up, then understand. ``home`` is the landing page.
SECTIONS = (
    ("home", "Start here"),
    ("tutorial", "Tutorial"),
    ("how-to", "How-to guides"),
    ("reference", "Reference"),
    ("explanation", "Explanation"),
)
SECTION_LABELS = dict(SECTIONS)
SECTION_ORDER = {key: index for index, (key, _label) in enumerate(SECTIONS)}
TYPE_LABELS = {
    "tutorial": "Tutorial",
    "how-to": "How-to guide",
    "reference": "Reference",
    "explanation": "Explanation",
}
PERSONA_LABELS = {
    "everyone": "For everyone",
    "ceo": "For the CEO",
    "accountant": "For accountants",
    "finance_operator": "For finance operators",
    "employee": "For employees",
    "manager": "For managers",
    "administrator": "For administrators",
}
LANGUAGE_LABELS = {"en": "English", "fr": "Français"}
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
#: When the Help menu asks for a screen, a how-to answers before anything else.
CONTEXT_PRIORITY = {"how-to": 0, "tutorial": 1, "reference": 2, "explanation": 3}


# --- Where the pages are ------------------------------------------------


def _module_root():
    return Path(__file__).resolve().parents[1]


def _candidate_roots():
    env_path = os.environ.get(DOCS_ENV_VAR)
    if env_path:
        yield Path(env_path)
    module_root = _module_root()
    yield module_root / "static" / "user_docs"
    for parent in module_root.parents:
        candidate = parent / "docs" / "users"
        if candidate.exists():
            yield candidate


def docs_root():
    for root in _candidate_roots():
        if (root / "README.md").is_file():
            return root.resolve()
    return None


def resolve_inside(root, requested_path):
    """Return the file ``requested_path`` names inside ``root``, or ``None``."""
    candidate = (root / requested_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def safe_doc_path(root, requested_path):
    if not requested_path:
        requested_path = "README.md"
    requested_path = requested_path.strip("/")
    if requested_path in {"", "index", "index.html"}:
        requested_path = "README.md"
    if requested_path.endswith("/"):
        requested_path += "README.md"
    if not requested_path.endswith(".md"):
        requested_path += ".md"
    return resolve_inside(root, requested_path)


def safe_asset_path(root, requested_path):
    requested_path = (requested_path or "").strip("/")
    if Path(requested_path).suffix.lower() not in IMAGE_TYPES:
        return None
    return resolve_inside(root, requested_path)


def page_url(path, base_url=""):
    return f"{base_url}{DOCS_ROUTE}/{quote(path, safe='/')}"


# --- Pages --------------------------------------------------------------


def _title_from_markdown(path, text):
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "User Guide" if path.name == "README.md" else path.stem.replace("-", " ").title()


def _page_type(rel, data):
    if rel == "README.md":
        return "home"
    declared = data.get("type")
    if declared in TYPE_LABELS:
        return declared
    return frontmatter.expected_type(rel) or "explanation"


def _strings(value):
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item]
    return []


def _string(value):
    return value if isinstance(value, str) and value.strip() else None


def load_page(root, path):
    rel = path.relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8")
    try:
        data, body = frontmatter.split(text)
    except frontmatter.FrontMatterError:
        # A malformed block is a repository defect the checks report; the
        # reader still gets the page rather than an error.
        data, body = {}, text
    # Raw HTML is disabled in the renderer, so a comment (the generated-file
    # notice) would otherwise print as text.
    body = HTML_COMMENT_RE.sub("", body).lstrip("\n")
    screen = _string(data.get("screen"))
    return {
        "path": rel,
        "type": _page_type(rel, data),
        "title": _string(data.get("title")) or _title_from_markdown(path, body),
        "description": _string(data.get("description")) or "",
        "lang": data.get("lang") if data.get("lang") in frontmatter.LANGUAGES else "en",
        "persona": data.get("persona") if data.get("persona") in PERSONA_LABELS else None,
        "app": _string(data.get("app")),
        "journey": _string(data.get("journey")),
        "tour": _string(data.get("tour")),
        "generated": data.get("generated") is True,
        "source": _strings(data.get("source")),
        "models": _strings(data.get("models")),
        "actions": _strings(data.get("actions")),
        "screen": screen if screen and screen.startswith("/") else None,
        "translation_of": _string(data.get("translation_of")),
        "front_matter": data,
        "body": body,
        "text": text,
    }


RECORD_KEYS = (
    "path", "type", "title", "description", "lang", "persona", "app", "journey",
    "tour", "generated", "source", "models", "actions", "screen", "translation_of",
)


def records(root):
    """Every page's front matter, in the guide's reading order."""
    result = []
    for path in sorted(root.rglob("*.md")):
        page = load_page(root, path)
        result.append({key: page[key] for key in RECORD_KEYS})
    result.sort(key=lambda record: (SECTION_ORDER.get(record["type"], 99), record["title"].lower()))
    return result


def navigation(page_records, current_path=None):
    """The sidebar: sections in Diátaxis order, pages grouped by application.

    A section whose pages declare no ``app`` is one flat group; otherwise
    pages are grouped under their application, alphabetically, with the
    pages that declare none under "Everything else" at the end.
    """
    sections = []
    for key, label in SECTIONS:
        pages = [record for record in page_records if record["type"] == key]
        if not pages:
            continue
        apps = sorted({record["app"] for record in pages if record["app"]}, key=str.lower)
        groups = []
        if apps:
            for app in apps:
                groups.append({"app": app, "pages": [record for record in pages if record["app"] == app]})
            rest = [record for record in pages if not record["app"]]
            if rest:
                groups.append({"app": "Everything else", "pages": rest})
        else:
            groups.append({"app": None, "pages": pages})
        for group in groups:
            group["pages"] = [
                {
                    "title": record["title"],
                    "href": page_url(record["path"]),
                    "lang": record["lang"],
                    "active": record["path"] == current_path,
                    "search": f"{label} {group['app'] or ''} {record['title']} {record['description']}".lower(),
                }
                for record in group["pages"]
            ]
        sections.append({"key": key, "label": label if key != "home" else "", "groups": groups})
    return sections


def translations(page_records, page):
    """The other-language versions of ``page``: ``[{lang, label, href, title}]``."""
    paths = {page["path"]}
    if page.get("translation_of"):
        paths.add(page["translation_of"])
    others = [
        record for record in page_records
        if record["path"] != page["path"]
        and (record["path"] in paths or record.get("translation_of") in paths)
    ]
    return [
        {
            "lang": record["lang"],
            "label": LANGUAGE_LABELS.get(record["lang"], record["lang"].upper()),
            "href": page_url(record["path"]),
            "title": record["title"],
        }
        for record in others
    ]


def page_for_context(page_records, model=None, action=None, lang=None):
    """The page the Help menu should open for a screen, or ``None``.

    A page matches when it names the screen's action (``actions``, by path or
    XML id; ``action`` may be one key or several) or the model of the records
    on it (``models``). Action matches win over model matches, how-to guides
    win over other types, and the reader's language wins over the other one.
    """
    actions = {key for key in ([action] if isinstance(action, str) else (action or [])) if key}
    candidates = []
    for record in page_records:
        if record["type"] == "home":
            continue
        rank = None
        if actions and actions & set(record["actions"]):
            rank = 0
        elif model and model in record["models"]:
            rank = 1
        if rank is None:
            continue
        candidates.append((rank, CONTEXT_PRIORITY.get(record["type"], 9), 0 if lang and record["lang"] == lang else 1, record["title"].lower(), record))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[:4])[0][4]


# --- Markdown -----------------------------------------------------------


def _slug(text):
    slug = re.sub(r"[^\w]+", "-", text.lower(), flags=re.UNICODE).strip("-")
    return slug or "section"


def _prepare_rendered_tokens(current_doc):
    current_dir = posixpath.dirname(current_doc)

    def rewrite(target_path):
        target = posixpath.normpath(posixpath.join(current_dir, unquote(target_path)))
        if target == ".." or target.startswith("../"):
            return None
        return f"{DOCS_ROUTE}/{quote(target, safe='/')}"

    def prepare(state):
        slug_counts = {}
        for index, token in enumerate(state.tokens):
            if token.type == "heading_open":
                title = state.tokens[index + 1].content
                base_slug = _slug(title)
                slug_counts[base_slug] = slug_counts.get(base_slug, 0) + 1
                suffix = f"-{slug_counts[base_slug]}" if slug_counts[base_slug] > 1 else ""
                token.attrSet("id", f"{base_slug}{suffix}")
            if token.type != "inline" or not token.children:
                continue
            for child in token.children:
                if child.type not in {"link_open", "image"}:
                    continue
                attribute = "href" if child.type == "link_open" else "src"
                href = (child.attrGet(attribute) or "").strip()
                if not href or href.startswith("#"):
                    continue
                parsed = urlsplit(href)
                if parsed.scheme:
                    if child.type == "image" or parsed.scheme.lower() not in {"http", "https", "mailto"}:
                        child.attrSet(attribute, "")
                    continue
                if href.startswith("//") or parsed.path.startswith("/"):
                    child.attrSet(attribute, "")
                    continue
                rewritten = rewrite(parsed.path)
                if rewritten is None:
                    child.attrSet(attribute, "")
                    continue
                if child.type == "link_open":
                    if parsed.query:
                        rewritten += f"?{quote(parsed.query, safe='=&')}"
                    if parsed.fragment:
                        rewritten += f"#{quote(unquote(parsed.fragment), safe='-._~')}"
                child.attrSet(attribute, rewritten)

    return prepare


def render_markdown(markdown_text, current_doc="README.md"):
    """Render committed Markdown to sanitized HTML with in-guide links."""
    # Raw HTML is disabled even though the repository documentation is trusted.
    # Odoo's sanitizer remains a second boundary for generated links and attrs.
    renderer = MarkdownIt("commonmark", {"html": False}).enable("table")
    renderer.core.ruler.after("inline", "usl_docs_prepare", _prepare_rendered_tokens(current_doc))
    rendered = renderer.render(markdown_text or "")
    return Markup(str(html_sanitize(rendered, sanitize_attributes=True, sanitize_style=True, strip_style=True) or ""))


def render_inline(text):
    """One sentence (bold, code, links) through the same sanitizer as pages."""
    rendered = str(render_markdown(text or "")).strip()
    if rendered.startswith("<p>") and rendered.endswith("</p>") and rendered.count("<p>") == 1:
        rendered = rendered[3:-4]
    return Markup(rendered)


def headings(body):
    """``[(level, slug, title)]`` for the page outline, the way the renderer slugs them."""
    result = []
    counts = {}
    for line in body.splitlines():
        match = re.match(r"^(#{2,3})\s+(.+?)\s*$", line)
        if not match:
            continue
        title = match.group(2).strip()
        base = _slug(title)
        counts[base] = counts.get(base, 0) + 1
        slug = base if counts[base] == 1 else f"{base}-{counts[base]}"
        result.append((len(match.group(1)), slug, re.sub(r"[*_`]", "", title)))
    return result


# --- Evidence -----------------------------------------------------------


@functools.lru_cache(maxsize=4)
def _evidence_from_file(path, mtime):
    del mtime  # part of the cache key only
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_evidence(env=None):
    """Return the docs evidence shipped with the running release, or ``None``.

    A file named by ``USL_DOCS_EVIDENCE_PATH`` wins (development stacks and
    the CI database job). Then ``USL_DOCS_EVIDENCE_JSON``, which the deployment
    injects into the service from the release manifest's
    ``qualification.docs_evidence``. Then the ``usl.docs.evidence`` parameter
    (a database-side stamp). Then a whole release manifest in
    ``USL_RELEASE_MANIFEST_JSON``. A build with none of them is unverified,
    and the pill says so.
    """
    path = os.environ.get(EVIDENCE_ENV_VAR)
    if path:
        try:
            mtime = os.stat(path).st_mtime_ns
        except OSError:
            mtime = None
        if mtime is not None:
            evidence = _evidence_from_file(path, mtime)
            if isinstance(evidence, dict):
                return evidence
    injected = os.environ.get(EVIDENCE_JSON_ENV_VAR)
    if injected:
        try:
            evidence = json.loads(injected)
        except ValueError:
            evidence = None
        if isinstance(evidence, dict):
            return evidence
    if env is not None:
        stored = env["ir.config_parameter"].sudo().get_str(EVIDENCE_PARAMETER) or ""
        if stored:
            try:
                evidence = json.loads(stored)
            except ValueError:
                evidence = None
            if isinstance(evidence, dict):
                return evidence
    raw = os.environ.get(RELEASE_MANIFEST_ENV_VAR)
    if raw:
        try:
            manifest = json.loads(raw)
        except ValueError:
            return None
        qualification = manifest.get("qualification") if isinstance(manifest, dict) else None
        evidence = qualification.get("docs_evidence") if isinstance(qualification, dict) else None
        if isinstance(evidence, dict):
            return evidence
    return None


def parse_timestamp(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def relative_age(then, now=None):
    """Say how long ago ``then`` was, the way a person would."""
    now = now or datetime.now(timezone.utc)
    seconds = max(0, int((now - then).total_seconds()))
    if seconds < 45:
        return "just now"
    minutes = seconds // 60
    if minutes < 2:
        return "a minute ago"
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    if hours < 2:
        return "an hour ago"
    if hours < 24:
        return f"{hours} hours ago"
    days = hours // 24
    if days < 2:
        return "a day ago"
    if days < 14:
        return f"{days} days ago"
    weeks = days // 7
    if weeks < 9:
        return f"{weeks} weeks ago"
    months = days // 30
    if months < 12:
        return f"{months} months ago"
    return f"{days // 365} years ago" if days >= 730 else "a year ago"


def release_commit(env):
    """Return the running release's commit when it is trusted, else ``None``."""
    values = (
        env["ir.config_parameter"].sudo().get_str("usl.release.commit") or "",
        os.environ.get("USL_RELEASE_COMMIT") or "",
    )
    for value in values:
        value = value.strip().lower()
        if RELEASE_COMMIT_RE.fullmatch(value):
            return value
    return None


def evidence_state(page, evidence, commit, now=None):
    """Describe what the evidence says about one page.

    Returns a dict with ``kind`` (``tested``, ``generated``, ``not-test-backed``,
    ``unverified``, ``untested-release``, ``recovery``), a short ``label``,
    an optional ``detail`` and, for a tested page, ``record`` (the in-guide
    test record URL) and ``run_url`` (the CI run, which GitHub forgets after
    its retention).
    """
    if page["type"] == "home":
        return None
    if not page.get("journey"):
        if page.get("generated"):
            return {"kind": "generated", "label": "Generated from the code", "detail": "This page is derived from the product's own definitions; it cannot drift from them."}
        return {"kind": "not-test-backed", "label": "Not test-backed", "detail": "Written by hand; no browser journey proves it yet."}
    if not evidence:
        return {"kind": "unverified", "label": "Unverified build", "detail": "No test evidence shipped with this build."}
    if evidence.get("mode") == "recovery":
        return {"kind": "recovery", "label": "Recovery build", "detail": "This release was rebuilt from a recovery tag without a qualification run."}
    journeys = evidence.get("journeys") if isinstance(evidence.get("journeys"), dict) else {}
    entry = journeys.get(page["journey"])
    if not isinstance(entry, dict) or entry.get("status") != "success":
        return {"kind": "untested-release", "label": "Not tested in this release", "detail": "The page's journey did not run in the qualification of this release."}
    tested_at = parse_timestamp(entry.get("finished") or evidence.get("tested_at"))
    label = f"Last tested {relative_age(tested_at, now)}" if tested_at else "Tested"
    detail = ""
    qualified = str(evidence.get("qualified_commit") or "").lower()
    adopted = str(evidence.get("release_commit") or "").lower()
    if commit and qualified and commit not in {qualified, adopted}:
        detail = "The evidence was produced for a different commit than the running release."
    return {
        "kind": "tested",
        "label": label,
        "detail": detail,
        "tested_at": tested_at.isoformat() if tested_at else "",
        "record": f"{DOCS_ROUTE}/evidence/{quote(page['journey'], safe='')}",
        "run_url": evidence.get("run_url") if isinstance(evidence.get("run_url"), str) else "",
    }


# --- The test record ----------------------------------------------------


def load_sidecar(root, page_record):
    """The committed journey facts beside a generated page, or ``None``."""
    if not page_record:
        return None
    page = Path(page_record["path"])
    directory = page.parent / page.stem if page.name != "TUTORIAL.md" else Path("tutorial") / page_record["journey"]
    sidecar = resolve_inside(root, (directory / "journey.json").as_posix())
    if not sidecar:
        return None
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("schema") != SIDECAR_SCHEMA:
        return None
    payload["_directory"] = directory.as_posix()
    return payload


def github(commit, path, line=None):
    if not path:
        return ""
    ref = commit or "19-usl-staging"
    url = f"{REPOSITORY_URL}/blob/{ref}/{quote(path, safe='/')}"
    return f"{url}#L{int(line)}" if line else url


def source_url(path, commit):
    return github(commit, path)


def short_sha(value):
    value = str(value or "")
    return value[:12] if RELEASE_COMMIT_RE.fullmatch(value) else value


def test_record(journey, entry, evidence, state, page_record, sidecar, commit):
    """Everything the test record page shows, as plain data.

    Built from the committed sidecar (what the journey *is*) and the
    release's evidence (what happened when it ran). Every link points at
    something real: the commit and the run on GitHub, the tour and the test
    at that commit, the screens the journey captured.
    """
    sidecar = sidecar or {}
    qualified = str(evidence.get("qualified_commit") or "")
    tested_at = parse_timestamp(entry.get("finished") or evidence.get("tested_at"))
    started = parse_timestamp(entry.get("started"))
    finished = parse_timestamp(entry.get("finished"))
    run_url = evidence.get("run_url") if isinstance(evidence.get("run_url"), str) else ""
    module = ""
    if sidecar.get("source_test", "").startswith("odoo.addons."):
        module = sidecar["source_test"].split(".")[2]
    test_file = sidecar.get("source_test_file") or {}
    tour_file = sidecar.get("source_tour_file") or {}
    matches = {shot.get("step"): shot for shot in entry.get("screenshots", []) if isinstance(shot, dict)}
    steps = []
    for step in sidecar.get("steps", []):
        shot = step.get("screenshot")
        line = (tour_file.get("steps") or {}).get(step.get("id"))
        capture = None
        if shot:
            match = matches.get(step.get("id"), {}).get("match")
            capture = {
                "src": f"{DOCS_ROUTE}/{quote(sidecar['_directory'] + '/' + shot, safe='/')}",
                "file": shot,
                "sha256": step.get("sha256") or "",
                "verdict": {
                    "exact": "identical to the published screen",
                    "tolerance": "within tolerance of the published screen",
                }.get(match, "published copy"),
                "alt": step.get("text", ""),
            }
        steps.append({
            "id": step.get("id", ""),
            "html": render_inline(step.get("text", "")),
            "text": step.get("text", ""),
            "assertion": step.get("assertion") or "",
            "trigger": step.get("trigger") or "",
            "run": step.get("run") or "",
            "tour_url": github(qualified, tour_file.get("path"), line),
            "tour_line": line,
            "capture": capture,
        })
    replay = f"USL_DOCS_JOURNEYS=1 make docs-journeys MODULE={module}" if module else "USL_DOCS_JOURNEYS=1 make docs-journeys"
    return {
        "journey": journey,
        "title": page_record["title"] if page_record else journey,
        "page_href": page_url(page_record["path"]) if page_record else DOCS_ROUTE,
        "state": state,
        "tested_at": tested_at.isoformat(timespec="seconds") if tested_at else "",
        "duration": f"{int((finished - started).total_seconds())} s" if started and finished else "unknown",
        "commit": qualified,
        "commit_short": short_sha(qualified) or "unknown",
        "commit_url": f"{REPOSITORY_URL}/commit/{qualified}" if RELEASE_COMMIT_RE.fullmatch(qualified) else "",
        "commit_differs": bool(commit and qualified and commit not in {qualified, str(evidence.get("release_commit") or "")}),
        "run_url": run_url,
        "run_id": str(evidence.get("workflow_run_id", "") or ""),
        "tour": sidecar.get("tour") or entry.get("tour", ""),
        "tour_url": github(qualified, tour_file.get("path"), tour_file.get("line")),
        "test": sidecar.get("source_test") or entry.get("source_test", ""),
        "test_url": github(qualified, test_file.get("path"), test_file.get("line")),
        "login": sidecar.get("login", "") or "a fixture user",
        "viewport": ", ".join(str(v) for v in entry.get("viewports", [])) + (f" ({sidecar['viewport_size']})" if sidecar.get("viewport_size") else ""),
        "browser": str(evidence.get("chromium", "")) or "unknown",
        "steps": steps,
        "replay": replay,
        "raw_evidence": json.dumps(entry, indent=2, sort_keys=True, ensure_ascii=False),
        "raw_sidecar": json.dumps({k: v for k, v in sidecar.items() if k != "_directory"}, indent=2, sort_keys=True, ensure_ascii=False),
    }


# --- What agents read ---------------------------------------------------


def render_llms_txt(page_records, base_url=""):
    """Index the guide for an agent, per llmstxt.org, with absolute page URLs."""
    lines = [
        "# USL User Guide",
        "",
        "> The user guide of this USL Odoo release. Every page carries front matter",
        "> (title, type, description, persona, app, journey) and says whether a browser",
        "> journey proves it. Add `?format=md` to any page URL for its Markdown,",
        "> `?format=json` for its facts, `index.json` for the whole guide as data, or",
        "> `llms-full.txt` for every page in one document.",
        "",
    ]
    grouped = {}
    for record in page_records:
        grouped.setdefault(record["type"], []).append(record)
    for key, label in SECTIONS:
        if key == "home":
            continue
        entries = grouped.get(key)
        if not entries:
            continue
        lines.extend([f"## {'Optional' if key == 'explanation' else label}", ""])
        for record in entries:
            url = f"{page_url(record['path'], base_url)}?format=md"
            description = record["description"] or ""
            lines.append(f"- [{record['title']}]({url}): {description}".rstrip(": "))
        lines.append("")
    return "\n".join(lines)


def render_llms_full(root, page_records, base_url=""):
    """Every page's committed Markdown in one document, in reading order."""
    parts = [render_llms_txt(page_records, base_url)]
    for record in page_records:
        if record["type"] == "home":
            continue
        path = resolve_inside(root, record["path"])
        if not path:
            continue
        parts.append(f"\n\n---\n\n<!-- {page_url(record['path'], base_url)} -->\n\n{path.read_text(encoding='utf-8').strip()}\n")
    return "".join(parts)


def _record_payload(record, state, base_url=""):
    payload = {key: record[key] for key in RECORD_KEYS}
    payload["url"] = page_url(record["path"], base_url)
    payload["markdown_url"] = f"{payload['url']}?format=md"
    payload["screen_url"] = f"{base_url}{record['screen']}" if record["screen"] else None
    payload["evidence"] = state and {
        "kind": state["kind"],
        "label": state["label"],
        "tested_at": state.get("tested_at") or None,
        "record_url": f"{base_url}{state['record']}" if state.get("record") else None,
    }
    return payload


def index_json(page_records, evidence, commit, base_url="", now=None):
    """The whole guide as data for an agent or a script."""
    return {
        "schema": INDEX_SCHEMA,
        "route": DOCS_ROUTE,
        "release_commit": commit,
        "pages": [
            _record_payload(record, evidence_state(record, evidence, commit, now), base_url)
            for record in page_records if record["type"] != "home"
        ],
    }


def page_json(page, state, sidecar, base_url=""):
    """One page as data: its facts, its Markdown and, when generated, its steps."""
    payload = _record_payload(page, state, base_url)
    payload["schema"] = PAGE_SCHEMA
    payload["markdown"] = page["text"]
    payload["headings"] = [{"level": level, "id": slug, "title": title} for level, slug, title in headings(page["body"])]
    if sidecar:
        payload["steps"] = [
            {
                "id": step.get("id"),
                "text": step.get("text"),
                "assertion": step.get("assertion"),
                "screenshot_url": f"{base_url}{DOCS_ROUTE}/{quote(sidecar['_directory'] + '/' + step['screenshot'], safe='/')}" if step.get("screenshot") else None,
            }
            for step in sidecar.get("steps", [])
        ]
    return payload


_WORD_RE = re.compile(r"[\w'’-]+", re.UNICODE)


def search_pages(root, query, lang=None, limit=10, base_url=""):
    """Rank pages for a free-text query; titles count most, then descriptions, then bodies."""
    terms = [term.lower() for term in _WORD_RE.findall(query or "") if len(term) > 1]
    if not terms:
        return []
    scored = []
    for path in sorted(root.rglob("*.md")):
        page = load_page(root, path)
        if page["type"] == "home" or (lang and page["lang"] != lang):
            continue
        title = page["title"].lower()
        description = page["description"].lower()
        body = page["body"].lower()
        score = 0
        matched = 0
        for term in terms:
            hit = False
            if term in title:
                score += 8
                hit = True
            if term in description:
                score += 4
                hit = True
            count = body.count(term)
            if count:
                score += min(count, 10)
                hit = True
            matched += hit
        if matched == 0:
            continue
        score += 5 * matched
        snippet = ""
        for term in terms:
            index = body.find(term)
            if index >= 0:
                start = max(0, index - 80)
                snippet = re.sub(r"\s+", " ", page["body"][start:index + 120]).strip()
                break
        entry = {key: page[key] for key in ("path", "title", "type", "description", "lang", "persona", "app")}
        entry.update({"url": page_url(page["path"], base_url), "score": score, "snippet": snippet})
        scored.append(entry)
    scored.sort(key=lambda entry: (-entry["score"], entry["title"].lower()))
    return scored[:limit]
