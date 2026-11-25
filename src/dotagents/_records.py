"""Shared frontmatter-Markdown record store, SQLite query engine, and pluggable providers.

Every record is a Markdown file with YAML frontmatter for structured fields/attributes
and Markdown context after the frontmatter:

- Simple record: ``<root>/<status>/<name>.md`` (or ``<root>/<name>.md`` when flat)
- Complex record: ``<root>/<status>/<name>/README.md`` + sibling ``<phase|extra>.md``

No ``INDEX.md`` is written or maintained. Queries run through an in-memory,
read-only SQLite table built on demand from the folder's frontmatter and bodies.

Standard library only; Python 3.9 or later.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:  # pragma: no cover
        tomllib = None  # type: ignore[assignment]

_LOGGER = logging.getLogger("dotagents.records")

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_EMPTY_FRONTMATTER_RE = re.compile(r"\A---\r?\n---[ \t]*(?:\r?\n|\Z)")
_YAML_INDICATORS = frozenset("-?:,[]{}#&*!|>'\"%@`")
_YAML_WORDS = frozenset(("true", "false", "yes", "no", "on", "off", "null", "~"))
_YAML_BREAKS = {"\x85": "\\u0085", "\u2028": "\\u2028", "\u2029": "\\u2029"}

_IGNORED_NAMES = frozenset(("index.md",))
_NON_RECORD_DIRS = frozenset((".git", "__pycache__", "reviews", "archive"))


def fail(message: str) -> SystemExit:
    """Standard CLI error exit."""
    return SystemExit("error: " + message)


def today_iso() -> str:
    """Today's date in ISO-8601 format."""
    return datetime.date.today().isoformat()


def _default_file_mode() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return 0o666 & ~mask


