"""`dotagents findings` -- AI6-report-style frontmatter findings in status folders:
add / list / query / show / set / edit / done / reopen / remove / migrate / requote / providers / path.

Layout::

    <root>/findings/
        active/<name>.md        active finding (also tolerates legacy `<root>/<name>.md`)
        processed/<name>.md     addressed finding, resolution appended
        <status>/<name>.md      any extra status folder (e.g. `later/`, `duplicate/`)

Every finding is a Markdown file with YAML frontmatter for structured fields and
attributes (``name``, ``description``, ``status``, ``severity``, ``category``,
``source``, ``credibility``, ``confidence``, ``where``, ``links``, ``created``,
``updated``, ``processed``, plus arbitrary attributes) and context/evidence/resolution
after the frontmatter. No ``INDEX.md`` is generated or maintained.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Optional

from duho import Cli, LoggingArgs

from dotagents._fs import write_text_lf
from dotagents._records import (
    FolderStore,
    Record,
    coerce_attr_value,
    format_query_result,
    open_provider,
    parse_frontmatter,
    query_records,
    render_frontmatter,
    requote_frontmatter,
    slugify_name,
    today_iso as _today,
    yaml_scalar as _yaml_scalar,
    yaml_value as _yaml_text,
)
from dotagents.cli import DotAgentsArgs, _write_stdout

FINDINGS_DIRNAME = "findings"
ACTIVE_DIRNAME = "active"
PROCESSED_DIRNAME = "processed"
INDEX_NAME = "INDEX.md"
_NOT_FINDINGS = {INDEX_NAME.lower(), "readme.md"}
_RESERVED_SLUGS = {name[:-3] for name in _NOT_FINDINGS} | {ACTIVE_DIRNAME, PROCESSED_DIRNAME}
_META_KEYS = (
    "name", "description", "status", "severity", "category", "source",
    "credibility", "confidence", "where", "reviewed", "links",
    "created", "updated", "processed", "provider",
)
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_MAX_SLUG = 60

STATUS_DIRS = {"active": ACTIVE_DIRNAME, "processed": PROCESSED_DIRNAME}

_LOGGER = logging.getLogger("dotagents.findings")


def slugify(text: str) -> str:
    """A finding's file name from free text (lowercase hyphen slug, max 60 chars)."""
    return slugify_name(text, sep="-", max_len=_MAX_SLUG)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _read_stdin() -> str:
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:
        return sys.stdin.read()
    try:
        return buffer.read().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SystemExit("error: stdin is not UTF-8 (%s)" % exc)


def _read_text_arg(inline: "Optional[str]", file: "Optional[Path]") -> "Optional[str]":
    if inline is not None:
        return inline
    if file is None:
        return None
    if str(file) == "-":
        return _read_stdin()
    path = Path(file).expanduser()
    try:
        return _read(path)
    except UnicodeDecodeError as exc:
        raise SystemExit("error: %s is not UTF-8 (%s)" % (path, exc))


def _parse_attrs(pairs: "list[str]") -> "dict[str, Any]":
    out: "dict[str, Any]" = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit("error: expected KEY=VALUE, got %r" % (pair,))
        k, v = pair.split("=", 1)
        k = k.strip()
        if not k:
            raise SystemExit("error: empty key in %r" % (pair,))
        out[k] = coerce_attr_value(v)
    return out


