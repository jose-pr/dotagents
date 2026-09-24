"""Overlay support: the :class:`Overlay` type, plus the multi-overlay recompose.

An overlay is a directory (optionally carrying an `overlay.toml` manifest) that
installs as a unit into `<store>/overlays/<name>/`, its files keeping their
relative paths inside that directory. Everything that
depends only on ONE overlay -- its name rules, its manifest, its setup script,
its files, what it contributes to the managed `AGENTS.md` block -- is a method or
property of :class:`Overlay`. Manifest keys:

* `routing` — lines appended to the core's "Load on demand" list.
* `rules` — overlay-relative paths to markdown files whose `- **…` bullet blocks
  are appended to "Always-on rules".
* `requires` — overlay names this one needs; `overlays add` installs them first.
* `name`, `description`, `priority` — see :meth:`Overlay.read_manifest`.

The operations over a *set* of overlays stay module functions:
``_compose_block`` folds their rules and routing into a base block, and
:func:`recompose_overlay_block` rewrites a store's ``AGENTS.md`` with it.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable, Optional

_log = logging.getLogger("dotagents")
_warned: "set[str]" = set()


def _warn_once(message: str, *args: object) -> None:
    """Log a warning once per process: manifests are re-read many times per
    command (priority, sort key, compose), and one problem is one line."""
    text = message % args if args else message
    if text not in _warned:
        _warned.add(text)
        _log.warning("%s", text)


# Default merge priority for an overlay that declares none. Lower sorts
# earlier. 500 leaves generous headroom on both sides for overlays that want to
# sort before (0..499) or after (501..) the unprioritized default.
DEFAULT_PRIORITY = 500


# --------------------------------------------------------------------------- #
# Manifest text parsing (pure functions over TOML source; no overlay needed).
#
# `tomllib` (3.11+) or `tomli` reads the manifest. `tomli` is a declared
# dependency below 3.11 and is vendored in the .pyz, so the small hand reader
# below only runs in an install that skipped its dependencies; it covers what
# a manifest uses: top-level strings, arrays of strings and an integer, with
# TOML's escapes.
# --------------------------------------------------------------------------- #


def _toml_module() -> "Any":
    """``tomllib`` / ``tomli`` when importable, else ``None`` (seam for tests)."""
    try:
        import tomllib  # type: ignore[import-not-found]

        return tomllib
    except ImportError:
        pass
    try:
        import tomli  # type: ignore[import-not-found]

        return tomli
    except ImportError:
        return None


_ESCAPES = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\", "e": "\x1b"}


def _unescape(text: str) -> str:
    """Decode a TOML basic string's escapes (``\\"``, ``\\\\``, ``\\n``, ``\\uXXXX``...)."""

    def one(m: "re.Match[str]") -> str:
        esc = m.group(1)
        if esc[0] in "uU":
            try:
                return chr(int(esc[1:], 16))
            except ValueError:
                return m.group(0)
        return _ESCAPES.get(esc, m.group(0))

    return re.sub(r"\\(u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}|.)", one, text)


def _top_level(text: str) -> str:
    """The (comment-stripped) text before the first ``[table]`` header, so a
    ``key =`` inside a table is never read as a top-level key. Quote- and
    bracket-aware: a ``[`` inside a string or an array does not count."""
    i, n, depth = 0, len(text), 0
    quote: "Optional[str]" = None
    line_start = True
    while i < n:
        c = text[i]
        if quote:
            if text.startswith(quote, i):
                i += len(quote)
                quote = None
            elif c == "\\" and quote in ('"', '"""') and i + 1 < n:
                i += 2
            else:
                i += 1
            continue
        if c == "\n":
            line_start = True
            i += 1
            continue
        if line_start and c in " \t":
            i += 1
            continue
        if line_start and c == "[" and depth == 0:
            return text[:i]
        line_start = False
        if text.startswith('"""', i) or text.startswith("'''", i):
            quote = text[i : i + 3]
            i += 3
        elif c in ('"', "'"):
            quote = c
            i += 1
        else:
            if c == "[":
                depth += 1
            elif c == "]":
                depth = max(0, depth - 1)
            i += 1
    return text


