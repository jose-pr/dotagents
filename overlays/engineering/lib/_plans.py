"""Plans as files: the shape `dotagents plans` reads and edits.

A plan is held as lines, so each change touches one of them. See the command
module, `cmds/plans.py`, for the layout and the file shape.
"""

from __future__ import annotations

import datetime
import logging
import re
from pathlib import Path
from typing import Optional

from dotagents._fs import write_text_lf

PLANS_DIRNAME = "plans"
COMPLETED = "completed"
ON_HOLD = "on-hold"
INDEX_NAME = "INDEX.md"
_NOT_PLANS = {INDEX_NAME.lower(), "readme.md"}

LIVE = ("draft", "ready", "review", "executing")
STATUSES = LIVE + ("on-hold", "done")
#: Where a plan with each status lives, relative to the plans directory.
_HOME = {"on-hold": ON_HOLD, "done": COMPLETED}

BOXES = {" ": "pending", "/": "active", "x": "done", "!": "blocked"}
_SEP = " — "
_ITEM_RE = re.compile(r"^- \[([ /x!])\] (.*)$")
_PHASE_ID_RE = re.compile(r"^Phase\s+([0-9A-Za-z.]+)\s*:\s*(.*)$")
_FIELD_RE = re.compile(r"^([A-Z][A-Za-z-]*):\s*(.*)$")
_COMMENT_RE = re.compile(r"<!--.*?-->\n?", re.DOTALL)
_PLACEHOLDER_RE = re.compile(r"<[a-z][^<>\n]*>")
SECTIONS = {
    "progress": "Progress",
    "facts": "Known Facts & Context",
    "phases": "Phases",
    "verification": "Verification",
    "reporting": "Reporting",
}
_TEMPLATE = Path(__file__).resolve().parents[1] / "references" / "plan_template.md"
_FALLBACK_TEMPLATE = (
    "# <Plan Title>\n\nStatus: draft\nExecutor: <family/subrole from MODELS.md>\n\n"
    "## Progress\n\n- [ ] Phase 1: <name>\n\n## Known Facts & Context\n\n- <fact>\n\n"
    "## Phases\n\n### Phase 1: <name>\n\n- Files: <paths>\n- Guidance: <hints>\n"
    "- Why: <one line>\n- Done-when: <observable check>\n\n## Verification\n\n"
    "- <command> → <expected>\n\n## Reporting\n\n- Handoff includes: changed files, "
    "evidence per done-when, unverified items.\n"
)

_LOGGER = logging.getLogger("dotagents.plans")


def _today() -> str:
    return datetime.date.today().isoformat()


def _fail(message: str) -> "SystemExit":
    return SystemExit("error: " + message)


def slugify(text: str) -> str:
    """A plan's file name from free text: lowercase snake_case."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


class Item:
    """One line of a plan's Progress list."""

    __slots__ = ("index", "box", "id", "name", "note")

    def __init__(self, index: int, box: str, text: str, ordinal: int):
        self.index = index  # line number within the plan
        self.box = box
        head, sep, note = text.partition(_SEP)
        match = _PHASE_ID_RE.match(head.strip())
        self.id = match.group(1) if match else str(ordinal)
        self.name = (match.group(2) if match else head).strip()
        self.note = note.strip() if sep else ""

    @property
    def state(self) -> str:
        return BOXES[self.box]

    def render(self) -> str:
        text = "- [%s] Phase %s: %s" % (self.box, self.id, self.name)
        return text + (_SEP + self.note if self.note else "")