class Finding:
    """One finding file: YAML frontmatter (``meta``) and Markdown ``body``."""

    __slots__ = ("path", "meta", "body")

    def __init__(self, path: Path, meta: "dict[str, Any]", body: str):
        self.path = Path(path)
        self.meta = dict(meta)
        self.body = body

    @classmethod
    def load(cls, path: Path) -> "Finding":
        path = Path(path)
        text = _read(path)
        # Keep string-compatible parsing for frontmatter values, preserving typed attrs
        meta: "dict[str, Any]" = {}
        m = _FRONTMATTER_RE.match(text)
        if m:
            parsed, body, _ = parse_frontmatter(text, typed=True)
            meta.update(parsed)
            # Keep raw-string values for description/name even if bare word looks bool/number
            for line in m.group(1).splitlines():
                key, sep, value = line.partition(":")
                if sep and key.strip() in ("name", "description", "status", "created", "processed", "updated"):
                    meta[key.strip()] = str(_yaml_text(value.strip()))
        else:
            body = text
        meta.setdefault("name", path.stem)
        if not meta.get("description"):
            meta["description"] = cls._first_line(body)
        if not meta.get("status"):
            parent = path.parent.name.lower()
            meta["status"] = PROCESSED_DIRNAME if parent == PROCESSED_DIRNAME else ACTIVE_DIRNAME
        return cls(path, meta, body.lstrip("\r\n"))

    @staticmethod
    def _first_line(body: str) -> str:
        for line in body.splitlines():
            line = line.strip()
            if line:
                return line.lstrip("#").strip()[:120]
        return ""

    def render(self) -> str:
        meta = dict(self.meta)
        if "description" in meta and meta["description"] is not None:
            meta["description"] = str(meta["description"])
        return render_frontmatter(meta, self.body, key_order=_META_KEYS)

    def save(self) -> None:
        write_text_lf(self.path, self.render())

    @property
    def name(self) -> str:
        return str(self.meta.get("name", self.path.stem))

    @property
    def description(self) -> str:
        val = self.meta.get("description", "")
        return "" if val is None else str(val)

    @property
    def status(self) -> str:
        return str(self.meta.get("status", "active"))

    @property
    def is_active(self) -> bool:
        return self.status != PROCESSED_DIRNAME

    def as_dict(self) -> "dict[str, Any]":
        out: "dict[str, Any]" = dict(self.meta)
        out["path"] = str(self.path)
        out["body"] = self.body
        return out

    def to_record(self, root: Path) -> Record:
        return Record(self.path, self.meta, self.body, root=root, key_order=_META_KEYS)

    def __repr__(self) -> str:
        return "Finding(%r, %s)" % (self.name, self.status)


