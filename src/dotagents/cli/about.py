"""`dotagents about` -- what this dotagents is: the CLI's version and every
package bundled with it.

    dotagents-cli 0.5.0
    duho 0.5.0
    pathlib_next 0.9.0

One package per line, `<distribution> <version>`, the CLI first. From a
built ``.pyz`` the list is what ``build-pyz`` vendored (recorded in
``dotagents/_bundle.json`` at build time, since the zipapp carries no
``dist-info``); from a plain install it is the dependencies actually present
(``duho``, ``pathlib_next``, and the ``uri`` extra's packages when installed).
``--json`` adds where it runs from and the Python.
"""

from __future__ import annotations

import json
import sys
from typing import Optional

from duho import Cmd, LoggingArgs

from dotagents import __version__
from dotagents.cli._common import _write_stdout

DISTRIBUTION = "dotagents-cli"
#: Recorded by `build-pyz` next to the package, read here.
BUNDLE_FILE = "_bundle.json"
#: What a plain install reports, in this order, when importable.
RUNTIME_PACKAGES = ("duho", "pathlib_next", "uritools", "netimps", "requests")


def bundle_manifest() -> "Optional[dict]":
    """The ``_bundle.json`` `build-pyz` wrote, or ``None`` (a plain install)."""
    try:
        from importlib.resources import files

        resource = files("dotagents") / BUNDLE_FILE
        if not resource.is_file():
            return None
        return json.loads(resource.read_text(encoding="utf-8"))
    except Exception:
        return None


def installed_packages() -> "dict[str, str]":
    """``{distribution: version}`` for the runtime packages a plain install has."""
    from importlib import metadata

    found: "dict[str, str]" = {}
    for name in RUNTIME_PACKAGES:
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return found


def packages() -> "tuple[dict[str, str], Optional[dict]]":
    """``(packages, bundle)``: the bundle's packages when running from one,
    else the installed runtime packages; the CLI itself is never in either."""
    bundle = bundle_manifest()
    if bundle is not None:
        return dict(bundle.get("packages") or {}), bundle
    return installed_packages(), None


class About(LoggingArgs, Cmd):
    """Print the CLI's version and every package bundled or installed with it,
    one `<distribution> <version>` per line, `dotagents-cli` first."""

    _parsername_ = "about"

    json: bool = False
    "Machine-readable: the same packages plus where this runs from and the Python."
    ("--json",)

    def __call__(self) -> int:
        found, bundle = packages()
        if self.json:
            payload = {
                "distribution": DISTRIBUTION,
                "version": __version__,
                "packages": found,
                "bundle": bundle is not None,
                "location": sys.argv[0] if bundle is not None else _package_location(),
                "python": "%d.%d.%d" % sys.version_info[:3],
                "executable": sys.executable,
            }
            if bundle is not None:
                payload["built"] = {k: v for k, v in bundle.items() if k != "packages"}
            _write_stdout(json.dumps(payload, indent=2) + "\n")
            return 0
        lines = ["%s %s" % (DISTRIBUTION, __version__)]
        lines += ["%s %s" % (name, version) for name, version in found.items()]
        _write_stdout("\n".join(lines) + "\n")
        return 0


def _package_location() -> str:
    import dotagents

    return str(getattr(dotagents, "__file__", "") or "")