def _strip_comments(text: str) -> str:
    """Drop `#` comments -- whole-line AND trailing -- leaving string contents
    untouched: a `#` inside `"..."`, `'...'`, `\"\"\"...\"\"\"` or `'''...'''`
    (multi-line included) is data. Trailing comments must go too, or
    `routing = ["a"] # note` reaches the array reader with text after its `]`."""
    out = []
    i, n = 0, len(text)
    quote: "Optional[str]" = None
    while i < n:
        c = text[i]
        if quote:
            if text.startswith(quote, i):
                out.append(quote)
                i += len(quote)
                quote = None
            elif c == "\\" and quote in ('"', '"""') and i + 1 < n:
                out.append(text[i : i + 2])  # an escape inside a basic string
                i += 2
            else:
                out.append(c)
                i += 1
            continue
        if text.startswith('"""', i) or text.startswith("'''", i):
            quote = text[i : i + 3]
            out.append(quote)
            i += 3
        elif c in ('"', "'"):
            quote = c
            out.append(c)
            i += 1
        elif c == "#":
            while i < n and text[i] != "\n":
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _array_body(text: str, key: str) -> "Optional[str]":
    """The text between the `[` and its matching `]` of a top-level
    `key = [...]`, found by a quote-aware scan -- so a `]` inside a string
    (a markdown link in a routing line) or the NEXT key's array can never be
    mistaken for this array's end, and the closing bracket may sit on the same
    line, on its own line, indented, or right after a multi-line string."""
    m = re.search(r"(?m)^%s\s*=\s*\[" % re.escape(key), text)
    if m is None:
        return None
    i, n = m.end(), len(text)
    start = i
    quote: "Optional[str]" = None
    while i < n:
        c = text[i]
        if quote:
            if text.startswith(quote, i):
                i += len(quote)
                quote = None
            elif c == "\\" and quote in ('"', '"""') and i + 1 < n:
                i += 2
            else:
                i += 1
            continue
        if text.startswith('"""', i) or text.startswith("'''", i):
            quote = text[i : i + 3]
            i += 3
        elif c in ('"', "'"):
            quote = c
            i += 1
        elif c == "]":
            return text[start:i]
        else:
            i += 1
    return None  # unterminated: treat as absent


def _parse_string_array(text: str, key: str) -> "list[str]":
    """Read a top-level `key = [...]` array of strings from (comment-stripped)
    TOML source.

    The fallback when neither `tomllib` nor `tomli` is importable (the Python
    3.9 floor has no `tomllib`). Handles `"..."` (escapes decoded),
    `'...'`, and the multi-line
    `\"\"\"...\"\"\"` / `'''...'''` -- one or many per array, with the closing
    `]` on the same line or on its own (indented or not)."""
    body = _array_body(text, key)
    if body is None:
        return []
    # One pass, in order: triple-quoted forms first so their content is never
    # re-matched as single-quoted; basic strings decode their escapes.
    pattern = re.compile(
        r'"""((?:[^\\]|\\.)*?)"""|\'\'\'(.*?)\'\'\'|"((?:[^"\\\n]|\\.)*)"|\'([^\'\n]*)\'',
        re.DOTALL,
    )
    items = []
    for basic_ml, literal_ml, basic, literal in pattern.findall(body):
        if basic_ml or basic:
            items.append(_unescape(basic_ml or basic))
        else:
            items.append(literal_ml or literal)
    return [s.strip("\n") for s in items if s.strip()]


def _parse_string(text: str, key: str) -> "Optional[str]":
    """A top-level single-line `key = "..."` / `key = '...'` string, or None."""
    m = re.search(
        r"""(?m)^%s\s*=\s*(?:"((?:[^"\\\n]|\\.)*)"|'([^'\n]*)')\s*$""" % re.escape(key), text
    )
    if m is None:
        return None
    return _unescape(m.group(1)) if m.group(1) is not None else m.group(2)


def _parse_priority(text: str) -> int:
    """Read a top-level `priority = <int>` from (comment-stripped) TOML source.
    Missing/unparseable -> DEFAULT_PRIORITY."""
    m = re.search(r"(?m)^priority\s*=\s*([+-]?\d(?:_?\d)*)\s*$", text)
    if m is None:
        return DEFAULT_PRIORITY
    return int(m.group(1).replace("_", ""))  # the pattern admits only valid ints


def _hand_parse(text: str) -> "dict[str, object]":
    """The fallback reader's view of a manifest, as the keys ``tomllib`` would
    give (absent keys omitted)."""
    raw = _top_level(_strip_comments(text))
    doc: "dict[str, object]" = {
        "routing": _parse_string_array(raw, "routing"),
        "rules": _parse_string_array(raw, "rules"),
        "requires": _parse_string_array(raw, "requires"),
        "priority": _parse_priority(raw),
    }
    for key in ("name", "description"):
        value = _parse_string(raw, key)
        if value is not None:
            doc[key] = value
    return doc


def parse_manifest_text(text: str, origin: str = "overlay.toml") -> "Optional[dict[str, object]]":
    """The manifest document: ``tomllib``/``tomli`` when importable, else the
    hand reader. ``None`` (and a warning naming ``origin``) when it is not
    valid TOML."""
    toml = _toml_module()
    if toml is None:
        return _hand_parse(text)
    try:
        return toml.loads(text)
    except ValueError as exc:  # TOMLDecodeError is a ValueError
        _warn_once("%s is not valid TOML (%s); ignoring it", origin, exc)
        return None


def _string_list(value: object) -> "list[str]":
    """The string items of an array (anything else dropped), multi-line
    strings trimmed of their surrounding newlines, blanks dropped."""
    if not isinstance(value, list):
        return []
    return [v.strip("\n") for v in value if isinstance(v, str) and v.strip()]


