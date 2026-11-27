"""Read one project's Markdown files from a read-only team vault checkout."""

from collections import defaultdict
from contextlib import ExitStack
from hashlib import sha256
import os
from pathlib import PurePosixPath
import posixpath
import re
import stat
from urllib.parse import quote, unquote, urlencode, urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token


_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_MAX_NOTE_BYTES = 4 * 1024 * 1024
_WIKILINK = re.compile(r"!\[\[([^\]\n]+)\]\]|\[\[([^\]\n]+)\]\]")


def _directory(stack, name, parent=None):
    fd = os.open(name, _DIRECTORY, dir_fd=parent)
    stack.callback(os.close, fd)
    return fd


def _notes(project_fd):
    paths = []
    for base, dirs, files, directory in os.fwalk(".", dir_fd=project_fd):
        dirs[:] = [name for name in dirs if not name.startswith(".")]
        for name in files:
            if name.startswith(".") or not name.lower().endswith(".md"):
                continue
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISREG(info.st_mode):
                paths.append(str(PurePosixPath(base, name)))
    return sorted(paths, key=lambda path: (path.casefold(), path))


def _read(project_fd, path):
    parts = PurePosixPath(path).parts
    with ExitStack() as stack:
        directory = project_fd
        for name in parts[:-1]:
            directory = _directory(stack, name, directory)
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        stream = stack.enter_context(os.fdopen(descriptor, "rb"))
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise OSError
        source = stream.read(_MAX_NOTE_BYTES + 1)
        return source if len(source) <= _MAX_NOTE_BYTES else None


def _text(value):
    token = Token("text", "", 0)
    token.content = value
    return token


def _render(source, active, paths, folder):
    """Only links to this project become navigable notes. Rendering never fetches an image."""
    by_name = defaultdict(list)
    for path in paths:
        by_name[PurePosixPath(path).name.casefold()].append(path)

    def resolve(raw):
        name = unquote(raw.split("#", 1)[0])
        if not name:
            return active
        from_vault_root = name.startswith("Projects/")
        if from_vault_root:
            if not name.startswith(folder + "/"):
                return None
            name = name[len(folder) + 1:]
        if name.startswith("/") or "\\" in name or re.match(r"^[A-Za-z][A-Za-z\d+.\-]*:", name):
            return None
        if not name.lower().endswith(".md"):
            name += ".md"
        if from_vault_root:
            return name if name in paths else None
        for candidate in (posixpath.normpath(posixpath.join(posixpath.dirname(active), name)), name):
            if candidate in paths:
                return candidate
        matches = by_name[name.casefold()] if "/" not in name else []
        return matches[0] if len(matches) == 1 else None

    def note_link(path):
        token = Token("link_open", "a", 1)
        token.attrSet("href", "#" + quote(path, safe="/"))
        token.attrSet("data-project-note", path)
        return token

    def wiki(text):
        end = 0
        for match in _WIKILINK.finditer(text):
            yield _text(text[end:match.start()])
            target, _, alias = (match.group(1) or match.group(2)).partition("|")
            path = resolve(target.strip()) if match.group(2) else None
            if path:
                yield note_link(path)
            yield _text((alias or target).strip())
            if path:
                yield Token("link_close", "a", -1)
            end = match.end()
        yield _text(text[end:])

    def scope_links(state):
        for block in state.tokens:
            if block.type != "inline" or not block.children:
                continue
            tokens, link = [], None
            for child in block.children:
                if child.type == "link_open":
                    href = child.attrGet("href") or ""
                    parsed = urlsplit(href)
                    if parsed.scheme.lower() in {"http", "https", "mailto"}:
                        link = child
                        child.attrSet("rel", "noreferrer")
                    else:
                        path = resolve(href) if not href.startswith("//") else None
                        link = note_link(path) if path else False
                    if link:
                        tokens.append(link)
                elif child.type == "link_close":
                    if link:
                        tokens.append(child)
                    link = None
                elif child.type == "image":
                    tokens.append(_text(child.content))
                elif child.type == "text" and link is None:
                    tokens.extend(wiki(child.content))
                else:
                    tokens.append(child)
            block.children = tokens

    lines = source.splitlines(keepends=True)
    if lines and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], 1):
            if line.strip() in {"---", "..."}:
                source = "".join(lines[i+1:])
                break
    markdown = MarkdownIt("commonmark", {"html": False}).enable("table")
    markdown.core.ruler.after("inline", "project_note_links", scope_links)
    return markdown.render(source)


def read_project_notes(project_id, selected, database):
    root = os.environ.get("USL_PROJECT_NOTES_VAULT")
    configured_database = os.environ.get("USL_PROJECT_NOTES_DATABASE") or os.environ.get("ODOO_DB_NAME")
    if not root or configured_database != database:
        return {"status": "not_configured"}
    if not os.path.isabs(root):
        return {"status": "unavailable"}
    try:
        with ExitStack() as stack:
            root_fd = _directory(stack, root)
            try:
                projects = _directory(stack, "Projects", root_fd)
                project_fd = _directory(stack, str(project_id), projects)
            except FileNotFoundError:
                return {"status": "empty"}
            paths = _notes(project_fd)
            if not paths:
                return {"status": "empty"}
            entries = [{"path": path, "title": str(PurePosixPath(path).with_suffix(""))} for path in paths]
            active = selected if selected is not None else next(
                (p for p in paths if p.casefold() in {"readme.md", "index.md"}), paths[0],
            )
            if active not in paths:
                return {"status": "missing", "notes": entries}
            source = _read(project_fd, active)
    except (OSError, UnicodeError):
        return {"status": "unavailable"}
    vault = os.environ.get("USL_PROJECT_NOTES_NAME") or os.path.basename(root.rstrip("/"))
    result = {"notes": entries, "path": active, "obsidian_url": "obsidian://open?" + urlencode({
        "vault": vault, "file": f"Projects/{project_id}/{active}",
    }, quote_via=quote)}
    if source is None:
        return dict(result, status="too_large")
    try:
        body = source.decode("utf-8-sig")
    except UnicodeDecodeError:
        return dict(result, status="unreadable")
    return dict(result, status="ready", html=_render(body, active, set(paths), f"Projects/{project_id}"),
                digest=sha256(source).hexdigest())
