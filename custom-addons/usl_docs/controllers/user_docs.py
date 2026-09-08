"""Serve the repository's user documentation inside the product.

``docs/users`` travels with every release (the distribution image copies it and
development stacks bind-mount it), so the guide a reader opens from the Help
menu always describes the release they are using. Pages are Markdown with a
small front matter block; the front matter decides where a page sits in the
guide, who it is for, and whether a test proves it.

Three readers share this controller:

- a person, who gets a rendered HTML page with the guide's navigation;
- an agent, who asks for ``text/markdown`` (or the ``.md`` path) and gets the
  page exactly as it is committed, plus ``llms.txt`` as an index;
- the evidence pill on each page, which reads the docs evidence shipped with
  the release to say when a page's journey last passed and where the proof is.
"""

from __future__ import annotations

import functools
import html
import json
import os
import posixpath
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from markdown_it import MarkdownIt

from odoo import http
from odoo.http import request
from odoo.tools import html_sanitize

from ..tools import frontmatter

DOCS_ROUTE = "/usl/user-docs"
DOCS_ENV_VAR = "USL_USER_DOCS_PATH"
EVIDENCE_ENV_VAR = "USL_DOCS_EVIDENCE_PATH"
RELEASE_MANIFEST_ENV_VAR = "USL_RELEASE_MANIFEST_JSON"
EVIDENCE_PARAMETER = "usl.docs.evidence"
READER_GROUP = "base.group_user"
REPOSITORY_URL = "https://github.com/unstaticlabs/odoo"
RELEASE_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->\s*", re.DOTALL)

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
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


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


def _docs_root():
    for root in _candidate_roots():
        if (root / "README.md").is_file():
            return root.resolve()
    return None


def _resolve_inside(root, requested_path):
    """Return the file ``requested_path`` names inside ``root``, or ``None``."""
    candidate = (root / requested_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def _safe_doc_path(root, requested_path):
    if not requested_path:
        requested_path = "README.md"
    requested_path = requested_path.strip("/")
    if requested_path in {"", "index", "index.html"}:
        requested_path = "README.md"
    if requested_path.endswith("/"):
        requested_path += "README.md"
    if not requested_path.endswith(".md"):
        requested_path += ".md"
    return _resolve_inside(root, requested_path)


def _safe_asset_path(root, requested_path):
    requested_path = (requested_path or "").strip("/")
    if Path(requested_path).suffix.lower() not in IMAGE_TYPES:
        return None
    return _resolve_inside(root, requested_path)


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


def _load_page(root, path):
    rel = path.relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8")
    try:
        data, body = frontmatter.split(text)
    except frontmatter.FrontMatterError:
        # A malformed block is a repository defect the checks report; the
        # reader still gets the page rather than an error.
        data, body = {}, text
    # Raw HTML is disabled in the renderer, so a comment (the generated-file
    # notice on the indexes) would otherwise print as text.
    body = HTML_COMMENT_RE.sub("", body).lstrip("\n")
    title = data.get("title") if isinstance(data.get("title"), str) else None
    return {
        "path": rel,
        "type": _page_type(rel, data),
        "title": title or _title_from_markdown(path, body),
        "description": data.get("description") if isinstance(data.get("description"), str) else "",
        "lang": data.get("lang") if data.get("lang") in frontmatter.LANGUAGES else "en",
        "persona": data.get("persona") if data.get("persona") in PERSONA_LABELS else None,
        "journey": data.get("journey") if isinstance(data.get("journey"), str) else None,
        "generated": data.get("generated") is True,
        "source": [item for item in data.get("source", []) if isinstance(item, str)]
        if isinstance(data.get("source"), list) else [],
        "front_matter": data,
        "body": body,
        "text": text,
    }


def _doc_records(root):
    records = []
    for path in sorted(root.rglob("*.md")):
        page = _load_page(root, path)
        records.append({key: page[key] for key in ("path", "type", "title", "description", "lang", "persona", "journey", "generated")})
    records.sort(key=lambda record: (SECTION_ORDER.get(record["type"], 99), record["title"].lower()))
    return records


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
                suffix = (
                    f"-{slug_counts[base_slug]}"
                    if slug_counts[base_slug] > 1
                    else ""
                )
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
    # Raw HTML is disabled even though the repository documentation is trusted.
    # Odoo's sanitizer remains a second boundary for generated links and attrs.
    renderer = MarkdownIt("commonmark", {"html": False}).enable("table")
    renderer.core.ruler.after(
        "inline",
        "usl_user_docs_prepare",
        _prepare_rendered_tokens(current_doc),
    )
    rendered = renderer.render(markdown_text)
    return str(
        html_sanitize(
            rendered,
            sanitize_attributes=True,
            sanitize_style=True,
            strip_style=True,
        ),
    )


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
    the CI database job). Then the ``usl.docs.evidence`` parameter, which the
    release stamping writes into the database at deploy. Then the release
    manifest the stack may inject as ``USL_RELEASE_MANIFEST_JSON``. A build
    with none of them is unverified, and the pill says so.
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
        evidence = manifest.get("docs_evidence") if isinstance(manifest, dict) else None
        if isinstance(evidence, dict):
            return evidence
    return None