def _file_digest(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class InstallResult(object):
    """What :meth:`Overlay.install_to` did. Unpacks as the historical
    ``(written, skipped, lines)``, ``skipped`` being unchanged + kept."""

    def __init__(self) -> None:
        self.written = 0
        self.unchanged = 0
        #: Files left alone that DIFFER from the source (hand edits, or a
        #: source change not applied without ``--overwrite``).
        self.kept: "list[str]" = []
        #: Files removed because the source no longer ships them.
        self.removed: "list[str]" = []
        #: Files the source no longer ships that were edited here, so kept.
        self.orphans_kept: "list[str]" = []
        #: Files copied to the backup root before being replaced or pruned.
        self.backed_up: "list[str]" = []
        self.lines: "list[str]" = []

    @property
    def skipped(self) -> int:
        return self.unchanged + len(self.kept)

    def __iter__(self):
        return iter((self.written, self.skipped, self.lines))


def _same_content(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# The overlay itself.
# --------------------------------------------------------------------------- #


class Overlay:
    """One overlay: a directory whose NAME identifies it and whose files install
    as a unit under ``<scope>/overlays/<name>/``.

    Construct it from the overlay's directory (a source dir or an installed
    dir -- both are overlays). Nothing is read at construction; every method
    reads the directory when called, so an ``Overlay`` made before its files
    land still sees them afterwards. The name-only rules (:meth:`is_valid_name`,
    :meth:`normalize_name`, :meth:`root_var_for`) are static so a caller holding
    just a name -- ``overlays add <name>`` -- uses the same rule as one holding a
    directory.
    """

    #: A directory under ``overlays/`` is an overlay IFF its name matches this: a
    #: leading ASCII letter, then ASCII letters/digits/``_``/``.``/``-``, ending
    #: in a letter or digit. A dot is allowed MID-name (``foo.bar``, ``v1.2``)
    #: but not first, so ``.git``/``.hidden`` are excluded, nor last: Win32
    #: strips a trailing dot, so ``python.`` would BE ``python`` on disk. A
    #: leading underscore (``__pycache__``) and a leading digit (``2fast``) are
    #: excluded too. One shared rule for :meth:`discover` and ``overlays add``
    #: (D84). Whether a path IS a directory is checked separately with
    #: ``is_dir()``, which follows symlinks -- a symlink-to-dir is a valid overlay.
    NAME_RE = re.compile(r"^[A-Za-z](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?$")

    #: Windows device names: ``con`` / ``nul`` / ``com1`` (with any extension)
    #: name the device, not a directory, on every Windows drive.
    RESERVED_NAMES = frozenset(
        ["con", "prn", "aux", "nul"]
        + ["com%d" % i for i in range(1, 10)]
        + ["lpt%d" % i for i in range(1, 10)]
    )

    #: The optional manifest an overlay may carry at its root.
    MANIFEST_NAME = "overlay.toml"

    #: An overlay's optional idempotent setup script: ``setup.py``, run under
    #: the interpreter that runs dotagents, so it works on every platform.
    #: Presence = opt-in; absence = nothing to run.
    SETUP_SCRIPT_NAMES = ("setup.py",)

    DEFAULT_PRIORITY = DEFAULT_PRIORITY

    __slots__ = ("path", "store")

    def __init__(
        self, path: "str | os.PathLike[str]", store: "str | os.PathLike[str] | None" = None
    ):
        self.path = Path(path)
        #: The store (an ``.agents`` root) this overlay was discovered in, when it
        #: came from :meth:`installed`; ``None`` for a bare directory. Not part
        #: of identity (``__eq__`` / ``__hash__`` are the path only).
        self.store: "Optional[Path]" = Path(store) if store is not None else None

    # -- identity: the plain object protocol ---------------------------------

    def __repr__(self) -> str:
        return "Overlay(%r)" % str(self.path)

    def __fspath__(self) -> str:
        return str(self.path)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Overlay) and self.path == other.path

    def __hash__(self) -> int:
        return hash(self.path)

    # -- name rules (static: usable with only a name in hand) -----------------

    @staticmethod
    def is_valid_name(name: str) -> bool:
        """A dir under ``overlays/`` is an overlay iff its name matches
        :data:`NAME_RE`: a leading ASCII letter, then ASCII letters/digits/``_``/
        ``.``/``-``. Dots are allowed mid-name (``foo.bar``, ``v1.2``) but NOT as
        the first char, so ``.git``/``.hidden`` and ``__pycache__`` (leading ``_``)
        and ``2fast`` (leading digit) are excluded. The contract-A level names
        (``user``, ``project``, ``system``, ``project-root``, ``default``,
        ``overlay`` -- :data:`dotagents._scope.LEVEL_NAMES`) are reserved: an
        overlay's dir name is its level label in the walk, and one of these
        would collide with the per-level filename keys. So are Windows device
        names (:data:`RESERVED_NAMES`, with or without an extension)."""
        from dotagents._scope import LEVEL_NAMES

        return (
            bool(Overlay.NAME_RE.match(name))
            and name.lower() not in LEVEL_NAMES
            and name.split(".", 1)[0].lower() not in Overlay.RESERVED_NAMES
        )

    @staticmethod
    def normalize_name(name: str) -> str:
        """THE canonical overlay name: lowercase, ``_`` and ``.`` -> ``-``.

        Every place that refers to an overlay by name goes through this one
        function, so ``My_Overlay`` and ``my-overlay`` are one overlay everywhere:
        ``overlays add`` installs to ``<scope>/overlays/<normalize_name(name)>/``
        and resolves the source dir by the same name, shadowing across stores
        compares it, and :meth:`root_var_for` derives the ``<NAME>_OVERLAY_ROOT``
        env var from it. A dot is legal in a dir name (:meth:`is_valid_name`)
        but no shell variable can carry it, so ``v1.2`` and ``v1-2`` would share
        ``V1_2_OVERLAY_ROOT``; normalizing it to ``-`` makes them one overlay
        rather than two that alias."""
        return name.lower().replace("_", "-").replace(".", "-")

    @staticmethod
    def root_var_for(name: str) -> str:
        """The env-var name carrying an overlay's installed root: ``<NAME>_OVERLAY_ROOT``.

        ``NAME`` is :meth:`normalize_name` of the overlay upper-cased, with ``-``
        (and a mid-name ``.``, which no shell variable can carry) turned into
        ``_``: ``my-overlay`` / ``My_Overlay`` -> ``MY_OVERLAY_OVERLAY_ROOT``,
        ``v1.2`` -> ``V1_2_OVERLAY_ROOT``. Because it starts from the same
        normalized name the install dir uses, the variable for ``overlays/<n>/``
        is always ``root_var_for(n)`` (:attr:`root_var` on the instance). Both
        consumers go through here: ``dotagents env`` EMITS the variable and
        ``dotagents context`` expands the ``<NAME_OVERLAY_ROOT>`` placeholder to
        the same path."""
        return re.sub(r"[^A-Z0-9]", "_", Overlay.normalize_name(name).upper()) + "_OVERLAY_ROOT"

    # -- per-instance identity ------------------------------------------------

    @property
    def name(self) -> str:
        """The overlay's name: its directory name."""
        return self.path.name

    @property
    def normalized_name(self) -> str:
        """:meth:`normalize_name` of :attr:`name`."""
        return self.normalize_name(self.name)

    @property
    def root_var(self) -> str:
        """:meth:`root_var_for` of :attr:`name` -- the ``<NAME>_OVERLAY_ROOT`` var."""
        return self.root_var_for(self.name)

    @property
    def is_valid(self) -> bool:
        """:meth:`is_valid_name` of :attr:`name`."""
        return self.is_valid_name(self.name)

    @property
    def manifest_path(self) -> Path:
        return self.path / self.MANIFEST_NAME

    # -- discovery ------------------------------------------------------------

    @classmethod
    def discover(
        cls, root: "str | os.PathLike[str]", store: "str | os.PathLike[str] | None" = None
    ) -> "list[Overlay]":
        """The overlays under ONE ``overlays/`` root, sorted by name.

        No registry, no manifest required: a directory under ``root`` *is* an
        overlay as long as its name passes :meth:`is_valid_name` (so ``.git``/
        ``__pycache__``/dotfiles are skipped). ``is_dir()`` follows symlinks, so a
        symlink-to-dir counts. Empty if ``root`` is absent. This is the ONE
        discovery rule: :meth:`installed` folds it over several stores,
        and the ``overlays`` command's ``add``/``remove``/``sync`` use it directly
        for the single scope they install into. ``store`` is stamped on each
        result (see :attr:`store`)."""
        root = Path(root)
        if not root.is_dir():
            return []
        found = [
            cls(p, store)
            for p in sorted(root.iterdir())
            if p.is_dir() and cls.is_valid_name(p.name)
        ]
        seen: "dict[str, str]" = {}
        for overlay in found:
            other = seen.setdefault(overlay.normalized_name, overlay.name)
            if other != overlay.name:
                _warn_once(
                    "overlays %s and %s in %s are one overlay (%s) and share %s; remove one",
                    other, overlay.name, root, overlay.normalized_name, overlay.root_var,
                )
        return found

    @classmethod
    def installed(cls, *stores: "str | os.PathLike[str] | None") -> "list[Overlay]":
        """Every overlay a session uses, across ``stores`` (``.agents``-shaped
        roots) given in precedence order -- ``Scope.stores``: the system store,
        the user store, then the project's ``<project>/.agents`` (a user scope
        has no project store). A ``None`` store is skipped.

        The overlays come back store by store, each store's sorted by name,
        with every result stamped with the :attr:`store` it came from.
        **Shadowing:** an overlay whose name (compared normalized, so
        ``my_overlay`` and ``my-overlay`` are one) also appears in a LATER store
        is dropped -- the later store's copy REPLACES it, so the project's bin/lib/
        env/cmds/CONTEXT.md/root var are the only ones that resolve, not both
        stacked. This is the one function behind the contract-A walk
        (``Scope.paths``), ``env``'s overlay roots, ``context``'s
        sources/placeholders/skills and ``overlays list``/``show``, so they can
        never disagree about what is installed."""
        per_store = [
            cls.discover(Path(store) / "overlays", store)
            for store in stores
            if store is not None
        ]
        result: "list[Overlay]" = []
        for i, found in enumerate(per_store):
            later = {o.normalized_name for rest in per_store[i + 1 :] for o in rest}
            result.extend(o for o in found if o.normalized_name not in later)
        return result

    # -- manifest -------------------------------------------------------------

    def read_manifest(self) -> "dict[str, object]":
        """Parse `overlay.toml`, returning name / description / routing / rules /
        requires / priority.

        A missing or unreadable manifest yields empty contributions -- an overlay
        is allowed to be just a directory of files."""
        empty: "dict[str, object]" = {
            "name": self.name, "description": "", "routing": [], "rules": [],
            "requires": [], "priority": DEFAULT_PRIORITY,
        }
        path = self.manifest_path
        if not path.is_file():
            return empty
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            _warn_once("cannot read %s (%s); ignoring it", path, exc)
            return empty
        doc = parse_manifest_text(text, str(path))
        if doc is None:
            return empty
        name, description, priority = doc.get("name"), doc.get("description"), doc.get("priority")
        # `requires` names become install paths (`<scope>/overlays/<name>`):
        # only a valid overlay name may pass, so `../x` or a level name can
        # never escape `overlays/` or leave an orphan the commands cannot see.
        requires: "list[str]" = []
        for raw in _string_list(doc.get("requires")):
            if not self.is_valid_name(raw):
                _warn_once("overlay %s: ignoring invalid requires entry %r", self.name, raw)
                continue
            dep = self.normalize_name(raw)
            if dep not in requires:
                requires.append(dep)
        return {
            "name": name if isinstance(name, str) and name else self.name,
            "description": description if isinstance(description, str) else "",
            "routing": _string_list(doc.get("routing")),
            "rules": _string_list(doc.get("rules")),
            "requires": requires,
            "priority": priority if isinstance(priority, int) and not isinstance(priority, bool)
            else DEFAULT_PRIORITY,
        }

    @property
    def priority(self) -> int:
        """The manifest ``priority``, ``DEFAULT_PRIORITY`` when absent or
        unparseable (:meth:`read_manifest` already did the checking). Lower
        sorts earlier."""
        return self.read_manifest()["priority"]  # type: ignore[return-value]

    @property
    def sort_key(self) -> "tuple[int, str, str]":
        """The `(priority, name)` merge-order key.

        Lower `priority` (default `DEFAULT_PRIORITY`, 500) sorts earlier; a
        numerically higher-priority overlay therefore lands *later* in the merged
        AGENTS.md block and wins on conflict -- the same convention `_context.py`
        uses. The tiebreaker is the manifest `name` (falling back to the directory
        name), then the directory name itself (two dirs can carry the same
        manifest name), so output is deterministic regardless of input order."""
        manifest = self.read_manifest()
        return (self.priority, str(manifest.get("name", self.name)), self.name)

    @classmethod
    def sort_by_priority(
        cls, overlays: "Iterable[Overlay | str | os.PathLike[str]]"
    ) -> "list[Overlay]":
        """`overlays` (instances or dirs) sorted by `(priority, name)`.

        Deterministic regardless of the caller's order (`add` passes invocation
        order, `sync` discovery order): a high-priority overlay's lines must
        always land last. See :attr:`sort_key`."""
        return sorted((cls(o) for o in overlays), key=lambda o: o.sort_key)

    # -- setup script ---------------------------------------------------------

    def find_setup_script(self) -> "Optional[Path]":
        """The overlay's setup script, or ``None`` if it ships none.

        Call this on the *installed* overlay: setup runs against the copy under
        ``<scope>/overlays/<name>/`` with that as its cwd, so a script can
        reference its own sibling files by relative path."""
        for name in self.SETUP_SCRIPT_NAMES:
            candidate = self.path / name
            if candidate.is_file():
                return candidate
        return None

    def run_setup(
        self, *, agents_dir: Path, dry_run: bool, logger,
        scope_root: "Optional[Path]" = None, scope_level: "Optional[str]" = None,
        base_env: "Optional[dict[str, str]]" = None,
    ) -> "Optional[int]":
        """Run the overlay's idempotent ``setup`` script if it ships one.

        Returns the script's exit code, or ``None`` when the overlay has no setup
        script (nothing to run -- not an error). Presence of ``setup.py`` at the
        overlay root is the opt-in; the runner never second-guesses a script the
        user chose to install.

        **Idempotency is the overlay author's contract**: the script must be safe
        to run on every ``add``/``sync`` (check-then-act). The runner only
        invokes it:

        * **cwd** = the installed overlay dir, so the script sees its own files.
        * **env** is ``base_env`` (default ``os.environ``; ``overlays`` passes the
          assembled ``dotagents env``, so the script imports any overlay's
          ``lib`` and calls any overlay's ``bin`` by name), plus
          ``AGENTS_HOME`` = ``agents_dir``, the USER store
          -- what the variable means everywhere else, so a ``dotagents``
          call the script makes resolves the same stores -- plus
          ``AGENTS_SCOPE_ROOT`` = the store the overlay is installed into
          (``scope_root``; the user store for a ``-g`` install, the project's
          ``.agents`` otherwise), ``AGENTS_SCOPE`` = ``user`` / ``project``
          (``scope_level``, when given) and ``AGENTS_OVERLAY_DIR`` = its own
          installed dir.
        * the script runs under the interpreter running dotagents.

        A non-zero exit is returned so the caller can raise a clear error --
        never a silent skip."""
        script = self.find_setup_script()
        if script is None:
            return None

        logger.info("running setup for %s (%s)", self.name, script.name)
        if dry_run:
            logger.info("[dry-run] would run %s in %s", script.name, self.path)
            return 0

        env = dict(os.environ if base_env is None else base_env)
        env["AGENTS_HOME"] = str(agents_dir)
        env["AGENTS_SCOPE_ROOT"] = str(scope_root if scope_root is not None else agents_dir)
        if scope_level:
            env["AGENTS_SCOPE"] = scope_level
        env["AGENTS_OVERLAY_DIR"] = str(self.path)

        import sys
        cmd = [sys.executable, str(script)]

        try:
            res = subprocess.run(cmd, cwd=str(self.path), env=env)
        except OSError as exc:
            logger.error("setup for %s failed to start: %s", self.name, exc)
            return 1
        if res.returncode != 0:
            logger.error("setup for %s exited %d", self.name, res.returncode)
        return res.returncode

    # -- files + what they contribute -----------------------------------------

    #: Path components never installed: VCS metadata (a whole-repository
    #: overlay's clone root carries `.git`) and tool caches. Named, not "every
    #: dot-name": an overlay may ship real dotfiles (a `references/.gitignore`).
    SKIP_PARTS = frozenset({
        ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", ".tox", ".venv", "node_modules",
    })

    #: Beside an INSTALLED overlay's files: where it came from and the digest of
    #: every file dotagents wrote, so ``sync`` resolves from the same repo and
    #: can tell a file deleted upstream (and untouched here) from a hand edit.
    INSTALL_RECORD = ".dotagents-install.json"

    def files(self) -> "list[Path]":
        """Files the overlay installs: everything except its manifest, its
        install record, VCS metadata and tool caches (:attr:`SKIP_PARTS`) and
        compiled ``*.pyc``."""
        out = []
        for p in sorted(self.path.rglob("*")):
            rel = p.relative_to(self.path)
            if (
                p.is_file()
                and p.name != self.MANIFEST_NAME
                and rel.parts != (self.INSTALL_RECORD,)
                and p.suffix != ".pyc"
                and not self.SKIP_PARTS.intersection(rel.parts)
            ):
                out.append(p)
        return out

    # -- the install record ---------------------------------------------------

    @property
    def install_record_path(self) -> Path:
        return self.path / self.INSTALL_RECORD

    def read_install_record(self) -> "dict[str, Any]":
        """``{"source": {...} | None, "files": {rel: sha256}}`` of an installed
        overlay; empty for a source dir or an install that predates records."""
        import json

        try:
            data = json.loads(self.install_record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        files = data.get("files")
        data["files"] = {
            str(k): str(v) for k, v in files.items() if isinstance(v, str)
        } if isinstance(files, dict) else {}
        return data

    def _write_install_record(self, record: "dict[str, Any]") -> None:
        import json

        from dotagents._fs import write_text_lf

        write_text_lf(self.install_record_path, json.dumps(record, indent=1, sort_keys=True) + "\n")

    def rule_blocks(self, rel_paths: "list[str]") -> "tuple[list[str], list[str]]":
        """Extract `- **…` bullet blocks from each referenced markdown file.

        Returns (blocks, warnings). Only the leading run of bullets is taken: a
        rules file may carry explanatory prose under a `## ` heading
        (the engineering overlay's rules/ENGINEERING.md documents *why* its rules are not in the base), and
        that must not be merged into the core. A path that does not exist is
        reported, not fatal -- a broken optional overlay must never block the
        base config landing."""
        blocks: "list[str]" = []
        warnings: "list[str]" = []
        try:
            root = self.path.resolve()
        except OSError:
            root = self.path
        for rel in rel_paths:
            src = self.path / rel
            # Contained: an absolute path or one that resolves outside the
            # overlay would merge bullets from any file into AGENTS.md.
            try:
                inside = not os.path.isabs(rel) and not rel.startswith(("/", "\\")) and (
                    src.resolve() == root or root in src.resolve().parents
                )
            except OSError:
                inside = False
            if not inside:
                warnings.append("rules file outside the overlay: %s" % rel)
                continue
            if not src.is_file():
                warnings.append("rules file not found: %s" % rel)
                continue
            try:
                text = src.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                warnings.append("rules file unreadable: %s (%s)" % (rel, exc))
                continue
            # Everything from the first bullet up to the first `## ` heading after it.
            start = re.search(r"(?m)^- \*\*", text)
            if start is None:
                warnings.append("no rules found in: %s" % rel)
                continue
            body = text[start.start():]
            end = re.search(r"(?m)^## ", body)
            if end is not None:
                body = body[: end.start()]
            blocks.append(body.rstrip())
        return blocks, warnings

    def install_to(
        self,
        dest_overlay_dir: Path,
        dry_run: bool,
        overwrite: bool = False,
        *,
        prune: bool = False,
        backup_root: "Optional[Path]" = None,
        source: "Optional[dict[str, Any]]" = None,
    ) -> "InstallResult":
        """Install the overlay as a *directory* under `<scope>/overlays/<name>/`:
        the overlay is the discoverable unit.

        Copies every file the overlay ships (minus caches -- see :meth:`files`)
        into `dest_overlay_dir`, create-if-absent so re-adding an overlay never
        clobbers a file the user hand-edited inside the installed copy: such a
        file is KEPT and reported (:attr:`InstallResult.kept`), never counted as
        unchanged. `overwrite` (`sync --overwrite`) replaces files whose content
        differs from the source, first copying each to `backup_root` when one is
        given. The overlay's own `overlay.toml` is always refreshed, so a new
        `routing` / `rules` / `requires` upstream reaches the installed copy.

        The install record (:attr:`INSTALL_RECORD`) keeps the digest of every
        file written and `source` (where the overlay came from; the previous
        value when `None`). A recorded file the source no longer ships is
        removed when it is still exactly what was installed, and kept (and
        reported) when it was edited here -- unless `prune`, which removes it
        too (backed up like an overwrite). An install without a record (made
        before records existed) prunes nothing.

        Returns an :class:`InstallResult`, which still unpacks as the old
        ``(written, skipped, lines)``."""
        dest = Path(dest_overlay_dir)
        installed = Overlay(dest)
        record = installed.read_install_record()
        old_files: "dict[str, str]" = record.get("files", {})
        new_files: "dict[str, str]" = {}
        result = InstallResult()

        def backup(rel: str, target: Path) -> None:
            if backup_root is None or dry_run:
                return
            saved = backup_root / rel
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(target), str(saved))
            result.backed_up.append(rel)

        def write(src: Path, target: Path) -> None:
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(target))

        for src in self.files():
            rel = src.relative_to(self.path).as_posix()
            target = dest / rel
            digest = _file_digest(src)
            if target.is_file():
                if _same_content(src, target):
                    result.lines.append("skip (exists): %s" % rel)
                    result.unchanged += 1
                    new_files[rel] = digest
                    continue
                if not overwrite:
                    result.lines.append("keep (differs from source): %s" % rel)
                    result.kept.append(rel)
                    if rel in old_files:
                        new_files[rel] = old_files[rel]
                    continue
                backup(rel, target)
                result.lines.append("update: %s" % rel)
            else:
                result.lines.append("install: %s" % rel)
            write(src, target)
            result.written += 1
            new_files[rel] = digest

        # The manifest is dotagents' own metadata: always the source's.
        if self.manifest_path.is_file():
            target = dest / self.MANIFEST_NAME
            if target.is_file() and _same_content(self.manifest_path, target):
                result.unchanged += 1
            else:
                result.lines.append(
                    "%s: %s" % ("update" if target.is_file() else "install", self.MANIFEST_NAME)
                )
                write(self.manifest_path, target)
                result.written += 1

        # Files installed earlier that the source no longer ships.
        for rel, digest in sorted(old_files.items()):
            if rel in new_files:
                continue
            target = dest / rel
            if not target.is_file():
                continue
            if _file_digest(target) != digest and not prune:
                result.lines.append("keep (gone from source, modified here): %s" % rel)
                result.orphans_kept.append(rel)
                new_files[rel] = digest
                continue
            if _file_digest(target) != digest:
                backup(rel, target)
            result.lines.append("remove (gone from source): %s" % rel)
            result.removed.append(rel)
            if not dry_run:
                target.unlink()
                parent = target.parent
                while parent != dest and parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent

        if not dry_run:
            dest.mkdir(parents=True, exist_ok=True)
            installed._write_install_record({
                "source": source if source is not None else record.get("source"),
                "files": new_files,
            })
        return result