def write_text_lf(path: "str | os.PathLike[str]", text: str, *, atomic: bool = False) -> Path:
    """Write ``text`` as UTF-8 with LF line endings (Python 3.9+ safe)."""
    given = Path(path)
    if not atomic:
        given.parent.mkdir(parents=True, exist_ok=True)
        with open(given, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return given
    target = Path(os.path.realpath(str(given)))
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        if target.exists():
            shutil.copymode(str(target), tmp_name)
        elif os.name != "nt":
            os.chmod(tmp_name, _default_file_mode())
        os.replace(tmp_name, str(target))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return given


def yaml_scalar(value: Any) -> str:
    """Render a scalar value as safe YAML (bare when unambiguous, else JSON-quoted)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(yaml_scalar(v) for v in value) + "]"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    text = str(value)
    plain = (
        text == text.strip()
        and text[:1] not in _YAML_INDICATORS
        and ": " not in text
        and " #" not in text
        and not text.endswith(":")
        and text.lower() not in _YAML_WORDS
        and not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", text)
        and not any(ord(c) < 32 or c == "\x7f" or c in _YAML_BREAKS for c in text)
    )
    if plain and text:
        return text
    quoted = json.dumps(text, ensure_ascii=False)
    for char, escape in _YAML_BREAKS.items():
        quoted = quoted.replace(char, escape)
    return quoted


def yaml_value(raw: str, *, typed: bool = False) -> Any:
    """Parse a YAML frontmatter scalar or inline list."""
    raw = raw.strip()
    if not raw:
        return ""
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        try:
            decoded = json.loads(raw)
            return decoded if isinstance(decoded, str) or typed else raw
        except ValueError:
            return raw
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        inner = raw[1:-1]
        if "'" not in inner.replace("''", ""):
            return inner.replace("''", "'")
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except ValueError:
            pass
        items = []
        for part in inner.split(","):
            part = part.strip()
            if part:
                items.append(yaml_value(part, typed=typed))
        return items
    if typed:
        low = raw.lower()
        if low in ("null", "~"):
            return None
        if low == "true":
            return True
        if low == "false":
            return False
        if re.fullmatch(r"-?(?:0|[1-9][0-9]*)", raw):
            return int(raw)
        if re.fullmatch(r"-?(?:0|[1-9][0-9]*)\.[0-9]+", raw):
            return float(raw)
    return raw


def parse_frontmatter(text: str, *, typed: bool = False) -> "tuple[dict[str, Any], str, bool]":
    """Parse YAML frontmatter from ``text`` -> ``(meta, body, had_frontmatter)``."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        if _EMPTY_FRONTMATTER_RE.match(text):
            end = _EMPTY_FRONTMATTER_RE.match(text).end()  # type: ignore[union-attr]
            return {}, text[end:].lstrip("\r\n"), True
        return {}, text, False
    meta: "dict[str, Any]" = {}
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        key, sep, rest = line.partition(":")
        if not sep or not key.strip():
            i += 1
            continue
        k = key.strip()
        val_raw = rest.strip()
        if not val_raw:
            # Check for indented block list (`  - item` or `- item`)
            items: "list[Any]" = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                nxt_stripped = nxt.strip()
                if not nxt_stripped:
                    j += 1
                    continue
                if re.match(r"^\s*-\s+", nxt):
                    item_raw = re.sub(r"^\s*-\s+", "", nxt, count=1)
                    items.append(yaml_value(item_raw, typed=typed))
                    j += 1
                    continue
                break
            if items:
                meta[k] = items
                i = j
                continue
            meta[k] = ""
        else:
            meta[k] = yaml_value(val_raw, typed=typed)
        i += 1
    body = text[m.end():].lstrip("\r\n")
    return meta, body, True


def render_frontmatter(
    meta: "dict[str, Any]",
    body: str,
    *,
    key_order: "tuple[str, ...]" = (),
) -> str:
    """Render ``meta`` as YAML frontmatter followed by ``body`` (LF-only)."""
    lines = ["---"]
    written: "set[str]" = set()

    def emit(key: str, value: Any) -> None:
        if value is None or value == "" or value == []:
            return
        if isinstance(value, list) and any(isinstance(v, str) and len(v) > 40 for v in value):
            lines.append("%s:" % key)
            for item in value:
                lines.append("  - %s" % yaml_scalar(item))
        else:
            lines.append("%s: %s" % (key, yaml_scalar(value)))
        written.add(key)

    for key in key_order:
        if key in meta:
            emit(key, meta[key])
    for key, value in meta.items():
        if key not in written:
            emit(key, value)
    lines.append("---")
    clean_body = body.lstrip("\r\n").rstrip("\r\n")
    return "\n".join(lines) + "\n\n" + (clean_body + "\n" if clean_body else "")


def requote_frontmatter(text: str) -> str:
    """Requote bare frontmatter scalars that YAML would misread, keeping body intact."""
    shift = 1 if text.startswith("\ufeff") else 0
    m = _FRONTMATTER_RE.match(text[shift:])
    if not m:
        return text
    start, end = m.start(1) + shift, m.end(1) + shift
    out = []
    for line in text[start:end].splitlines(True):
        ln = line.rstrip("\r\n")
        key, sep, value = ln.partition(":")
        bare = value.strip()
        if sep and key.strip() and bare and yaml_value(bare) == bare and yaml_scalar(bare) != bare:
            ln = "%s: %s" % (key, yaml_scalar(bare))
        out.append(ln + line[len(line.rstrip("\r\n")):])
    block = "".join(out)
    return text[:start] + block + text[end:] if block != text[start:end] else text


def coerce_attr_value(raw: str) -> Any:
    """Parse CLI ``key=value`` strings (lists, ints, bools, null, or text)."""
    stripped = raw.strip()
    if stripped.lower() == "true":
        return True
    if stripped.lower() == "false":
        return False
    if stripped.lower() == "null":
        return None
    if re.fullmatch(r"-?(?:0|[1-9][0-9]*)", stripped):
        return int(stripped)
    if re.fullmatch(r"-?(?:0|[1-9][0-9]*)\.[0-9]+", stripped):
        return float(stripped)
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return parsed
        except ValueError:
            pass
        inner = stripped[1:-1].strip()
        return [p.strip() for p in inner.split(",") if p.strip()] if inner else []
    return raw


def slugify_name(text: str, *, sep: str = "-", max_len: int = 60) -> str:
    """Derive a safe filename slug from free text."""
    pat = r"[^a-z0-9]+"
    slug = re.sub(pat, sep, text.lower()).strip(sep)
    if max_len > 0 and len(slug) > max_len:
        slug = slug[:max_len].rstrip(sep)
    return slug


class Record:
    """One Markdown record with YAML frontmatter and optional sibling files."""

    def __init__(
        self,
        path: Path,
        meta: "dict[str, Any]",
        body: str,
        *,
        root: Optional[Path] = None,
        extras: "Optional[list[Path]]" = None,
        key_order: "tuple[str, ...]" = (),
    ):
        self.path = Path(path)
        self.meta = dict(meta)
        self.body = body
        self.root = Path(root) if root else None
        self.extras = list(extras) if extras else []
        self.key_order = key_order

    @property
    def is_complex(self) -> bool:
        """True when the record is a directory's ``README.md``."""
        return self.path.name.lower() == "readme.md"

    @property
    def container_dir(self) -> Path:
        """Directory holding a complex record (or parent directory of a file record)."""
        return self.path.parent

    @property
    def name(self) -> str:
        if self.meta.get("name"):
            return str(self.meta["name"])
        if self.is_complex:
            parent_name = self.path.parent.name
            return parent_name[:-3] if parent_name.endswith(".md") else parent_name
        return self.path.stem

    @property
    def status(self) -> str:
        return str(self.meta.get("status", ""))

    @property
    def title(self) -> str:
        if self.meta.get("title"):
            return str(self.meta["title"])
        for line in self.body.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
        return self.name

    @property
    def description(self) -> str:
        if self.meta.get("description"):
            return str(self.meta["description"])
        for line in self.body.splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                return s[:120]
        return self.title

    @property
    def folder(self) -> str:
        if self.root is None:
            return self.path.parent.name
        try:
            rel = self.path.parent.relative_to(self.root)
            return "" if str(rel) == "." else rel.as_posix()
        except ValueError:
            return self.path.parent.name

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        root: Optional[Path] = None,
        status_folders: "Optional[dict[str, str]]" = None,
        typed: bool = True,
        key_order: "tuple[str, ...]" = (),
    ) -> "Record":
        path = Path(path)
        text = path.read_text(encoding="utf-8-sig")
        meta, body, _ = parse_frontmatter(text, typed=typed)
        is_complex = path.name.lower() == "readme.md"
        if not meta.get("name"):
            if is_complex:
                p_name = path.parent.name
                meta["name"] = p_name[:-3] if p_name.endswith(".md") else p_name
            else:
                meta["name"] = path.stem
        if not meta.get("status") and status_folders:
            folder_to_status = {v: k for k, v in status_folders.items()}
            check_dir = path.parent.parent if is_complex else path.parent
            if check_dir.name in folder_to_status:
                meta["status"] = folder_to_status[check_dir.name]
        extras: "list[Path]" = []
        if is_complex:
            for item in sorted(path.parent.rglob("*.md")):
                if item == path or item.name.lower() in _IGNORED_NAMES:
                    continue
                if any(part in _NON_RECORD_DIRS for part in item.relative_to(path.parent).parts):
                    continue
                extras.append(item)
        return cls(path, meta, body, root=root, extras=extras, key_order=key_order)

    def render(self) -> str:
        return render_frontmatter(self.meta, self.body, key_order=self.key_order)

    def save(self, *, atomic: bool = False) -> None:
        write_text_lf(self.path, self.render(), atomic=atomic)

    def section_range(self, heading: str) -> "tuple[int, int]":
        """Line indices ``(after_heading, next_h2)`` in ``self.body.splitlines()``."""
        lines = self.body.splitlines()
        start = -1
        for i, line in enumerate(lines):
            if line.startswith("## "):
                if start >= 0:
                    return start, i
                if line[3:].strip().lower() == heading.strip().lower():
                    start = i + 1
        return (start, len(lines)) if start >= 0 else (-1, -1)

    def section_text(self, heading: str) -> str:
        lines = self.body.splitlines()
        start, end = self.section_range(heading)
        if start < 0:
            raise fail("%s has no `## %s` section" % (self.name, heading))
        header = lines[start - 1]
        return "\n".join([header] + lines[start:end]).rstrip() + "\n"

    def set_section(self, heading: str, content: str) -> None:
        lines = self.body.splitlines()
        start, end = self.section_range(heading)
        new_lines = [""] + content.strip("\r\n").splitlines() + [""]
        if start < 0:
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend(["## " + heading] + new_lines)
        else:
            lines[start:end] = new_lines
        self.body = "\n".join(lines).rstrip("\n") + "\n"

    def append_to_section(self, heading: str, text: str) -> None:
        lines = self.body.splitlines()
        start, end = self.section_range(heading)
        if start < 0:
            self.set_section(heading, text)
            return
        at = end
        while at > start and not lines[at - 1].strip():
            at -= 1
        lines.insert(at, text.rstrip("\r\n"))
        self.body = "\n".join(lines).rstrip("\n") + "\n"

    def replace_in_body(self, old: str, new: str) -> int:
        count = self.body.count(old)
        if count == 0:
            raise fail("%s: %r not found in body" % (self.name, old))
        self.body = self.body.replace(old, new)
        return count

    def as_dict(self, *, include_body: bool = True) -> "dict[str, Any]":
        out: "dict[str, Any]" = dict(self.meta)
        out["name"] = self.name
        out["status"] = self.status
        out["path"] = str(self.path)
        out["folder"] = self.folder
        out["is_complex"] = self.is_complex
        out["extras"] = [str(p.relative_to(self.path.parent)) for p in self.extras]
        if include_body:
            out["body"] = self.body
        return out