def _parse_timestamp(value):
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
    if seconds < 90:
        return "just now"
    minutes = seconds // 60
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


def _release_commit(env):
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


def evidence_state(page, evidence, release_commit, now=None):
    """Describe what the evidence says about one page.

    Returns a dict with ``kind`` (``tested``, ``not-test-backed``,
    ``unverified``, ``untested-release``, ``recovery``), a short ``label``,
    an optional ``detail`` and, for a tested page, ``proof`` (an in-guide URL)
    and ``run_url`` (the CI run, which GitHub forgets after its retention).
    """
    if page["type"] == "home":
        return None
    if not page.get("journey"):
        return {"kind": "not-test-backed", "label": "Not test-backed", "detail": ""}
    if not evidence:
        return {"kind": "unverified", "label": "Unverified build", "detail": "No test evidence shipped with this build."}
    if evidence.get("mode") == "recovery":
        return {"kind": "recovery", "label": "Recovery build", "detail": "This release was rebuilt from a recovery tag without a qualification run."}
    journeys = evidence.get("journeys") if isinstance(evidence.get("journeys"), dict) else {}
    entry = journeys.get(page["journey"])
    if not isinstance(entry, dict) or entry.get("status") != "success":
        return {"kind": "untested-release", "label": "Not tested in this release", "detail": "The page's journey did not run in the qualification of this release."}
    tested_at = _parse_timestamp(entry.get("finished") or evidence.get("tested_at"))
    label = "Tested"
    if tested_at:
        label = f"Last tested {relative_age(tested_at, now)}"
    detail = ""
    qualified = str(evidence.get("qualified_commit") or "").lower()
    if release_commit and qualified and qualified != release_commit:
        detail = "The evidence was produced for a different commit than the running release."
    return {
        "kind": "tested",
        "label": label,
        "detail": detail,
        "tested_at": tested_at.isoformat() if tested_at else "",
        "proof": f"{DOCS_ROUTE}/evidence/{quote(page['journey'], safe='')}",
        "run_url": evidence.get("run_url") if isinstance(evidence.get("run_url"), str) else "",
    }


# --- llms.txt -----------------------------------------------------------


def render_llms_txt(records, base_url=""):
    """Index the guide for an agent, per llmstxt.org, with absolute page URLs.

    The repository keeps its own ``docs/llms.txt`` with repository paths; this
    one is derived from the same front matter at request time so the product
    never ships a second copy that could drift.
    """
    lines = [
        "# USL User Guide",
        "",
        "> The user guide of this USL Odoo release. Every page carries front matter",
        "> (title, type, description, persona, journey) and says whether a browser",
        "> journey proves it. Add `?format=md` to any page URL for its Markdown.",
        "",
    ]
    grouped = {}
    for record in records:
        grouped.setdefault(record["type"], []).append(record)
    for key, label in SECTIONS:
        if key == "home":
            continue
        entries = grouped.get(key)
        if not entries:
            continue
        lines.extend([f"## {'Optional' if key == 'explanation' else label}", ""])
        for record in entries:
            url = f"{base_url}{DOCS_ROUTE}/{quote(record['path'], safe='/')}?format=md"
            description = record["description"] or ""
            lines.append(f"- [{record['title']}]({url}): {description}".rstrip(": "))
        lines.append("")
    return "\n".join(lines)


