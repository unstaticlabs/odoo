import html
import os
import posixpath
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from markdown_it import MarkdownIt

from odoo import http
from odoo.http import request
from odoo.tools import html_sanitize

DOCS_ROUTE = "/usl/user-docs"
DOCS_ENV_VAR = "USL_USER_DOCS_PATH"
ACCOUNTING_READONLY_GROUP = "account.group_account_readonly"


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
    candidate = (root / requested_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def _title_from_markdown(path, text):
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "User Guide" if path.name == "README.md" else path.stem.replace("-", " ").title()


def _doc_records(root):
    records = []
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        records.append({
            "path": rel,
            "section": "Home" if rel == "README.md" else rel.split("/", 1)[0].replace("-", " ").title(),
            "title": _title_from_markdown(path, text),
        })
    return records


def _slug(text):
    slug = re.sub(r"[^\w]+", "-", text.lower(), flags=re.UNICODE).strip("-")
    return slug or "section"


def _prepare_rendered_tokens(current_doc):
    current_dir = posixpath.dirname(current_doc)

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
                if child.type != "link_open":
                    continue
                href = (child.attrGet("href") or "").strip()
                if not href or href.startswith("#"):
                    continue
                parsed = urlsplit(href)
                if parsed.scheme:
                    if parsed.scheme.lower() not in {"http", "https", "mailto"}:
                        child.attrSet("href", "")
                    continue
                if href.startswith("//") or parsed.path.startswith("/"):
                    child.attrSet("href", "")
                    continue

                target = posixpath.normpath(
                    posixpath.join(current_dir, unquote(parsed.path)),
                )
                if target == ".." or target.startswith("../"):
                    child.attrSet("href", "")
                    continue
                rewritten = f"{DOCS_ROUTE}/{quote(target, safe='/')}"
                if parsed.query:
                    rewritten += f"?{quote(parsed.query, safe='=&')}"
                if parsed.fragment:
                    rewritten += f"#{quote(unquote(parsed.fragment), safe='-._~')}"
                child.attrSet("href", rewritten)

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


def _page_html(root, doc_path, title, body_html, records):
    nav = "\n".join(
        '<a class="doc-link" data-title="{search}" data-section="{section}" href="{href}">{title}</a>'.format(
            search=html.escape(f"{record['section']} {record['title']}".lower(), quote=True),
            section=html.escape(record["section"], quote=True),
            href=html.escape(f"{DOCS_ROUTE}/{record['path']}", quote=True),
            title=html.escape(record["title"]),
        )
        for record in records
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{html.escape(title)} - USL Odoo User Guide</title>
  <style>
    :root {{
      --bg: #f7f8fa;
      --panel: #ffffff;
      --text: #20242a;
      --muted: #5f6875;
      --border: #d9dee7;
      --accent: #017e84;
      --code: #f1f4f8;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font: 15px/1.55 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--text); background: var(--bg); }}
    .layout {{ display: grid; grid-template-columns: minmax(260px, 320px) minmax(0, 1fr); min-height: 100vh; }}
    aside {{ border-right: 1px solid var(--border); background: var(--panel); padding: 18px; position: sticky; top: 0; height: 100vh; overflow: auto; }}
    main {{ max-width: 980px; width: 100%; padding: 36px 42px 64px; }}
    .brand {{ font-weight: 700; font-size: 18px; margin-bottom: 4px; }}
    .subtitle {{ color: var(--muted); font-size: 13px; margin-bottom: 16px; }}
    input[type="search"] {{ width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 6px; font: inherit; }}
    nav {{ margin-top: 16px; display: grid; gap: 4px; }}
    .doc-link {{ color: var(--text); text-decoration: none; padding: 7px 8px; border-radius: 6px; }}
    .doc-link:hover, .doc-link.active {{ background: #e9f5f6; color: var(--accent); }}
    .source-note {{ color: var(--muted); font-size: 12px; margin-top: 18px; }}
    h1 {{ font-size: 32px; line-height: 1.2; margin: 0 0 18px; }}
    h2 {{ margin-top: 34px; border-top: 1px solid var(--border); padding-top: 22px; }}
    h3, h4 {{ margin-top: 26px; }}
    a {{ color: var(--accent); }}
    blockquote {{ margin: 18px 0; padding: 8px 16px; border-left: 4px solid var(--accent); color: var(--muted); background: #eef7f7; }}
    hr {{ border: 0; border-top: 1px solid var(--border); margin: 28px 0; }}
    code {{ background: var(--code); border-radius: 4px; padding: 1px 4px; }}
    pre {{ background: #111827; color: #f9fafb; border-radius: 8px; padding: 14px; overflow: auto; }}
    pre code {{ background: transparent; padding: 0; color: inherit; }}
    table {{ border-collapse: collapse; width: 100%; margin: 16px 0; background: var(--panel); }}
    th, td {{ border: 1px solid var(--border); padding: 8px 10px; text-align: left; vertical-align: top; }}
    th {{ background: #eef2f7; }}
    ul, ol {{ padding-left: 24px; }}
    li {{ margin: 4px 0; }}
    li > ul, li > ol {{ margin: 4px 0 8px; }}
    @media (max-width: 820px) {{
      .layout {{ grid-template-columns: 1fr; }}
      aside {{ position: static; height: auto; }}
      main {{ padding: 26px 20px 48px; }}
    }}
  </style>
</head>
<body>
  <div class="layout">
    <aside>
      <div class="brand">USL Odoo User Guide</div>
      <div class="subtitle">Accounting help for CEOs, accountants and finance operators.</div>
      <input id="doc-search" type="search" placeholder="Search the guide" aria-label="Search the guide"/>
      <nav id="doc-nav">{nav}</nav>
      <div class="source-note">Built-in guide for the active Accounting product.</div>
    </aside>
    <main>
      {body_html}
    </main>
  </div>
  <script>
    const currentPath = window.location.pathname;
    for (const link of document.querySelectorAll('.doc-link')) {{
      if (link.pathname === currentPath || (currentPath.endsWith('/user-docs') && link.pathname.endsWith('/README.md'))) {{
        link.classList.add('active');
      }}
    }}
    document.getElementById('doc-search').addEventListener('input', (event) => {{
      const value = event.target.value.toLowerCase().trim();
      for (const link of document.querySelectorAll('.doc-link')) {{
        link.style.display = !value || link.dataset.title.includes(value) ? '' : 'none';
      }}
    }});
  </script>
</body>
</html>"""


class RebuildAccountUserDocsController(http.Controller):
    @http.route([DOCS_ROUTE, DOCS_ROUTE + "/", DOCS_ROUTE + "/<path:doc_path>"], type="http", auth="user")
    def user_docs(self, doc_path=None, **kwargs):
        if not request.env.user.has_group(ACCOUNTING_READONLY_GROUP):
            return request.not_found()
        root = _docs_root()
        if not root:
            return request.make_response(
                "USL user documentation is not available. Configure USL_USER_DOCS_PATH or mount docs/users.",
                headers=[("Content-Type", "text/plain; charset=utf-8")],
                status=503,
            )
        path = _safe_doc_path(root, doc_path)
        if not path:
            return request.not_found()
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        title = _title_from_markdown(path, text)
        body_html = render_markdown(text, rel)
        return request.make_response(
            _page_html(root, rel, title, body_html, _doc_records(root)),
            headers=[("Content-Type", "text/html; charset=utf-8")],
        )
