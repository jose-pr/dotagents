"""Activating installed overlays: pins checked, contexts built, weakenings
reported.

An installed overlay is **active** when its manifest's declarations may be
applied: an overlay whose store pins it (:mod:`dotagents._policy`) is active
only while its tree digest is the pin and its copy holds nothing that is not
part of it; an overlay with no pin is active, and reported ``unpinned`` each
time it is activated. A refused overlay contributes nothing at all -- no
environment, context, skills, commands, launchers or servers -- and every
command that walks overlays logs why, once per process.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

import coqui_aos

from dotagents import _aos, _policy

_log_once: "set[str]" = set()


def _warn(logger, message: str, *args: object) -> None:
    import logging

    text = message % args if args else message
    if text not in _log_once:
        _log_once.add(text)
        (logger or logging.getLogger("dotagents")).warning("%s", text)


def _signature(overlay: Any) -> "tuple":
    """What a verification depends on: the install record (rewritten by every
    install) and the manifest. A copy edited by hand within one process is
    caught by the next process."""
    out = []
    for path in (overlay.install_record_path, overlay.manifest_path):
        try:
            st = os.stat(str(path))
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


_DIGESTS: "dict[str, tuple[tuple, Optional[str], Optional[str]]]" = {}


def digest_of(overlay: Any) -> "tuple[Optional[str], Optional[str]]":
    """``(tree digest, None)``, or ``(None, why)`` when it cannot be
    computed. Memoized per process on :func:`_signature`."""
    key = str(overlay.path)
    sig = _signature(overlay)
    cached = _DIGESTS.get(key)
    if cached is not None and cached[0] == sig:
        return cached[1], cached[2]
    try:
        digest, why = overlay.tree_digest(), None
    except coqui_aos.Error as exc:
        digest, why = None, "its tree has no digest (%s)" % exc
    except OSError as exc:
        digest, why = None, "its tree cannot be read (%s)" % exc
    _DIGESTS[key] = (sig, digest, why)
    return digest, why


#: Overlays this process is installing and preparing: activated as if
#: prepared until their preparation ends, so their libs, launchers and root
#: variables are in place for it.
_PREPARING: "set[str]" = set()


@contextlib.contextmanager
def preparing(path: "str | os.PathLike[str]") -> "Iterator[None]":
    """Treat the overlay at ``path`` as prepared while the block runs."""
    key = os.path.normcase(os.path.abspath(str(path)))
    _PREPARING.add(key)
    try:
        yield
    finally:
        _PREPARING.discard(key)


def needs_preparation(overlay: Any) -> bool:
    """Whether ``overlay`` has something to prepare on this OS: a dependency
    file or a setup. Raises :class:`dotagents._aos.Refused` for a
    requirements file only partly hashed."""
    from dotagents import _deps

    return bool(_deps.dependencies(overlay)) or overlay.setup_variant() is not None


def refusal(overlay: Any, *, prepared: bool = True) -> "Optional[str]":
    """Why ``overlay`` may not be activated, or None: a store setting that is
    invalid; pinned, a tree with no digest, a digest that is not its pin or
    an excluded entry in its copy; a requirements file only partly hashed;
    and, with ``prepared``, a digest that is not the one its last
    preparation (dependencies and setup) succeeded for on this OS."""
    try:
        pin = _policy.for_overlay(overlay).pin(overlay.name)
    except _policy.PolicyError as exc:
        return str(exc.code)
    if pin is not None:
        digest, why = digest_of(overlay)
        if digest is None:
            return "pinned, and %s" % why
        if not _aos.same_digest(digest, pin):
            return "its tree digest %s is not its pin %s" % (digest, pin)
        foreign = _aos.foreign_entries(overlay.path)
        if foreign:
            return "its copy holds %s, which is not part of the overlay" % ", ".join(foreign[:5])
    from dotagents import _overlay_hooks

    held = _overlay_hooks.refusal(overlay)
    if held is not None:
        return held
    try:
        needs = needs_preparation(overlay)
    except _aos.Refused as refused:
        return "%s %s (hash every requirement or none)" % (refused.code, refused.subject)
    if prepared and needs and os.path.normcase(os.path.abspath(str(overlay.path))) not in _PREPARING:
        recorded = overlay.read_preparation()
        digest, why = digest_of(overlay)
        if digest is None:
            return why
        if recorded is None:
            return "it is not prepared here (its dependencies and setup have not succeeded)"
        if recorded["digest"].lower() != digest.lower():
            return "it was prepared for %s, not this digest %s" % (recorded["digest"], digest)
    return None


def active(overlays: "Iterable[Any]", logger=None) -> "list[Any]":
    """The ``overlays`` that may be activated, less those whose dependencies
    conflict with another's in one environment; each refusal is logged once."""
    from dotagents import _deps

    out = []
    for overlay in overlays:
        why = refusal(overlay)
        if why is None:
            out.append(overlay)
        else:
            _warn(logger, "overlay %s is refused: %s (fix it with `dotagents overlays sync %s`, or change "
                          "its pin)", overlay.name, why, overlay.name)
    blocked = _deps.conflicts(out)
    for overlay in out:
        if overlay.normalized_name in blocked:
            _warn(logger, "overlay %s is refused: %s", overlay.name, blocked[overlay.normalized_name])
    return [overlay for overlay in out if overlay.normalized_name not in blocked]