class Plan:
    """One plan file, held as lines so that every edit touches one of them."""

    def __init__(self, path: Path, lines: "list[str]", name: str):
        self.path = Path(path)
        self.lines = lines
        self.name = name

    @classmethod
    def load(cls, path: Path, name: str) -> "Plan":
        text = Path(path).read_text(encoding="utf-8-sig")
        return cls(path, text.splitlines(), name)

    def save(self) -> None:
        write_text_lf(self.path, "\n".join(self.lines).rstrip("\n") + "\n")

    # -- the head: title and `Key: value` fields ------------------------------

    @property
    def title(self) -> str:
        for line in self.lines:
            if line.startswith("# "):
                return line[2:].strip()
        return self.name

    def _head_end(self) -> int:
        for i, line in enumerate(self.lines):
            if line.startswith("## "):
                return i
        return len(self.lines)

    def field(self, key: str) -> str:
        for line in self.lines[: self._head_end()]:
            match = _FIELD_RE.match(line)
            if match and match.group(1) == key:
                return match.group(2).strip()
        return ""

    def set_field(self, key: str, value: Optional[str]) -> None:
        """Set, add (after the last field) or, with `None`, drop a head field."""
        end = self._head_end()
        last = None
        for i, line in enumerate(self.lines[:end]):
            match = _FIELD_RE.match(line)
            if not match:
                continue
            last = i
            if match.group(1) == key:
                if value is None:
                    del self.lines[i]
                else:
                    self.lines[i] = "%s: %s" % (key, value)
                return
        if value is None:
            return
        at = last + 1 if last is not None else min(end, 1)
        self.lines.insert(at, "%s: %s" % (key, value))

    @property
    def status(self) -> str:
        return self.field("Status").split()[0].lower() if self.field("Status") else ""

    # -- sections -------------------------------------------------------------

    def section(self, heading: str) -> "tuple[int, int]":
        """(first line after the heading, line of the next `## `); -1, -1 if absent."""
        start = -1
        for i, line in enumerate(self.lines):
            if line.startswith("## "):
                if start >= 0:
                    return start, i
                if line[3:].strip() == heading:
                    start = i + 1
        return (start, len(self.lines)) if start >= 0 else (-1, -1)

    def section_text(self, heading: str) -> str:
        start, end = self.section(heading)
        if start < 0:
            raise _fail("%s has no `## %s` section" % (self.name, heading))
        return "\n".join(["## " + heading] + self.lines[start:end]).rstrip() + "\n"

    # -- progress -------------------------------------------------------------

    def items(self) -> "list[Item]":
        start, end = self.section(SECTIONS["progress"])
        found: "list[Item]" = []
        for i in range(max(start, 0), max(end, 0)):
            match = _ITEM_RE.match(self.lines[i])
            if match:
                found.append(Item(i, match.group(1), match.group(2), len(found) + 1))
        return found

    def item(self, phase: str) -> Item:
        wanted = phase.strip().lower()
        if wanted.startswith("phase"):
            wanted = wanted[5:].strip().rstrip(":").strip()
        items = self.items()
        for it in items:
            if it.id.lower() == wanted:
                return it
        raise _fail("%s has no phase %r (it has: %s)"
                    % (self.name, phase, ", ".join(it.id for it in items) or "none"))

    def mark(self, phase: str, box: str, note: Optional[str]) -> Item:
        it = self.item(phase)
        it.box = box
        if note is not None:
            it.note = " ".join(note.split())
        self.lines[it.index] = it.render()
        return it

    def counts(self) -> "tuple[int, int]":
        items = self.items()
        return sum(1 for it in items if it.box == "x"), len(items)

    def current(self) -> Optional[Item]:
        """The phase to work on: the active one, else the first pending one."""
        items = self.items()
        for box in ("/", " "):
            for it in items:
                if it.box == box:
                    return it
        return None

    def phase_text(self, phase: str) -> str:
        it = self.item(phase)
        start, end = self.section(SECTIONS["phases"])
        head = re.compile(r"^###\s+Phase\s+%s\s*:" % re.escape(it.id), re.IGNORECASE)
        for i in range(max(start, 0), max(end, 0)):
            if head.match(self.lines[i]):
                stop = i + 1
                while stop < end and not self.lines[stop].startswith("### "):
                    stop += 1
                return "\n".join(self.lines[i:stop]).rstrip() + "\n"
        raise _fail("%s has no `### Phase %s:` block" % (self.name, it.id))

    def append_fact(self, fact: str) -> None:
        start, end = self.section(SECTIONS["facts"])
        if start < 0:
            raise _fail("%s has no `## %s` section" % (self.name, SECTIONS["facts"]))
        at = end
        while at > start and not self.lines[at - 1].strip():
            at -= 1
        self.lines.insert(at, "- " + " ".join(fact.split()))

    def set_phases(self, phases: "list[str]") -> None:
        """Replace the template's placeholder phases with the named ones."""
        start, end = self.section(SECTIONS["progress"])
        self.lines[start:end] = (
            [""] + ["- [ ] Phase %d: %s" % (n, p) for n, p in enumerate(phases, 1)] + [""])
        start, end = self.section(SECTIONS["phases"])
        blocks = [""]
        for n, p in enumerate(phases, 1):
            blocks += ["### Phase %d: %s" % (n, p), "", "- Files: <paths>",
                       "- Guidance: <signatures, hints, defaults>", "- Why: <one line>",
                       "- Done-when: <observable check>", ""]
        self.lines[start:end] = blocks

    # -- summaries ------------------------------------------------------------

    def summary(self) -> str:
        done, total = self.counts()
        current = self.current()
        where = " | %s Phase %s: %s" % (
            "active" if current.box == "/" else "next", current.id, current.name
        ) if current else ""
        return "%s  [%s]  %d/%d  %s%s" % (
            self.name, self.status or "?", done, total, self.title, where)

    def as_dict(self) -> "dict[str, object]":
        done, total = self.counts()
        return {
            "name": self.name, "path": str(self.path), "title": self.title,
            "status": self.status, "executor": self.field("Executor"),
            "done": done, "total": total,
            "phases": [{"id": it.id, "name": it.name, "state": it.state, "note": it.note}
                       for it in self.items()],
        }

    def problems(self) -> "list[str]":
        """What keeps this plan from the required shape."""
        found: "list[str]" = []
        if not any(line.startswith("# ") for line in self.lines):
            found.append("no `# <title>` line")
        if self.status not in STATUSES:
            found.append("Status %r is not one of: %s" % (self.status, ", ".join(STATUSES)))
        if not self.field("Executor"):
            found.append("no `Executor:` line")
        order = [line[3:].strip() for line in self.lines if line.startswith("## ")]
        required = list(SECTIONS.values())
        if [h for h in order if h in required] != required:
            found.append("sections must be, in order: %s" % ", ".join(required))
        items = self.items()
        if not items:
            found.append("the Progress list is empty")
        start, end = self.section(SECTIONS["phases"])
        blocks = [m.group(1).lower() for m in
                  (re.match(r"^###\s+Phase\s+([0-9A-Za-z.]+)\s*:", line)
                   for line in self.lines[max(start, 0):max(end, 0)]) if m]
        ids = [it.id.lower() for it in items]
        if len(set(ids)) != len(ids):
            found.append("a phase id is used twice in Progress")
        for missing in [i for i in ids if i not in blocks]:
            found.append("Progress lists Phase %s but there is no `### Phase %s:` block"
                         % (missing, missing))
        for extra in [b for b in blocks if b not in ids]:
            found.append("`### Phase %s:` has no line in Progress" % extra)
        left = sorted({m.group(0) for line in self.lines
                       for m in _PLACEHOLDER_RE.finditer(line)})
        if left:
            found.append("unfilled placeholders: %s" % ", ".join(left[:6]))
        if "<!--" in "\n".join(self.lines):
            found.append("template comments were not removed")
        return found