# --------------------------------------------------------------------------- #
# SQL-like query engine over folder records (read-only SQLite :memory:)
# --------------------------------------------------------------------------- #

_SAFE_COL_RE = re.compile(r"[^a-zA-Z0-9_]")
_SQL_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
}


def _sql_col(name: str) -> str:
    col = _SAFE_COL_RE.sub("_", name.strip().lower()).strip("_")
    if not col or col[0].isdigit():
        col = "col_" + col
    return col


def _sql_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float, str)):
        return value
    return json.dumps(value, ensure_ascii=False)


def _authorizer(action, arg1, arg2, db_name, trigger_name):
    if action in _SQL_ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and (arg1 or "").lower() == "query_only":
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _build_query_db(
    records: "list[Record]",
    *,
    table_alias: Optional[str] = None,
    extra_fields: "Optional[callable]" = None,
) -> "tuple[sqlite3.Connection, list[str]]":
    base_cols = [
        "name", "status", "folder", "path", "is_complex", "extras_count",
        "title", "description", "created", "updated", "body",
    ]
    rows_data: "list[dict[str, Any]]" = []
    seen_cols: "dict[str, None]" = {c: None for c in base_cols}
    for rec in records:
        row: "dict[str, Any]" = {
            "name": rec.name,
            "status": rec.status,
            "folder": rec.folder,
            "path": str(rec.path),
            "is_complex": 1 if rec.is_complex else 0,
            "extras_count": len(rec.extras),
            "title": rec.title,
            "description": rec.description,
            "created": rec.meta.get("created", ""),
            "updated": rec.meta.get("updated", ""),
            "body": rec.body,
        }
        for k, v in rec.meta.items():
            col = _sql_col(str(k))
            if col not in row or row[col] in ("", None):
                row[col] = _sql_cell(v)
            seen_cols.setdefault(col, None)
        if extra_fields is not None:
            for k, v in extra_fields(rec).items():
                col = _sql_col(str(k))
                row[col] = _sql_cell(v)
                seen_cols.setdefault(col, None)
        rows_data.append(row)

    columns = list(seen_cols.keys())
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    col_defs = ", ".join('"%s"' % c for c in columns)
    conn.execute("CREATE TABLE records (%s)" % col_defs)
    if rows_data:
        placeholders = ", ".join("?" for _ in columns)
        conn.executemany(
            "INSERT INTO records (%s) VALUES (%s)" % (col_defs, placeholders),
            [[row.get(c) for c in columns] for row in rows_data],
        )
    if table_alias and _sql_col(table_alias) != "records":
        alias = _sql_col(table_alias)
        conn.execute('CREATE VIEW "%s" AS SELECT * FROM records' % alias)
    conn.commit()
    conn.execute("PRAGMA query_only = ON")
    conn.set_authorizer(_authorizer)
    return conn, columns


