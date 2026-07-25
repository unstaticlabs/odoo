import html
import os
import re
from pathlib import Path

from odoo import http
from odoo.http import request


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
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "section"


def _render_inline(text, current_doc):
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)

    def link(match):
        label = match.group(1)
        target = html.unescape(match.group(2))
        if target.startswith(("http://", "https://", "mailto:", "#")):
            href = target
        else:
            base = Path(current_doc).parent
            doc_target, fragment = (target.split("#", 1) + [""])[:2] if "#" in target else (target, "")
            normalized = (base / doc_target).as_posix()
            fragment_suffix = f"#{fragment}" if fragment else ""
            href = f"{DOCS_ROUTE}/{normalized}{fragment_suffix}"
        return f'<a href="{html.escape(href, quote=True)}">{label}</a>'

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, escaped)


def _render_table(lines, current_doc):
    header = [cell.strip() for cell in lines[0].strip("|").split("|")]
    body = lines[2:]
    output = ["<table>", "<thead><tr>"]
    output.extend(f"<th>{_render_inline(cell, current_doc)}</th>" for cell in header)
    output.append("</tr></thead><tbody>")
    for line in body:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        output.append("<tr>")
        output.extend(f"<td>{_render_inline(cell, current_doc)}</td>" for cell in cells)
        output.append("</tr>")
    output.append("</tbody></table>")
    return "\n".join(output)


def render_markdown(markdown, current_doc="README.md"):
    lines = markdown.splitlines()
    output = []
    in_code = False
    in_ul = False
    in_ol = False
    code_lines = []
    paragraph = []
    i = 0

    def flush_paragraph():
        if paragraph:
            output.append(f"<p>{_render_inline(' '.join(paragraph), current_doc)}</p>")
            paragraph.clear()

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            output.append("</ul>")
            in_ul = False
        if in_ol:
            output.append("</ol>")
            in_ol = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                output.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
                code_lines = []
                in_code = False
            else:
                flush_paragraph()
                close_lists()
                in_code = True
            i += 1
            continue
        if in_code:
            code_lines.append(line)
            i += 1
            continue
        if stripped == "":
            flush_paragraph()
            close_lists()
            i += 1
            continue
        if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", lines[i + 1]):
            flush_paragraph()
            close_lists()
            table_lines = [line, lines[i + 1]]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            output.append(_render_table(table_lines, current_doc))
            continue
        heading = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            close_lists()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            output.append(f'<h{level} id="{_slug(title)}">{_render_inline(title, current_doc)}</h{level}>')
            i += 1
            continue
        bullet = re.match(r"^-\s+(.+)$", stripped)
        if bullet:
            flush_paragraph()
            if in_ol:
                output.append("</ol>")
                in_ol = False
            if not in_ul:
                output.append("<ul>")
                in_ul = True
            output.append(f"<li>{_render_inline(bullet.group(1), current_doc)}</li>")
            i += 1
            continue
        numbered = re.match(r"^\d+\.\s+(.+)$", stripped)
        if numbered:
            flush_paragraph()
            if in_ul:
                output.append("</ul>")
                in_ul = False
            if not in_ol:
                output.append("<ol>")
                in_ol = True
            output.append(f"<li>{_render_inline(numbered.group(1), current_doc)}</li>")
            i += 1
            continue
        paragraph.append(stripped)
        i += 1

    if in_code:
        output.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
    flush_paragraph()
    close_lists()
    return "\n".join(output)


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
    code {{ background: var(--code); border-radius: 4px; padding: 1px 4px; }}
    pre {{ background: #111827; color: #f9fafb; border-radius: 8px; padding: 14px; overflow: auto; }}
    pre code {{ background: transparent; padding: 0; color: inherit; }}
    table {{ border-collapse: collapse; width: 100%; margin: 16px 0; background: var(--panel); }}
    th, td {{ border: 1px solid var(--border); padding: 8px 10px; text-align: left; vertical-align: top; }}
    th {{ background: #eef2f7; }}
    li {{ margin: 4px 0; }}
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