class FindingsStore:
    """Findings store under ``<root>/active/*.md`` and ``<root>/processed/*.md``
    (also tolerating legacy top-level ``<root>/*.md`` and any extra status dirs).
    No ``INDEX.md`` is generated."""

    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def active_dir(self) -> Path:
        return self.root / ACTIVE_DIRNAME

    @property
    def processed_dir(self) -> Path:
        return self.root / PROCESSED_DIRNAME

    def status_dir(self, status: str) -> Path:
        folder = STATUS_DIRS.get(status, status)
        return self.root / folder

    @staticmethod
    def _findings_in(directory: Path) -> "list[Finding]":
        if not directory.is_dir():
            return []
        out = []
        for p in sorted(directory.glob("*.md")):
            if p.name.lower() in _NOT_FINDINGS or p.name.startswith("_"):
                continue
            try:
                out.append(Finding.load(p))
            except UnicodeDecodeError:
                _LOGGER.warning("skipping %s: not UTF-8", p)
        return out

    def active(self) -> "list[Finding]":
        # Read both new `active/*.md` and legacy top-level `*.md`
        seen: "dict[str, Finding]" = {}
        for f in self._findings_in(self.active_dir) + self._findings_in(self.root):
            seen.setdefault(f.name, f)
        return list(seen.values())

    def processed(self) -> "list[Finding]":
        return self._findings_in(self.processed_dir)

    def all(self) -> "list[Finding]":
        out = self.active() + self.processed()
        if self.root.is_dir():
            for d in sorted(self.root.iterdir()):
                if d.is_dir() and d.name not in (ACTIVE_DIRNAME, PROCESSED_DIRNAME, "archive") and not d.name.startswith("."):
                    out.extend(self._findings_in(d))
        return out

    def get(self, name: str) -> "Optional[Finding]":
        wanted = {name, slugify(name)}
        if name.endswith(".md"):
            wanted.add(name[:-3])
        for finding in self.all():
            if finding.name in wanted or finding.path.stem in wanted:
                return finding
        return None

    def require(self, name: str) -> Finding:
        finding = self.get(name)
        if finding is None:
            raise SystemExit("error: no finding %r under %s" % (name, self.root))
        return finding

    def add(
        self,
        description: str,
        *,
        name: "Optional[str]" = None,
        body: str = "",
        attrs: "Optional[dict[str, Any]]" = None,
        status: str = ACTIVE_DIRNAME,
    ) -> Finding:
        description = " ".join(description.split())
        if not description:
            raise SystemExit("error: a finding needs a one-line description")
        slug = slugify(name or description)
        if not slug:
            raise SystemExit("error: %r yields no usable file name; pass --name" % (name or description))
        if slug in _RESERVED_SLUGS:
            raise SystemExit(
                "error: %r is reserved; pass another --name" % (slug,)
            )
        dest_dir = self.status_dir(status)
        path = dest_dir / (slug + ".md")
        if (
            path.exists()
            or (self.root / (slug + ".md")).exists()
            or (self.processed_dir / (slug + ".md")).exists()
            or self.get(slug)
        ):
            raise SystemExit("error: finding %r already exists under %s (pass --name)" % (slug, self.root))
        meta: "dict[str, Any]" = {
            "name": slug,
            "description": description,
            "status": status,
            "created": _today(),
        }
        if attrs:
            for k, v in attrs.items():
                if v is not None:
                    meta[k] = v
        meta["name"] = slug
        meta["description"] = description
        meta["status"] = status
        finding = Finding(path, meta, body.strip("\r\n") or description)
        finding.save()
        return finding

    def set_attrs(self, name: str, updates: "dict[str, Any]", *, unset: "Optional[list[str]]" = None) -> Finding:
        finding = self.require(name)
        for k in (unset or []):
            finding.meta.pop(k, None)
        for k, v in updates.items():
            if v is None:
                finding.meta.pop(k, None)
            else:
                finding.meta[k] = v
        finding.meta["updated"] = _today()
        finding.save()
        if "status" in updates and updates["status"]:
            new_st = str(updates["status"]).strip().lower()
            dest = self.status_dir(new_st) / finding.path.name
            if dest != finding.path:
                self._refuse_to_overwrite(finding, dest)
                dest.parent.mkdir(parents=True, exist_ok=True)
                finding.path.replace(dest)
                finding.path = dest
        return finding

    def edit_body(
        self,
        name: str,
        *,
        body: "Optional[str]" = None,
        section: "Optional[str]" = None,
        append: "Optional[str]" = None,
        replace: "Optional[tuple[str, str]]" = None,
    ) -> Finding:
        finding = self.require(name)
        rec = finding.to_record(self.root)
        if body is not None:
            rec.body = body
        elif section and append is not None:
            rec.append_to_section(section, append)
        elif section and replace is not None:
            sec_text = rec.section_text(section)
            old, new = replace
            if old not in sec_text:
                raise SystemExit("error: %s: %r not found in section %r" % (finding.name, old, section))
            rec.set_section(section, sec_text.split("\n", 1)[1].replace(old, new))
        elif append is not None:
            rec.body = rec.body.rstrip("\r\n") + "\n\n" + append.strip("\r\n") + "\n"
        elif replace is not None:
            rec.replace_in_body(replace[0], replace[1])
        else:
            raise SystemExit("error: pass --body, --append, or --replace")
        finding.body = rec.body
        finding.meta["updated"] = _today()
        finding.save()
        return finding

    def done(self, name: str, resolution: str) -> Finding:
        resolution = resolution.strip()
        if not resolution:
            raise SystemExit(
                "error: a resolution is required -- a processed finding without one "
                "is untriaged, not done (pass -r/--resolution or --resolution-file)"
            )
        finding = self.require(name)
        if not finding.is_active:
            raise SystemExit("error: finding %r is already processed (%s)" % (finding.name, finding.path))
        dest = self.processed_dir / finding.path.name
        self._refuse_to_overwrite(finding, dest)
        today = _today()
        finding.meta["status"] = PROCESSED_DIRNAME
        finding.meta["processed"] = today
        finding.meta["updated"] = today
        finding.body = finding.body.rstrip("\r\n") + "\n\n## Resolution (%s)\n\n%s\n" % (today, resolution)
        finding.save()
        dest.parent.mkdir(parents=True, exist_ok=True)
        finding.path.replace(dest)
        finding.path = dest
        return finding

    def reopen(self, name: str) -> Finding:
        finding = self.require(name)
        if finding.is_active:
            raise SystemExit("error: finding %r is already active (%s)" % (finding.name, finding.path))
        dest = self.active_dir / finding.path.name
        self._refuse_to_overwrite(finding, dest)
        self._refuse_to_overwrite(finding, self.root / finding.path.name)
        finding.meta["status"] = ACTIVE_DIRNAME
        finding.meta["updated"] = _today()
        finding.meta.pop("processed", None)
        finding.save()
        dest.parent.mkdir(parents=True, exist_ok=True)
        finding.path.replace(dest)
        finding.path = dest
        return finding

    @staticmethod
    def _refuse_to_overwrite(finding: Finding, dest: Path) -> None:
        if dest.exists():
            raise SystemExit(
                "error: cannot move %r: %s already exists (rename one of the two "
                "files, then retry)" % (finding.name, dest)
            )

    def remove(self, name: str, *, force: bool = False) -> Path:
        finding = self.require(name)
        if not finding.is_active and not force:
            raise SystemExit(
                "error: finding %r is processed (%s); processed findings are kept. "
                "`reopen` it first or pass --force"
                % (finding.name, finding.path)
            )
        path = finding.path
        path.unlink()
        return path

    def requote(self) -> "list[Path]":
        changed = []
        for finding in self.all():
            text = finding.path.read_bytes().decode("utf-8")
            fixed = requote_frontmatter(text)
            if fixed != text:
                write_text_lf(finding.path, fixed)
                changed.append(finding.path)
        return changed

    def query(
        self,
        *,
        sql: "Optional[str]" = None,
        select: "Optional[str]" = None,
        where: "Optional[str]" = None,
        order_by: "Optional[str]" = None,
        group_by: "Optional[str]" = None,
        limit: "Optional[int]" = None,
    ) -> "tuple[list[str], list[dict[str, Any]]]":
        recs = [f.to_record(self.root) for f in self.all()]
        return query_records(
            recs,
            sql=sql,
            select=select or "name, status, severity, category, description",
            where=where,
            order_by=order_by,
            group_by=group_by,
            limit=limit,
            table_alias="findings",
        )

    def migrate(self, *, dry_run: bool = False) -> "dict[str, Any]":
        """Move legacy top-level ``*.md`` into ``active/``, requote/normalize frontmatter, remove ``INDEX.md``."""
        moved: "list[str]" = []
        requoted: "list[str]" = []
        removed_index = False
        for finding in self.all():
            if not dry_run:
                text = finding.path.read_bytes().decode("utf-8")
                fixed = requote_frontmatter(text)
                if fixed != text:
                    write_text_lf(finding.path, fixed)
                    requoted.append(finding.name)
            if finding.is_active and finding.path.parent == self.root:
                dest = self.active_dir / finding.path.name
                self._refuse_to_overwrite(finding, dest)
                if not dry_run:
                    finding.meta["status"] = ACTIVE_DIRNAME
                    finding.save()
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    finding.path.replace(dest)
                    finding.path = dest
                moved.append("%s -> %s" % (finding.name, dest))
        idx = self.root / INDEX_NAME
        if idx.is_file():
            if not dry_run:
                idx.unlink()
            removed_index = True
        return {"moved": moved, "requoted": requoted, "removed_index": removed_index}

    def provider_info(self, agents_root: "Optional[Path]" = None) -> "dict[str, Any]":
        fs = FolderStore(
            self.root,
            status_folders=STATUS_DIRS,
            default_status=ACTIVE_DIRNAME,
            key_order=_META_KEYS,
            table_alias="findings",
        )
        return open_provider(fs, agents_root or self.root.parent, scope_key="findings").capabilities()