def query_records(
    records: "list[Record]",
    *,
    sql: Optional[str] = None,
    select: Optional[str] = None,
    where: Optional[str] = None,
    order_by: Optional[str] = None,
    group_by: Optional[str] = None,
    limit: Optional[int] = None,
    table_alias: Optional[str] = None,
    extra_fields: "Optional[callable]" = None,
) -> "tuple[list[str], list[dict[str, Any]]]":
    """Execute a read-only SQL query (raw ``sql`` or structured clauses) over ``records``."""
    conn, _ = _build_query_db(records, table_alias=table_alias, extra_fields=extra_fields)
    try:
        if sql and sql.strip():
            stmt = sql.strip().rstrip(";")
        else:
            cols = select.strip() if select and select.strip() else "name, status, folder, title, description"
            parts = ["SELECT", cols, "FROM", table_alias or "records"]
            if where and where.strip():
                parts.extend(["WHERE", where.strip()])
            if group_by and group_by.strip():
                parts.extend(["GROUP BY", group_by.strip()])
            if order_by and order_by.strip():
                parts.extend(["ORDER BY", order_by.strip()])
            if limit is not None and limit > 0:
                parts.extend(["LIMIT", str(int(limit))])
            stmt = " ".join(parts)
        try:
            cur = conn.execute(stmt)
        except sqlite3.Error as exc:
            raise fail("query failed: %s (SQL: %s)" % (exc, stmt))
        cols_out = [d[0] for d in (cur.description or [])]
        rows_out = [dict(zip(cols_out, row)) for row in cur.fetchall()]
        return cols_out, rows_out
    finally:
        conn.close()