# --------------------------------------------------------------------------- #
# Over a SET of overlays.
# --------------------------------------------------------------------------- #


def _compose_block(base_text: str, overlays, logger) -> str:
    """Fold each overlay's `rules`/`routing` contributions into the base block.

    Rules append to "Always-on rules" and routing to "Load on demand", after the
    base's own -- the base carries the mechanism and should read first. The
    overlays fold in **`(priority, name)` order**, NOT the caller's list
    order: lower `priority` (default `DEFAULT_PRIORITY`, 500) sorts earlier, so a
    numerically higher-priority overlay lands *last* and wins on conflict -- the
    same convention `_context.py` uses. `name` is the tiebreaker, so the block is
    deterministic regardless of discovery order. Returns `base_text` unchanged
    when nothing contributes. `overlays` are `Overlay` instances or overlay dirs."""
    from dotagents._merge import END_MARKER, _marker_lines

    def block_end(text: str) -> int:
        # The managed block's end marker line (outside fences), as `_merge`
        # matches it; the end of the text when there is none.
        ends = _marker_lines(text, END_MARKER)
        return ends[0].start() if ends else len(text)

    rules: "list[str]" = []
    routing: "list[str]" = []
    for overlay in Overlay.sort_by_priority(overlays):
        manifest = overlay.read_manifest()
        blocks, warnings = overlay.rule_blocks(manifest["rules"])  # type: ignore[arg-type]
        for warning in warnings:
            logger.warning("overlay %s: %s", manifest["name"], warning)
        rules.extend(blocks)
        routing.extend(manifest["routing"])  # type: ignore[arg-type]

    if not rules and not routing:
        return base_text

    text = base_text
    if rules:
        # Append after the last always-on bullet, i.e. just before the next heading.
        m = re.search(r"(?m)^## Load on demand", text)
        if m is None:
            # A custom `--from` base without the heading: the rules land at the
            # end of the block instead, as the warning says.
            logger.warning("base AGENTS.md has no 'Load on demand' heading; "
                           "appending overlay rules at the end of the block")
            insert_at = block_end(text)
            text = text[:insert_at] + "\n".join(rules) + "\n\n" + text[insert_at:]
        else:
            text = text[: m.start()] + "\n".join(rules) + "\n\n" + text[m.start():]
    if routing:
        # The base's placeholder line only makes sense with no routing lines.
        # Exactly that one line: anything after it is real content.
        text = re.sub(r"(?m)^Nothing ships here by default[^\n]*\n", "", text)
        # Overlay routing points at `$<NAME>_OVERLAY_ROOT/...` (the var
        # `dotagents env` exports per installed overlay) rather than a hard
        # store path; say so once, so an agent reading the file knows the
        # token is an environment variable it can resolve, not a literal path.
        if any("_OVERLAY_ROOT" in line for line in routing):
            routing = [OVERLAY_ROOT_NOTE] + routing
        insert_at = block_end(text)
        text = text[:insert_at] + "\n".join(routing) + "\n" + text[insert_at:]
    return text