class PlansStore:
    """The plans under one directory."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, home: str) -> Path:
        return self.root / home if home else self.root

    def _plans_in(self, home: str) -> "list[Plan]":
        base = self._dir(home)
        if not base.is_dir():
            return []
        found: "list[Plan]" = []
        for path in sorted(base.glob("*.md")):
            if path.name.lower() in _NOT_PLANS or path.name.startswith("_"):
                continue
            try:
                found.append(Plan.load(path, path.stem))
            except UnicodeDecodeError:
                _LOGGER.warning("skipping %s: not UTF-8", path)
                continue
            sub_dir = base / path.stem
            if sub_dir.is_dir() and sub_dir.name not in (COMPLETED, ON_HOLD):
                for sub in sorted(sub_dir.glob("*.md")):
                    found.append(Plan.load(sub, "%s/%s" % (path.stem, sub.stem)))
        return found

    def live(self) -> "list[Plan]":
        return self._plans_in("")

    def on_hold(self) -> "list[Plan]":
        return self._plans_in(ON_HOLD)

    def completed(self) -> "list[Plan]":
        return self._plans_in(COMPLETED)

    def all(self) -> "list[Plan]":
        return self.live() + self.on_hold() + self.completed()

    def get(self, name: str) -> Optional[Plan]:
        wanted = name[:-3] if name.endswith(".md") else name
        wanted = wanted.replace("\\", "/").strip("/")
        for plan in self.all():
            if plan.name == wanted:
                return plan
        return None

    def require(self, name: str) -> Plan:
        plan = self.get(name)
        if plan is None:
            raise _fail("no plan %r under %s" % (name, self.root))
        return plan

    # -- mutation (every one regenerates the index) ---------------------------

    def add(self, name: str, *, title: str = "", executor: str = "",
            phases: "Optional[list[str]]" = None) -> Plan:
        parts = [slugify(p) for p in name.replace("\\", "/").split("/") if p.strip()]
        if not parts or not all(parts) or len(parts) > 2:
            raise _fail("a plan name is `<name>` or `<parent>/<sub>`, in snake_case")
        if parts[-1] in {n[:-3] for n in _NOT_PLANS} or parts[-1] in (COMPLETED, ON_HOLD.replace("-", "_")):
            raise _fail("%r is reserved" % parts[-1])
        slug = "/".join(parts)
        if self.get(slug):
            raise _fail("plan %r already exists under %s" % (slug, self.root))
        if len(parts) == 2 and not self.get(parts[0]):
            raise _fail("sub-plan %r needs its parent plan %r first" % (slug, parts[0]))
        path = self.root.joinpath(*parts).with_suffix(".md")
        text = _TEMPLATE.read_text(encoding="utf-8") if _TEMPLATE.is_file() else _FALLBACK_TEMPLATE
        text = _COMMENT_RE.sub("", text).lstrip("\n")
        plan = Plan(path, text.splitlines(), slug)
        if title:
            for i, line in enumerate(plan.lines):
                if line.startswith("# "):
                    plan.lines[i] = "# " + " ".join(title.split())
                    break
        plan.set_field("Status", "draft")
        if executor:
            plan.set_field("Executor", " ".join(executor.split()))
        if phases:
            plan.set_phases([" ".join(p.split()) for p in phases])
        path.parent.mkdir(parents=True, exist_ok=True)
        plan.save()
        self.write_index()
        return plan

    def set_status(self, name: str, status: str, *, note: str = "", force: bool = False) -> Plan:
        status = status.strip().lower()
        if status not in STATUSES:
            raise _fail("status must be one of: %s" % ", ".join(STATUSES))
        plan = self.require(name)
        note = " ".join(note.split())
        if status == "done":
            open_items = [it for it in plan.items() if it.box != "x"]
            if open_items and not force:
                raise _fail(
                    "%s is not finished: %s. Check them, or pass --force to close it anyway"
                    % (plan.name, ", ".join("Phase %s [%s]" % (it.id, it.state) for it in open_items)))
            if not note:
                raise _fail("closing a plan needs -m: what was done and the evidence")
            plan.set_field("Closed", _today())
            plan.set_field("Outcome", note)
        elif status == "on-hold":
            if not note:
                raise _fail("parking a plan needs -m: why, and what would restart it")
            plan.set_field("Held", "%s %s" % (_today(), note))
        else:
            for key in ("Closed", "Outcome", "Held"):
                plan.set_field(key, None)
        plan.set_field("Status", status)
        plan.save()
        self._move(plan, _HOME.get(status, ""))
        self.write_index()
        return plan

    def _move(self, plan: Plan, home: str) -> None:
        """Move a plan, and its sub-plan directory with it, to where its status
        says it lives. A sub-plan stays beside its siblings."""
        if "/" in plan.name:
            return
        dest = self._dir(home) / plan.path.name
        if dest == plan.path:
            return
        if dest.exists():
            raise _fail("cannot move %r: %s already exists" % (plan.name, dest))
        sub_src = plan.path.with_suffix("")
        sub_dest = dest.with_suffix("")
        if sub_src.is_dir() and sub_dest.exists():
            raise _fail("cannot move %r: %s already exists" % (plan.name, sub_dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        plan.path.replace(dest)
        if sub_src.is_dir():
            sub_src.replace(sub_dest)
        plan.path = dest

    def save(self, plan: Plan) -> None:
        plan.save()
        self.write_index()

    # -- index ----------------------------------------------------------------

    def render_index(self) -> str:
        lines = [
            "# Plans", "",
            "One line per plan. Live plans first; a finished plan is moved to",
            "`%s/`, a parked one to `%s/`. Regenerated by `dotagents plans`."
            % (COMPLETED, ON_HOLD), "",
        ]

        def link(plan: Plan, home: str) -> str:
            rel = (home + "/" if home else "") + plan.name + ".md"
            return "[%s](%s)" % (plan.name, rel)

        def block(heading: str, plans: "list[Plan]", home: str, tail) -> None:
            lines.extend(["## " + heading, ""])
            if not plans:
                lines.append("(none)")
            for plan in plans:
                done, total = plan.counts()
                indent = "  " if "/" in plan.name else ""
                lines.append("%s- %s — %s — %d/%d — %s%s" % (
                    indent, link(plan, home), plan.status or "?", done, total,
                    plan.title, tail(plan)))
            lines.append("")

        block("Live", self.live(), "", lambda p: "")
        block("On hold", self.on_hold(), ON_HOLD,
              lambda p: " — held %s" % p.field("Held") if p.field("Held") else "")
        block("Completed", self.completed(), COMPLETED,
              lambda p: " — closed %s: %s" % (p.field("Closed"), p.field("Outcome"))
              if p.field("Closed") else "")
        return "\n".join(lines).rstrip("\n") + "\n"

    def write_index(self) -> Path:
        path = self.root / INDEX_NAME
        write_text_lf(path, self.render_index(), atomic=True)
        return path