def format_query_result(
    columns: "list[str]",
    rows: "list[dict[str, Any]]",
    fmt: str = "table",
) -> str:
    """Format query output as ``table``, ``json``, ``tsv``, or ``md``."""
    fmt = fmt.lower()
    if fmt == "json":
        return json.dumps(rows, indent=2, ensure_ascii=False) + "\n"
    if not rows:
        return "(0 rows)\n" if fmt in ("table", "md") else ""
    str_rows = [["" if row.get(c) is None else str(row.get(c)) for c in columns] for row in rows]
    if fmt == "tsv":
        lines = ["\t".join(columns)] + ["\t".join(r.replace("\t", " ").replace("\n", " ") for r in row) for row in str_rows]
        return "\n".join(lines) + "\n"
    widths = [max([len(c)] + [len(r[i].replace("\n", " ")) for r in str_rows]) for i, c in enumerate(columns)]
    if fmt == "md":
        head = "| " + " | ".join(columns) + " |"
        sep = "| " + " | ".join("---" for _ in columns) + " |"
        body = ["| " + " | ".join(cell.replace("\n", " ").replace("|", "\\|") for cell in row) + " |" for row in str_rows]
        return "\n".join([head, sep] + body) + "\n"
    # table
    head = "  ".join(c.ljust(widths[i]) for i, c in enumerate(columns))
    sep = "  ".join("-" * widths[i] for i in range(len(columns)))
    body = ["  ".join(row[i].replace("\n", " ").ljust(widths[i]) for i in range(len(columns))) for row in str_rows]
    return "\n".join([head, sep] + body) + "\n"


# --------------------------------------------------------------------------- #
# Filesystem folder store
# --------------------------------------------------------------------------- #