# --- HTML ---------------------------------------------------------------


def _source_url(path, release_commit):
    ref = release_commit or "19-usl-staging"
    return f"{REPOSITORY_URL}/blob/{ref}/{quote(path, safe='/')}"


def _nav_html(records, current_path):
    sections = []
    for key, label in SECTIONS:
        links = []
        for record in records:
            if record["type"] != key:
                continue
            badge = ""
            if record["lang"] != "en":
                badge = f'<span class="doc-lang">{html.escape(record["lang"].upper())}</span>'
            links.append(
                '<a class="doc-link{active}" data-title="{search}" href="{href}">{title}{badge}</a>'.format(
                    active=" active" if record["path"] == current_path else "",
                    search=html.escape(f"{label} {record['title']}".lower(), quote=True),
                    href=html.escape(f"{DOCS_ROUTE}/{record['path']}", quote=True),
                    title=html.escape(record["title"]),
                    badge=badge,
                ),
            )
        if not links:
            continue
        heading = "" if key == "home" else f'<div class="doc-section">{html.escape(label)}</div>'
        sections.append(f'<div class="doc-group" data-section="{html.escape(key)}">{heading}{"".join(links)}</div>')
    return "\n".join(sections)


def _header_html(page, state, release_commit):
    if page["type"] == "home":
        return ""
    pills = [f'<span class="pill pill-type">{html.escape(TYPE_LABELS.get(page["type"], page["type"]))}</span>']
    if page["persona"]:
        pills.append(f'<span class="pill">{html.escape(PERSONA_LABELS[page["persona"]])}</span>')
    if page["lang"] != "en":
        pills.append(f'<span class="pill">{html.escape(page["lang"].upper())}</span>')
    if state:
        title = html.escape(state.get("detail") or "", quote=True)
        if state["kind"] == "tested":
            pills.append(
                f'<a class="pill pill-evidence pill-{state["kind"]}" href="{html.escape(state["proof"], quote=True)}" title="{title}">'
                f'{html.escape(state["label"])} · proof</a>',
            )
        else:
            pills.append(f'<span class="pill pill-evidence pill-{state["kind"]}" title="{title}">{html.escape(state["label"])}</span>')
    sources = ""
    if page["source"]:
        links = ", ".join(
            f'<a href="{html.escape(_source_url(path, release_commit), quote=True)}" rel="noopener">{html.escape(posixpath.basename(path))}</a>'
            for path in page["source"]
        )
        sources = f'<div class="doc-sources">Source: {links}</div>'
    return f'<div class="doc-header"><div class="doc-pills">{"".join(pills)}</div>{sources}</div>'


def _footer_html(page):
    if page["type"] == "home":
        return ""
    if page["generated"]:
        what = f"the journey <code>{html.escape(page['journey'])}</code>" if page["journey"] else "its source"
        return (
            f'<div class="doc-footer">This page is generated from {what}. '
            f"Change the test, then run <code>make docs</code>; edits to the page itself are overwritten.</div>"
        )
    return (
        f'<div class="doc-footer">This page is written by hand: <code>docs/users/{html.escape(page["path"])}</code> '
        f"in the repository.</div>"
    )