class Findings(LoggingArgs, Cli):
    """Findings store for a scope (`active/` and `processed/`, YAML frontmatter, SQL query)."""

    _parsername_ = "findings"

    class _Base(DotAgentsArgs):
        dir: Optional[Path] = None
        "Findings directory override (default: <scope>/findings)."
        ("--dir",)

        def store(self) -> FindingsStore:
            if self.dir:
                root = Path(self.dir).expanduser()
            else:
                root = self.resolve_scope().agents_root / FINDINGS_DIRNAME
            return FindingsStore(root)

    class Add(_Base):
        """Record a new finding in `findings/active/` with AI6-report frontmatter."""

        _parsername_ = "add"

        description: str = ""
        "One-line summary of the finding."
        ("description",)

        name: Optional[str] = None
        "File name (slug) to use instead of one derived from the description."
        ("--name", "-n")

        severity: str = ""
        "Severity (high, medium, low, nit)."
        ("--severity", "-S")

        category: str = ""
        "Category (correctness, security, api, lean, docs, ux, ...)."
        ("--category", "-C")

        source: str = ""
        "Source that observed it (review, probe, session, user, ...)."
        ("--source",)

        attr: "list[str]" = []
        "Extra frontmatter attribute `KEY=VALUE` (repeatable)."
        ("--attr", "-a")

        body: Optional[str] = None
        "Details (markdown). Default: the description."
        ("--body", "-b")

        body_file: Optional[Path] = None
        "Read the details from a file (`-` = stdin)."
        ("--body-file",)

        def __call__(self) -> int:
            body = _read_text_arg(self.body, self.body_file) or ""
            attrs = _parse_attrs(list(self.attr)) if self.attr else {}
            if self.severity:
                attrs["severity"] = self.severity
            if self.category:
                attrs["category"] = self.category
            if self.source:
                attrs["source"] = self.source
            finding = self.store().add(
                self.description, name=self.name, body=body, attrs=attrs or None
            )
            self._logger_.info("added finding %s", finding.name)
            _write_stdout("%s\n" % (finding.path,))
            return 0

    class List(_Base):
        """List findings (active by default; supports --where, --order, --all, --processed, --json)."""

        _parsername_ = "list"

        all: bool = False
        "Include processed findings."
        ("--all", "-a")

        processed: bool = False
        "Only processed findings."
        ("--processed",)

        where: str = ""
        "SQL WHERE filter."
        ("--where", "-w")

        order_by: str = ""
        "SQL ORDER BY clause."
        ("--order",)

        as_json: bool = False
        "Emit JSON."
        ("--json",)

        def __call__(self) -> int:
            store = self.store()
            if self.where or self.order_by:
                where = self.where
                if self.processed:
                    where = ("(status = 'processed') AND (%s)" % where) if where else "status = 'processed'"
                elif not self.all:
                    where = ("(status != 'processed') AND (%s)" % where) if where else "status != 'processed'"
                cols, rows = store.query(where=where, order_by=self.order_by or None)
                _write_stdout(format_query_result(cols, rows, "json" if self.as_json else "table"))
                return 0
            if self.processed:
                findings = store.processed()
            elif self.all:
                findings = store.all()
            else:
                findings = store.active()
            if self.as_json:
                _write_stdout(json.dumps({
                    "dir": str(store.root),
                    "findings": [
                        {k: v for k, v in f.as_dict().items() if k != "body"}
                        for f in findings
                    ],
                }, indent=2) + "\n")
                return 0
            if not findings:
                self._logger_.info("no findings under %s", store.root)
                return 0
            show_status = self.all
            for f in findings:
                prefix = "[%s] " % f.status if show_status else ""
                _write_stdout("%s%s: %s\n" % (prefix, f.name, f.description))
            return 0

    class Query(_Base):
        """SQL-like query over findings (`SELECT ... FROM findings WHERE ...`)."""

        _parsername_ = "query"

        sql: str = ""
        "Full SQL SELECT query against table `findings` (or `records`)."
        ("sql",)

        select: str = ""
        "Comma-separated columns to select."
        ("--select", "-s")

        where: str = ""
        "SQL WHERE clause."
        ("--where", "-w")

        order_by: str = ""
        "SQL ORDER BY clause."
        ("--order",)

        group_by: str = ""
        "SQL GROUP BY clause."
        ("--group",)

        limit: int = 0
        "Max rows (0 = all)."
        ("--limit", "-n")

        fmt: str = "table"
        "Output format: table, json, tsv, md."
        ("--format", "-f")

        def __call__(self) -> int:
            cols, rows = self.store().query(
                sql=self.sql or None,
                select=self.select or None,
                where=self.where or None,
                order_by=self.order_by or None,
                group_by=self.group_by or None,
                limit=self.limit or None,
            )
            _write_stdout(format_query_result(cols, rows, self.fmt))
            return 0

    class Show(_Base):
        """Print one finding, the whole file (--json for structured form)."""

        _parsername_ = "show"

        name: str = ""
        "Finding name."
        ("name",)

        as_json: bool = False
        "Emit JSON."
        ("--json",)

        def __call__(self) -> int:
            finding = self.store().require(self.name)
            if self.as_json:
                _write_stdout(json.dumps(finding.as_dict(), indent=2) + "\n")
            else:
                _write_stdout(finding.render())
            return 0

    class Set(_Base):
        """Update frontmatter attributes (`--attr KEY=VALUE`) on a finding."""

        _parsername_ = "set"

        name: str = ""
        "Finding name."
        ("name",)

        attrs: "list[str]" = []
        "Attributes as `KEY=VALUE` (repeatable)."
        ("--attr", "-a")

        unset: "list[str]" = []
        "Attribute keys to remove."
        ("--unset", "-u")

        def __call__(self) -> int:
            updates = _parse_attrs(list(self.attrs))
            if not updates and not self.unset:
                raise SystemExit("error: pass --attr KEY=VALUE or --unset KEY")
            finding = self.store().set_attrs(self.name, updates, unset=list(self.unset))
            _write_stdout("%s\n" % (finding.path,))
            return 0

    class Edit(_Base):
        """Edit the Markdown body or a section of a finding."""

        _parsername_ = "edit"

        name: str = ""
        "Finding name."
        ("name",)

        section: str = ""
        "Target `## <section>` heading."
        ("--section", "-s")

        append: Optional[str] = None
        "Append text to the body or target section."
        ("--append",)

        replace: "list[str]" = []
        "Replace `OLD=NEW`."
        ("--replace", "-r")

        body: Optional[str] = None
        "Replace the entire Markdown body."
        ("--body", "-b")

        body_file: Optional[Path] = None
        "Replace body from a file (`-` = stdin)."
        ("--body-file",)

        def __call__(self) -> int:
            body = _read_text_arg(self.body, self.body_file)
            rep = None
            if self.replace:
                if len(self.replace) != 1 or "=" not in self.replace[0]:
                    raise SystemExit("error: --replace takes one `OLD=NEW` pair")
                old, new = self.replace[0].split("=", 1)
                rep = (old, new)
            finding = self.store().edit_body(
                self.name,
                body=body,
                section=self.section or None,
                append=self.append,
                replace=rep,
            )
            _write_stdout("%s\n" % (finding.path,))
            return 0

    class Done(_Base):
        """Mark a finding addressed: append resolution, move to `processed/`."""

        _parsername_ = "done"

        name: str = ""
        "Finding name."
        ("name",)

        resolution: Optional[str] = None
        "The decision, evidence, or why no action was needed."
        ("--resolution", "-r")

        resolution_file: Optional[Path] = None
        "Read the resolution from a file (`-` = stdin)."
        ("--resolution-file",)

        def __call__(self) -> int:
            resolution = _read_text_arg(self.resolution, self.resolution_file) or ""
            finding = self.store().done(self.name, resolution)
            self._logger_.info("processed finding %s", finding.name)
            _write_stdout("%s\n" % (finding.path,))
            return 0

    class Reopen(_Base):
        """Move a processed finding back to `active/`."""

        _parsername_ = "reopen"

        name: str = ""
        "Finding name."
        ("name",)

        def __call__(self) -> int:
            finding = self.store().reopen(self.name)
            self._logger_.info("reopened finding %s", finding.name)
            _write_stdout("%s\n" % (finding.path,))
            return 0

    class Remove(_Base):
        """Delete a finding (requires --force for processed findings)."""

        _parsername_ = "remove"

        name: str = ""
        "Finding name."
        ("name",)

        force: bool = False
        "Allow removing a processed finding."
        ("--force", "-f")

        def __call__(self) -> int:
            path = self.store().remove(self.name, force=self.force)
            self._logger_.info("removed %s", path)
            _write_stdout("%s\n" % (path,))
            return 0

    class Requote(_Base):
        """Quote frontmatter values YAML cannot read bare (bodies are not touched)."""

        _parsername_ = "requote"

        def __call__(self) -> int:
            changed = self.store().requote()
            self._logger_.info("requoted the frontmatter of %d finding(s)", len(changed))
            for p in changed:
                _write_stdout("%s\n" % (p,))
            return 0

    class Migrate(_Base):
        """Move legacy top-level findings to `active/`, requote frontmatter, remove `INDEX.md`."""

        _parsername_ = "migrate"

        dry_run: bool = False
        "Report changes without writing."
        ("--dry-run",)

        def __call__(self) -> int:
            res = self.store().migrate(dry_run=self.dry_run)
            _write_stdout(json.dumps(res, indent=2) + "\n")
            return 0

    class Providers(_Base):
        """Show the active findings provider configuration (default: markdown)."""

        _parsername_ = "providers"

        def __call__(self) -> int:
            store = self.store()
            agents_root = self.dir.parent if self.dir else self.resolve_scope().agents_root
            _write_stdout(json.dumps(store.provider_info(agents_root), indent=2) + "\n")
            return 0

    class PathCmd(_Base):
        """Print the findings directory for the scope."""

        _parsername_ = "path"

        def __call__(self) -> int:
            _write_stdout("%s\n" % (self.store().root,))
            return 0

    _subcommands_ = [
        Add, List, Query, Show, Set, Edit, Done, Reopen, Remove,
        Requote, Migrate, Providers, PathCmd,
    ]