def is_pinned(overlay: Any) -> bool:
    try:
        return _policy.for_overlay(overlay).pin(overlay.name) is not None
    except _policy.PolicyError:
        return False


def files(overlay: Any) -> "list[str]":
    """The overlay's member files, which decide its defaults."""
    return _aos.member_paths(overlay.path)


_INTERPRETERS: "list[set[str]]" = []


def interpreters() -> "set[str]":
    """The AOS interpreters this machine can run, found once per process."""
    if not _INTERPRETERS:
        from dotagents._run import can_run

        _INTERPRETERS.append({name for name in _aos.INTERPRETERS if can_run(name)})
    return set(_INTERPRETERS[0])


def state_dir(overlay: Any) -> "Optional[Path]":
    """The state directory of an overlay with a setup or an env script,
    declared or default (they are what writes state), or None."""
    doc = overlay.document()
    members = files(overlay)
    has_script = doc.get("env_scripts") or _aos.default_script(
        doc, "env_scripts", "env", members, _aos.current_os(), interpreters())[0]
    if not (has_script or overlay.setup_variant()):
        return None
    return overlay.state_dir()


def context(overlay: Any, *, host_env: "Optional[dict[str, str]]" = None) -> _aos.Context:
    """How ``overlay`` is applied on this OS: its root, its env files read
    from it, its store's grants and secrets, whether its store lets its env
    scripts run, its member files (its defaults), the interpreters here, its
    state directory and the packages directories of its environments."""
    from dotagents import _deps

    policy = _policy.for_overlay(overlay)
    root = os.path.abspath(str(overlay.path))
    state = state_dir(overlay)

    def read(rel: str) -> "Optional[bytes]":
        if not _aos.valid_relpath(rel):
            return None
        path = os.path.join(root, *rel.split("/"))
        real_root = os.path.realpath(root)
        if not os.path.realpath(path).startswith(real_root.rstrip(os.sep) + os.sep):
            return None  # a link leaving the root
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except OSError:
            return None

    return _aos.Context(
        os_name=_aos.current_os(), root=root, identity=overlay.name, read=read,
        secrets=policy.secrets_for(overlay.name, host_env), env_scripts_enabled=policy.env_scripts(overlay.name),
        files=files(overlay), interpreters=interpreters(), state=None if state is None else str(state),
        packages=_deps.packages_for(overlay),
    )


def describe(weakenings: "Iterable[tuple[str, str, str]]") -> str:
    """``overlay[id subject, ...]`` per overlay, for one report line."""
    by: "dict[str, list[str]]" = {}
    for overlay, ident, subject in weakenings:
        entry = "%s %s" % (ident, subject) if subject else ident
        if entry not in by.setdefault(overlay, []):
            by[overlay].append(entry)
    return "; ".join("%s[%s]" % (name, ", ".join(items)) for name, items in by.items())


def report(logger, weakenings: "list[tuple[str, str, str]]") -> None:
    """Report weakenings ``(overlay, id, subject)``, never silently."""
    if weakenings and logger is not None:
        logger.warning("overlay weakenings: %s", describe(weakenings))