def _page_html(page, body_html, records, state, release_commit):
    nav = _nav_html(records, page["path"])
    header = _header_html(page, state, release_commit)
    footer = _footer_html(page)
    description = html.escape(page["description"], quote=True)
    markdown_href = html.escape(f"{DOCS_ROUTE}/{page['path']}?format=md", quote=True)
    return f"""<!doctype html>
<html lang="{html.escape(page["lang"])}">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <meta name="description" content="{description}"/>
  <link rel="alternate" type="text/markdown" href="{markdown_href}"/>
  <title>{html.escape(page["title"])} - USL User Guide</title>
  <style>
    :root {{
      --bg: #F5F7F9;
      --panel: #FFFFFF;
      --text: #17212B;
      --muted: #5F6B76;
      --border: #D8DEE4;
      --accent: #714B67;
      --accent-wash: #F1EBF0;
      --ok: #1F7A4D;
      --ok-wash: #E6F4EC;
      --warn: #8A6D1F;
      --warn-wash: #FBF3DC;
      --code: #F5F7F9;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font: 15px/1.55 Roboto, system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--text); background: var(--bg); }}
    .layout {{ display: grid; grid-template-columns: minmax(260px, 300px) minmax(0, 1fr); min-height: 100vh; }}
    aside {{ border-right: 1px solid var(--border); background: var(--panel); padding: 18px; position: sticky; top: 0; height: 100vh; overflow: auto; }}
    main {{ max-width: 920px; width: 100%; padding: 32px 42px 64px; }}
    .brand {{ font-weight: 700; font-size: 17px; }}
    .subtitle {{ color: var(--muted); font-size: 13px; margin: 2px 0 14px; }}
    input[type="search"] {{ width: 100%; padding: 9px 12px; border: 1px solid var(--border); border-radius: 6px; font: inherit; }}
    nav {{ margin-top: 14px; }}
    .doc-section {{ margin: 16px 0 4px; font-size: 11px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); }}
    .doc-link {{ display: flex; align-items: center; gap: 8px; color: var(--text); text-decoration: none; padding: 6px 8px; border-radius: 6px; }}
    .doc-link:hover, .doc-link.active {{ background: var(--accent-wash); color: var(--accent); }}
    .doc-lang {{ font-size: 10px; font-weight: 700; color: var(--muted); border: 1px solid var(--border); border-radius: 4px; padding: 0 4px; }}
    .source-note {{ color: var(--muted); font-size: 12px; margin-top: 18px; }}
    .doc-header {{ display: grid; gap: 8px; margin-bottom: 20px; }}
    .doc-pills {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .pill {{ display: inline-flex; align-items: center; font-size: 12px; font-weight: 600; padding: 2px 9px; border-radius: 999px; background: var(--code); color: var(--muted); text-decoration: none; border: 1px solid transparent; }}
    .pill-type {{ background: var(--accent-wash); color: var(--accent); }}
    .pill-tested {{ background: var(--ok-wash); color: var(--ok); }}
    a.pill-tested:hover {{ border-color: var(--ok); }}
    .pill-unverified, .pill-untested-release, .pill-recovery {{ background: var(--warn-wash); color: var(--warn); }}
    .doc-sources {{ color: var(--muted); font-size: 13px; }}
    .doc-footer {{ margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--border); color: var(--muted); font-size: 13px; }}
    h1 {{ font-size: 30px; line-height: 1.2; margin: 0 0 16px; }}
    h2 {{ margin-top: 34px; border-top: 1px solid var(--border); padding-top: 22px; }}
    h3, h4 {{ margin-top: 26px; }}
    a {{ color: var(--accent); }}
    img {{ max-width: 100%; height: auto; border: 1px solid var(--border); border-radius: 6px; margin: 8px 0 4px; }}
    blockquote {{ margin: 18px 0; padding: 8px 16px; border-left: 4px solid var(--accent); color: var(--muted); background: var(--accent-wash); }}
    hr {{ border: 0; border-top: 1px solid var(--border); margin: 28px 0; }}
    code {{ background: var(--code); border-radius: 4px; padding: 1px 4px; font-size: 13px; }}
    pre {{ background: #17212B; color: #F5F7F9; border-radius: 8px; padding: 14px; overflow: auto; }}
    pre code {{ background: transparent; padding: 0; color: inherit; }}
    table {{ border-collapse: collapse; width: 100%; margin: 16px 0; background: var(--panel); }}
    th, td {{ border: 1px solid var(--border); padding: 8px 10px; text-align: left; vertical-align: top; }}
    th {{ background: var(--code); }}
    ul, ol {{ padding-left: 24px; }}
    li {{ margin: 4px 0; }}
    li > ul, li > ol {{ margin: 4px 0 8px; }}
    .doc-nav-title {{ display: none; }}
    @media (max-width: 820px) {{
      .layout {{ grid-template-columns: 1fr; }}
      aside {{ position: static; height: auto; order: 2; border-right: 0; border-top: 1px solid var(--border); }}
      main {{ padding: 24px 20px 40px; }}
      .brand, .subtitle {{ display: none; }}
      .doc-nav-title {{ display: block; font-weight: 700; margin-bottom: 10px; }}
    }}
  </style>
</head>
<body>
  <div class="layout">
    <aside>
      <div class="brand">USL User Guide</div>
      <div class="subtitle">Built-in guide for this release.</div>
      <div class="doc-nav-title">All pages</div>
      <input id="doc-search" type="search" placeholder="Search the guide" aria-label="Search the guide"/>
      <nav id="doc-nav">{nav}</nav>
      <div class="source-note">Pages marked as tested are generated from the product's own browser tests.</div>
    </aside>
    <main>
      {header}
      {body_html}
      {footer}
    </main>
  </div>
  <script>
    document.getElementById('doc-search').addEventListener('input', (event) => {{
      const value = event.target.value.toLowerCase().trim();
      for (const link of document.querySelectorAll('.doc-link')) {{
        link.style.display = !value || link.dataset.title.includes(value) ? '' : 'none';
      }}
      for (const group of document.querySelectorAll('.doc-group')) {{
        const visible = Array.from(group.querySelectorAll('.doc-link')).some((link) => link.style.display !== 'none');
        group.style.display = visible ? '' : 'none';
      }}
    }});
  </script>
</body>
</html>"""