class FolderStore:
    """Markdown+frontmatter store for any folder (flat or status-folder layout)."""

    def __init__(
        self,
        root: Path,
        *,
        status_folders: "Optional[dict[str, str]]" = None,
        default_status: str = "active",
        key_order: "tuple[str, ...]" = (),
        table_alias: Optional[str] = None,
    ):
        self.root = Path(root)
        self.status_folders = dict(status_folders) if status_folders else None
        self.default_status = default_status
        self.key_order = key_order
        self.table_alias = table_alias

    def status_dir(self, status: str) -> Path:
        if not self.status_folders:
            return self.root
        folder = self.status_folders.get(status, status)
        return self.root / folder if folder else self.root

    def _scan_dir(self, base: Path, out: "list[Record]", *, recursive: bool = False) -> None:
        if not base.is_dir():
            return
        for entry in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if entry.name.startswith(".") or entry.name.startswith("_"):
                continue
            if entry.is_file():
                if entry.suffix.lower() != ".md" or entry.name.lower() in _IGNORED_NAMES:
                    continue
                if entry.name.lower() == "readme.md":
                    continue
                try:
                    out.append(
                        Record.load(
                            entry,
                            root=self.root,
                            status_folders=self.status_folders,
                            key_order=self.key_order,
                        )
                    )
                except UnicodeDecodeError:
                    _LOGGER.warning("skipping %s: not UTF-8", entry)
            elif entry.is_dir() and entry.name not in _NON_RECORD_DIRS:
                readme = entry / "README.md"
                if readme.is_file():
                    try:
                        out.append(
                            Record.load(
                                readme,
                                root=self.root,
                                status_folders=self.status_folders,
                                key_order=self.key_order,
                            )
                        )
                    except UnicodeDecodeError:
                        _LOGGER.warning("skipping %s: not UTF-8", readme)
                elif recursive:
                    self._scan_dir(entry, out, recursive=True)

    def all(self) -> "list[Record]":
        if not self.root.is_dir():
            return []
        found: "list[Record]" = []
        if self.status_folders:
            seen_dirs: "set[Path]" = set()
            for folder in self.status_folders.values():
                d = (self.root / folder) if folder else self.root
                if d in seen_dirs:
                    continue
                seen_dirs.add(d)
                self._scan_dir(d, found, recursive=False)
            # Also pick up any extra status folders or legacy top-level files
            if self.root not in seen_dirs:
                self._scan_dir(self.root, found, recursive=False)
            for child in sorted(self.root.iterdir(), key=lambda p: p.name.lower()):
                if child.is_dir() and child not in seen_dirs and child.name not in _NON_RECORD_DIRS and not child.name.startswith("."):
                    if not (child / "README.md").is_file():
                        self._scan_dir(child, found, recursive=False)
        else:
            self._scan_dir(self.root, found, recursive=True)
        return found

    def get(self, name: str) -> Optional[Record]:
        wanted = name[:-3] if name.endswith(".md") else name
        wanted = wanted.replace("\\", "/").strip("/")
        slug_dash = slugify_name(wanted, sep="-", max_len=0)
        slug_under = slugify_name(wanted, sep="_", max_len=0)
        for rec in self.all():
            candidates = {rec.name, rec.path.stem}
            if rec.is_complex:
                candidates.add(rec.path.parent.name)
            if wanted in candidates or slug_dash in candidates or slug_under in candidates:
                return rec
        return None

    def require(self, name: str) -> Record:
        rec = self.get(name)
        if rec is None:
            raise fail("no record %r under %s" % (name, self.root))
        return rec

    def add(
        self,
        name: str,
        *,
        meta: "Optional[dict[str, Any]]" = None,
        body: str = "",
        status: Optional[str] = None,
        complex_dir: bool = False,
    ) -> Record:
        slug = slugify_name(name, sep="-", max_len=60)
        if not slug or slug in ("readme", "index"):
            raise fail("invalid record name %r" % (name,))
        if self.get(slug):
            raise fail("record %r already exists under %s" % (slug, self.root))
        st = status or self.default_status
        rec_meta: "dict[str, Any]" = {"name": slug, "status": st, "created": today_iso()}
        if meta:
            rec_meta.update(meta)
        rec_meta["name"] = slug
        rec_meta["status"] = st
        dest_dir = self.status_dir(st)
        path = (dest_dir / slug / "README.md") if complex_dir else (dest_dir / (slug + ".md"))
        rec = Record(path, rec_meta, body, root=self.root, key_order=self.key_order)
        rec.save()
        return rec

    def set_attrs(
        self,
        name: str,
        updates: "dict[str, Any]",
        *,
        unset: "Optional[list[str]]" = None,
    ) -> Record:
        rec = self.require(name)
        for k in (unset or []):
            rec.meta.pop(k, None)
        for k, v in updates.items():
            if v is None:
                rec.meta.pop(k, None)
            else:
                rec.meta[k] = v
        rec.meta["updated"] = today_iso()
        rec.save()
        return rec

    def edit_body(
        self,
        name: str,
        *,
        body: Optional[str] = None,
        section: Optional[str] = None,
        append: Optional[str] = None,
        replace: "Optional[tuple[str, str]]" = None,
    ) -> Record:
        rec = self.require(name)
        if body is not None:
            rec.body = body
        elif section and append is not None:
            rec.append_to_section(section, append)
        elif section and replace is not None:
            sec_text = rec.section_text(section)
            old, new = replace
            if old not in sec_text:
                raise fail("%s: %r not found in section %r" % (rec.name, old, section))
            rec.set_section(section, sec_text.split("\n", 1)[1].replace(old, new))
        elif append is not None:
            rec.body = rec.body.rstrip("\r\n") + "\n\n" + append.strip("\r\n") + "\n"
        elif replace is not None:
            rec.replace_in_body(replace[0], replace[1])
        else:
            raise fail("pass --body, --append, or --replace")
        rec.meta["updated"] = today_iso()
        rec.save()
        return rec

    def move(self, name: str, new_status: str) -> Record:
        rec = self.require(name)
        new_status = new_status.strip().lower()
        rec.meta["status"] = new_status
        rec.meta["updated"] = today_iso()
        rec.save()
        dest_dir = self.status_dir(new_status)
        if rec.is_complex:
            src_dir = rec.path.parent
            dest_subdir = dest_dir / src_dir.name
            if dest_subdir != src_dir:
                legacy_dir = self.root / src_dir.name
                if dest_subdir.exists() or (new_status == self.default_status and legacy_dir != src_dir and legacy_dir.exists()):
                    existing = dest_subdir if dest_subdir.exists() else legacy_dir
                    raise fail("cannot move %r: %s already exists" % (rec.name, existing))
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src_dir), str(dest_subdir))
                rec.path = dest_subdir / rec.path.name
        else:
            dest_file = dest_dir / rec.path.name
            if dest_file != rec.path:
                legacy_file = self.root / rec.path.name
                if dest_file.exists() or (new_status == self.default_status and legacy_file != rec.path and legacy_file.exists()):
                    existing = dest_file if dest_file.exists() else legacy_file
                    raise fail("cannot move %r: %s already exists" % (rec.name, existing))
                dest_dir.mkdir(parents=True, exist_ok=True)
                rec.path.replace(dest_file)
                rec.path = dest_file
        return rec

    def remove(self, name: str, *, force: bool = False) -> Path:
        rec = self.require(name)
        if rec.status in ("done", "processed") and not force:
            raise fail("%s has status %r; pass --force to remove" % (rec.name, rec.status))
        if rec.is_complex:
            target = rec.path.parent
            if rec.extras and not force:
                raise fail("%s has extra files (%d); pass --force to remove directory" % (rec.name, len(rec.extras)))
            shutil.rmtree(target)
            return target
        path = rec.path
        path.unlink()
        return path

    def query(
        self,
        *,
        sql: Optional[str] = None,
        select: Optional[str] = None,
        where: Optional[str] = None,
        order_by: Optional[str] = None,
        group_by: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> "tuple[list[str], list[dict[str, Any]]]":
        return query_records(
            self.all(),
            sql=sql,
            select=select,
            where=where,
            order_by=order_by,
            group_by=group_by,
            limit=limit,
            table_alias=self.table_alias,
        )


# --------------------------------------------------------------------------- #
# Pluggable record providers (Markdown default; GitHub / GitLab / Jira / AI6)
# --------------------------------------------------------------------------- #


class RecordProvider(ABC):
    """Backend interface for storing and querying plans/findings/records."""

    kind: str = ""

    @abstractmethod
    def capabilities(self) -> "dict[str, Any]":
        """Report provider capabilities and configuration."""

    @abstractmethod
    def list_records(self, *, status: Optional[str] = None) -> "list[dict[str, Any]]":
        """List records."""

    @abstractmethod
    def query(
        self,
        *,
        sql: Optional[str] = None,
        select: Optional[str] = None,
        where: Optional[str] = None,
        order_by: Optional[str] = None,
        group_by: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> "tuple[list[str], list[dict[str, Any]]]":
        """Query records with SQL or filter clauses."""

    @abstractmethod
    def get(self, name: str) -> "dict[str, Any]":
        """Fetch one record by name/id."""

    @abstractmethod
    def create(self, name: str, *, meta: "Optional[dict[str, Any]]" = None, body: str = "") -> "dict[str, Any]":
        """Create a record."""

    @abstractmethod
    def update(self, name: str, *, meta: "Optional[dict[str, Any]]" = None, body: Optional[str] = None) -> "dict[str, Any]":
        """Update record metadata or body."""

    @abstractmethod
    def move(self, name: str, new_status: str) -> "dict[str, Any]":
        """Transition a record to ``new_status``."""

    @abstractmethod
    def delete(self, name: str, *, force: bool = False) -> str:
        """Delete a record."""


class MarkdownProvider(RecordProvider):
    """Default local Markdown+frontmatter file provider."""

    kind = "markdown"

    def __init__(self, store: FolderStore):
        self.store = store

    def capabilities(self) -> "dict[str, Any]":
        return {
            "kind": self.kind,
            "root": str(self.store.root),
            "sql": True,
            "complex_dirs": True,
            "status_folders": self.store.status_folders or {},
        }

    def list_records(self, *, status: Optional[str] = None) -> "list[dict[str, Any]]":
        recs = self.store.all()
        if status:
            recs = [r for r in recs if r.status == status]
        return [r.as_dict(include_body=False) for r in recs]

    def query(
        self,
        *,
        sql: Optional[str] = None,
        select: Optional[str] = None,
        where: Optional[str] = None,
        order_by: Optional[str] = None,
        group_by: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> "tuple[list[str], list[dict[str, Any]]]":
        return self.store.query(
            sql=sql,
            select=select,
            where=where,
            order_by=order_by,
            group_by=group_by,
            limit=limit,
        )

    def get(self, name: str) -> "dict[str, Any]":
        return self.store.require(name).as_dict(include_body=True)

    def create(self, name: str, *, meta: "Optional[dict[str, Any]]" = None, body: str = "") -> "dict[str, Any]":
        return self.store.add(name, meta=meta, body=body).as_dict(include_body=True)

    def update(self, name: str, *, meta: "Optional[dict[str, Any]]" = None, body: Optional[str] = None) -> "dict[str, Any]":
        if meta:
            rec = self.store.set_attrs(name, meta)
        else:
            rec = self.store.require(name)
        if body is not None:
            rec = self.store.edit_body(name, body=body)
        return rec.as_dict(include_body=True)

    def move(self, name: str, new_status: str) -> "dict[str, Any]":
        return self.store.move(name, new_status).as_dict(include_body=False)

    def delete(self, name: str, *, force: bool = False) -> str:
        return str(self.store.remove(name, force=force))


class ExternalTrackerProvider(RecordProvider):
    """Base adapter for remote issue/MR/report providers (GitHub, GitLab, Jira, AI6)."""

    required_keys: "tuple[str, ...]" = ()

    def __init__(self, kind: str, config: "Optional[dict[str, Any]]" = None):
        self.kind = kind
        self.config = dict(config or {})

    def _require_configured(self) -> None:
        missing = [k for k in self.required_keys if not self.config.get(k)]
        if missing:
            raise fail(
                "provider %r is missing configuration keys: %s"
                % (self.kind, ", ".join(missing))
            )

    def capabilities(self) -> "dict[str, Any]":
        return {
            "kind": self.kind,
            "configured": all(bool(self.config.get(k)) for k in self.required_keys),
            "required_keys": list(self.required_keys),
            "config": self.config,
        }

    def _not_wired(self, op: str) -> "Any":
        self._require_configured()
        raise fail(
            "external provider %r (%s) requires its transport/CLI adapter to execute %s"
            % (self.kind, self.config.get("repo") or self.config.get("project") or self.config.get("url") or "", op)
        )

    def list_records(self, *, status: Optional[str] = None) -> "list[dict[str, Any]]":
        return self._not_wired("list")

    def query(self, **kwargs: Any) -> "tuple[list[str], list[dict[str, Any]]]":
        return self._not_wired("query")

    def get(self, name: str) -> "dict[str, Any]":
        return self._not_wired("get")

    def create(self, name: str, *, meta: "Optional[dict[str, Any]]" = None, body: str = "") -> "dict[str, Any]":
        return self._not_wired("create")

    def update(self, name: str, *, meta: "Optional[dict[str, Any]]" = None, body: Optional[str] = None) -> "dict[str, Any]":
        return self._not_wired("update")

    def move(self, name: str, new_status: str) -> "dict[str, Any]":
        return self._not_wired("move")

    def delete(self, name: str, *, force: bool = False) -> str:
        return self._not_wired("delete")


class GitHubProvider(ExternalTrackerProvider):
    required_keys = ("repo",)

    def __init__(self, config: "Optional[dict[str, Any]]" = None):
        super().__init__("github", config)


class GitLabProvider(ExternalTrackerProvider):
    required_keys = ("project",)

    def __init__(self, config: "Optional[dict[str, Any]]" = None):
        super().__init__("gitlab", config)


class JiraProvider(ExternalTrackerProvider):
    required_keys = ("url", "project")

    def __init__(self, config: "Optional[dict[str, Any]]" = None):
        super().__init__("jira", config)


class Ai6Provider(ExternalTrackerProvider):
    required_keys = ("operation",)

    def __init__(self, config: "Optional[dict[str, Any]]" = None):
        super().__init__("ai6", config)


BUILTIN_PROVIDERS: "dict[str, type[ExternalTrackerProvider]]" = {
    "github": GitHubProvider,
    "gitlab": GitLabProvider,
    "jira": JiraProvider,
    "ai6": Ai6Provider,
}


def load_provider_settings(agents_root: Path, scope_key: str) -> "dict[str, Any]":
    """Read provider configuration for ``scope_key`` (``plans``, ``findings``, ``records``)
    from ``<agents_root>/records.toml`` or ``<agents_root>/agent-tools.toml``."""
    if tomllib is None:
        return {"provider": "markdown"}
    for candidate in (agents_root / "records.toml", agents_root / "agent-tools.toml"):
        if not candidate.is_file():
            continue
        try:
            data = tomllib.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        records_cfg = data.get("records")
        if isinstance(records_cfg, dict) and isinstance(records_cfg.get(scope_key), dict):
            cfg = dict(records_cfg[scope_key])
            cfg.setdefault("provider", "markdown")
            return cfg
        direct = data.get(scope_key)
        if isinstance(direct, dict):
            cfg = dict(direct)
            cfg.setdefault("provider", "markdown")
            return cfg
    return {"provider": "markdown"}


def open_provider(store: FolderStore, agents_root: Optional[Path] = None, scope_key: str = "records") -> RecordProvider:
    """Resolve the active :class:`RecordProvider` for ``store`` (default: ``markdown``)."""
    cfg = load_provider_settings(agents_root or store.root.parent, scope_key)
    kind = str(cfg.get("provider", "markdown")).lower()
    if kind == "markdown":
        return MarkdownProvider(store)
    cls = BUILTIN_PROVIDERS.get(kind)
    if cls is None:
        known = ", ".join(["markdown"] + sorted(BUILTIN_PROVIDERS))
        raise fail("unknown record provider %r (known: %s)" % (kind, known))
    return cls(cfg)
