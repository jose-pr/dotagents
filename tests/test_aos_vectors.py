"""The AOS conformance vectors for what dotagents applies itself: one overlay's environment and
the package environments overlays share. Reading, validation, names, sources and digests are
:mod:`coqui_aos`'s, and its own tests run those vectors.

The vectors are read from ``tests/aos_vectors/`` (a copy of the
specification's ``tests/vectors/`` at the revision ``REVISION`` names), or
from the directory ``AGENTS_AOS_VECTORS`` names, such as a checkout of the
specification.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import coqui_aos
import pytest

from dotagents import _aos
from dotagents._overlays import Overlay

VECTORS = Path(os.environ.get("AGENTS_AOS_VECTORS") or Path(__file__).parent / "aos_vectors")
FILES = ("compose.json", "effective.json")


def cases(name: str) -> "list[dict]":
    return json.loads((VECTORS / name).read_text(encoding="utf-8"))["cases"]


def ids(items: "list[dict]") -> "list[str]":
    return [case["id"] for case in items]


def strict(value):
    """A value with its types made visible: 30 and 30.0, 1 and True differ."""
    if isinstance(value, dict):
        return {key: strict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [strict(item) for item in value]
    return (type(value).__name__, value)


def test_every_vector_file_is_run():
    assert {path.name for path in VECTORS.glob("*.json")} == set(FILES)


def test_the_vendored_copy_names_its_revision():
    revision = (Path(__file__).parent / "aos_vectors" / "REVISION").read_text(encoding="utf-8").split()
    assert revision[0] == "Boriken-Dev/agent-overlay-spec" and len(revision[1]) == 40




def _context(manifest: dict, facts: dict, identity: str, root: str, tree=None) -> _aos.Context:
    windows = facts["os"] == "windows"
    files = facts.get("files", {})
    secret_files = facts.get("secret_files")
    secrets = _aos.Secrets(
        grants=facts.get("grants", {}),
        host_env=facts.get("host_env", {}),
        named=lambda name: facts.get("secrets", {}).get(name),
        read_file=(lambda key: None) if secret_files is None else secret_files.get,
        providers=facts.get("providers", ["env", "file"]),
        provider_values=facts.get("provider_values", {}),
        windows=windows,
    )
    return _aos.Context(
        os_name=facts["os"], root=root, identity=identity,
        read=lambda rel: files[rel].encode("utf-8") if rel in files else None,
        secrets=secrets, env_scripts_enabled=facts.get("env_scripts_enabled", True),
        files=list(files) if tree is None else tree,
        interpreters=facts.get("interpreters", _aos.INTERPRETERS), state=facts.get("state"),
        packages=facts.get("packages"),
    )




COMPOSE = [case for case in cases("compose.json") if "facts" in case]


@pytest.mark.parametrize("case", COMPOSE, ids=ids(COMPOSE))
def test_compose(case):
    overlays = case["overlays"]
    files = case.get("files", {})
    try:
        resolution = coqui_aos.resolve(overlays, case["requested"])
    except coqui_aos.Cycle:
        return
    active = coqui_aos.composition_order(overlays, resolution.install_order)
    facts = case["facts"]
    expect = case["expect"]

    def texts(identity):
        tree = files.get(identity, [])
        # A mapping of path to text also gives the files an overlay reads.
        return tree if isinstance(tree, dict) else {}

    def deps(identity):
        text = texts(identity)
        return _aos.dependencies_of(overlays[identity], list(files.get(identity, [])), facts["os"],
                                    lambda rel: text[rel].encode("utf-8") if rel in text else None)

    reduced = {"active": active}
    try:
        members = [(i, d, texts(i).get(d["file"])) for i in active for d in deps(i)]
        installed = _aos.environments(members, [(i, d) for i in facts.get("removed", []) for d in deps(i)])
    except _aos.Refused as refused:
        reduced["env"] = {"error": refused.code, "subject": refused.subject}
    else:
        for built in installed["environments"]:
            del built["key"]
        active = [i for i in active if i not in installed["blocked"]]
        reduced["active"] = active
        for key in ("environments", "conflicts", "blocked", "left"):
            if key in expect:
                reduced[key] = installed[key]
        env = _aos.Environment(facts.get("base_env", {}), facts["os"] == "windows")
        for identity in active:
            env.set(Overlay.root_var_for(identity), facts["roots"][identity])
        try:
            for identity in active:
                ctx = _context(overlays[identity],
                               dict(facts, files=texts(identity),
                                    packages=facts.get("packages", {}).get(identity, {})),
                               identity, facts["roots"][identity], tree=list(files.get(identity, [])))
                _aos.apply_overlay(env, overlays[identity], ctx, [])
        except _aos.Refused as refused:
            reduced["env"] = {"error": refused.code, "subject": refused.subject}
        else:
            reduced["env"] = env.values
    assert json.loads(json.dumps(reduced)) == expect


EFFECTIVE = cases("effective.json")
# What a preparation does is coqui_aos's, and its own tests run those vectors.
PREPARATION = ("setup_phase", "setup_arguments", "steps", "record", "activates")


@pytest.mark.parametrize("case", EFFECTIVE, ids=ids(EFFECTIVE))
def test_effective(case):
    manifest, facts = case["manifest"], case["facts"]
    ctx = _context(manifest, facts, facts["identity"], facts["root"])
    result = _aos.effective(
        manifest, ctx, base_env=facts.get("base_env", {}),
        pinned=facts.get("pinned", False), enforces_network=facts.get("enforces_network", True),
    )
    expect = case["expect"]
    if result.error is not None:
        got = {"error": result.error.code, "subject": result.error.subject}
    else:
        got = dict(result.plan)
        got["env"] = result.env.values
        got["env_scripts"] = result.scripts
        got["weakenings"] = sorted({tuple(w) for w in result.weakenings})
        if "change" in facts:
            expect = {key: value for key, value in expect.items() if key not in PREPARATION}
    assert strict(json.loads(json.dumps(got))) == strict(expect)