def _wants_markdown():
    """An agent asks for the committed Markdown; a browser gets HTML."""
    if request.params.get("format") == "md":
        return True
    accept = request.httprequest.headers.get("Accept", "")
    first = accept.split(",")[0].split(";")[0].strip().lower()
    return first == "text/markdown"


def _evidence_page_html(journey, entry, evidence, state, page_record):
    """The proof behind a page's pill: run, commit, timings and captures."""
    rows = "".join(
        "<tr><td>{step}</td><td>{viewport}</td><td><code>{file}</code></td><td>{match}</td></tr>".format(
            step=html.escape(str(shot.get("step", ""))),
            viewport=html.escape(str(shot.get("viewport", ""))),
            file=html.escape(str(shot.get("file", ""))),
            match={"exact": "matches the published screenshot", "tolerance": "within tolerance of the published screenshot"}.get(
                shot.get("match"), "not published",
            ),
        )
        for shot in entry.get("screenshots", []) if isinstance(shot, dict)
    )
    started = _parse_timestamp(entry.get("started"))
    finished = _parse_timestamp(entry.get("finished"))
    duration = ""
    if started and finished:
        duration = f"{int((finished - started).total_seconds())} s"
    run_url = evidence.get("run_url") if isinstance(evidence.get("run_url"), str) else ""
    run_link = (
        f'<a href="{html.escape(run_url, quote=True)}" rel="noopener">GitHub run {html.escape(str(evidence.get("workflow_run_id", "")))}</a>'
        " (GitHub keeps a run for a limited time; this page is the durable record)"
        if run_url else "no run link"
    )
    back = f'{DOCS_ROUTE}/{quote(page_record["path"], safe="/")}' if page_record else DOCS_ROUTE
    facts = [
        ("Page", f'<a href="{html.escape(back, quote=True)}">{html.escape(page_record["title"] if page_record else journey)}</a>'),
        ("Journey", f"<code>{html.escape(journey)}</code>"),
        ("Tour", f"<code>{html.escape(str(entry.get('tour', '')))}</code>"),
        ("Test", f"<code>{html.escape(str(entry.get('source_test', '')))}</code>"),
        ("Last passed", html.escape(state["label"]) + (f" ({html.escape(state['tested_at'])})" if state.get("tested_at") else "")),
        ("Duration", html.escape(duration or "unknown")),
        ("Viewports", html.escape(", ".join(str(v) for v in entry.get("viewports", [])))),
        ("Qualified commit", f"<code>{html.escape(str(evidence.get('qualified_commit', '')))}</code>"),
        ("Browser", html.escape(str(evidence.get("chromium", "")) or "unknown")),
        ("Qualification", run_link),
    ]
    facts_html = "".join(f"<tr><th>{label}</th><td>{value}</td></tr>" for label, value in facts)
    body = (
        f"<h1>Proof for “{html.escape(page_record['title'] if page_record else journey)}”</h1>"
        "<p>This page was generated from a browser journey that the qualification of the running release "
        "replayed end to end. What follows is that run.</p>"
        f"<table>{facts_html}</table>"
        "<h2>Screens captured</h2>"
        f"<table><thead><tr><th>Step</th><th>Viewport</th><th>File</th><th>Published copy</th></tr></thead><tbody>{rows}</tbody></table>"
        f'<p><a href="{html.escape(back, quote=True)}">Back to the page</a></p>'
    )
    return body