#: Emitted once above overlay routing lines that use the per-overlay root vars.
OVERLAY_ROOT_NOTE = (
    "Overlay paths below use `$<NAME>_OVERLAY_ROOT` -- an environment variable "
    "`dotagents env` exports per installed overlay (its install dir); resolve it "
    "in a shell (`echo $ENGINEERING_OVERLAY_ROOT`; PowerShell `$env:ENGINEERING_OVERLAY_ROOT`) "
    "before opening the file with a file tool."
)


def recompose_overlay_block(
    agents_md: Path,
    base_block: str,
    overlays: "Iterable[Overlay | str | os.PathLike[str]]",
    dry_run: bool,
    logger,
) -> bool:
    """Rebuild AGENTS.md's managed block from the *pristine* base over ALL installed
    overlays in `(priority, name)` order, in place.

    `base_block` is the base overlay's managed block (no overlay content),
    `overlays` is every installed overlay in the scope (instances or dirs), and
    `_compose_block` folds them in sorted order -- so the block is a pure
    function of *which* overlays are installed, never *when* each was added
    (an incremental append could only put a newcomer last).

    Only the managed block (between the dotagents markers) is rewritten; content
    outside the markers is untouched. Returns True if the file changed, False on a
    no-op (nothing to merge, file/markers absent, or block already correct).
    """
    from dotagents._fs import write_text_lf
    from dotagents._merge import _extract_block, find_block

    if not agents_md.is_file():
        # No `init` here yet (an `overlays add` into a fresh project): create
        # the block rather than drop the overlays' rules and routing.
        if not dry_run:
            write_text_lf(agents_md, _compose_block(_extract_block(base_block), list(overlays), logger) + "\n")
        logger.info("%s %s with the managed block", "would create" if dry_run else "created", agents_md)
        return True
    existing = agents_md.read_text(encoding="utf-8")
    span = find_block(existing)
    if span is None:
        logger.warning(
            "AGENTS.md has no dotagents managed block; overlay rules/routing not "
            "merged (run `dotagents init` first)"
        )
        return False

    # Compose over the pristine base block, not the current (already-merged) one, so
    # every install of the same overlay set yields identical output.
    pristine = _extract_block(base_block)
    merged = _compose_block(pristine, list(overlays), logger)

    start, end = span
    if existing[start:end] == merged:
        return False
    new_text = existing[:start] + merged + existing[end:]
    if not dry_run:
        write_text_lf(agents_md, new_text)
    return True