class UserDocsController(http.Controller):
    @http.route(DOCS_ROUTE + "/evidence/<string:journey>", type="http", auth="user")
    def user_docs_evidence(self, journey, **kwargs):
        if not request.env.user.has_group(READER_GROUP):
            return request.not_found()
        root = _docs_root()
        if not root:
            return request.not_found()
        evidence = load_evidence(request.env)
        journeys = evidence.get("journeys") if isinstance(evidence, dict) and isinstance(evidence.get("journeys"), dict) else {}
        entry = journeys.get(journey)
        if not isinstance(entry, dict):
            return request.not_found()
        records = _doc_records(root)
        page_record = next((record for record in records if record["journey"] == journey), None)
        release_commit = _release_commit(request.env)
        state = evidence_state({"type": "how-to", "journey": journey}, evidence, release_commit)
        page = {
            "path": f"evidence/{journey}",
            "type": "home",
            "title": f"Proof: {page_record['title'] if page_record else journey}",
            "description": "",
            "lang": page_record["lang"] if page_record else "en",
            "persona": None,
            "journey": journey,
            "generated": False,
            "source": [],
        }
        body_html = _evidence_page_html(journey, entry, evidence, state, page_record)
        return request.make_response(
            _page_html(page, body_html, records, None, release_commit),
            headers=[("Content-Type", "text/html; charset=utf-8")],
        )

    @http.route([DOCS_ROUTE, DOCS_ROUTE + "/", DOCS_ROUTE + "/<path:doc_path>"], type="http", auth="user")
    def user_docs(self, doc_path=None, **kwargs):
        if not request.env.user.has_group(READER_GROUP):
            return request.not_found()
        root = _docs_root()
        if not root:
            return request.make_response(
                "USL user documentation is not available. Configure USL_USER_DOCS_PATH or mount docs/users.",
                headers=[("Content-Type", "text/plain; charset=utf-8")],
                status=503,
            )
        if doc_path and doc_path.strip("/") == "llms.txt":
            return request.make_response(
                render_llms_txt(_doc_records(root), request.httprequest.host_url.rstrip("/")),
                headers=[("Content-Type", "text/markdown; charset=utf-8")],
            )
        asset = _safe_asset_path(root, doc_path)
        if asset:
            return request.make_response(
                asset.read_bytes(),
                headers=[
                    ("Content-Type", IMAGE_TYPES[asset.suffix.lower()]),
                    ("Cache-Control", "private, max-age=3600"),
                ],
            )
        path = _safe_doc_path(root, doc_path)
        if not path:
            return request.not_found()
        page = _load_page(root, path)
        if _wants_markdown():
            return request.make_response(
                page["text"],
                headers=[("Content-Type", "text/markdown; charset=utf-8")],
            )
        release_commit = _release_commit(request.env)
        state = evidence_state(page, load_evidence(request.env), release_commit)
        body_html = render_markdown(page["body"], page["path"])
        return request.make_response(
            _page_html(page, body_html, _doc_records(root), state, release_commit),
            headers=[("Content-Type", "text/html; charset=utf-8")],
        )
