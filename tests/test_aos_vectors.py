"""The AOS conformance vectors, run through dotagents' own reader, validator,
name rules, source parser, digest and activation.

The vectors are read from ``tests/aos_vectors/`` (a copy of the
specification's ``tests/vectors/`` at the revision ``REVISION`` names), or
from the directory ``AGENTS_AOS_VECTORS`` names, such as a checkout of the
specification.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from dotagents import _aos, _sources
from dotagents._overlays import Overlay

VECTORS = Path(os.environ.get("AGENTS_AOS_VECTORS") or Path(__file__).parent / "aos_vectors")
FILES = ("reading.json", "validation.json", "names.json", "source.json", "digest.json", "compose.json",
         "effective.json")


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


READING = cases("reading.json")


@pytest.mark.parametrize("case", READING, ids=ids(READING))
def test_reading(case):
    data = base64.b64decode(case["base64"]) if "base64" in case else case["text"].encode("utf-8")
    limits = {"max_" + key[4:]: value for key, value in case.get("limits", {}).items()}
    expect = case["expect"]
    read = {"aos": _aos.read_document, "dotenv": _aos.read_dotenv,
            "env-script": _aos.read_env_script_output}[case["format"]]
    if "error" in expect and "data" in expect:
        try:
            got = read(data, **limits)
        except _aos.ReadError as error:
            assert error.code == expect["error"]
        else:
            assert strict(got) == strict(expect["data"])
    elif "error" in expect:
        allowed = expect["error"] if isinstance(expect["error"], list) else [expect["error"]]
        with pytest.raises(_aos.ReadError) as raised:
            read(data, **limits)
        assert raised.value.code in allowed
    else:
        assert strict(read(data, **limits)) == strict(expect["data"])


VALIDATION = cases("validation.json")


@pytest.mark.parametrize("case", VALIDATION, ids=ids(VALIDATION))
def test_validation(case):
    files = set(case["files"]) if "files" in case else None
    issues = _aos.validate(case["document"], files)
    if case["expect"]["valid"]:
        assert not issues, issues
    else:
        at = case["expect"]["at"]
        assert any(
            found == at or found.startswith(at + "/") or (found and at.startswith(found + "/"))
            for _code, found in issues
        ), (at, issues)


NAMES = cases("names.json")


@pytest.mark.parametrize("case", NAMES, ids=ids(NAMES))
def test_names(case):
    expect = case["expect"]
    assert Overlay.is_valid_name(case["name"]) is expect["valid"]
    if expect["valid"]:
        assert Overlay.normalize_name(case["name"]) == expect["normalized"]
        assert Overlay.root_var_for(case["name"]) == expect["variable"]


SOURCES = cases("source.json")
_KINDS = {"git": "git", "url": "url", "dir": "directory"}


@pytest.mark.parametrize("case", SOURCES, ids=ids(SOURCES))
def test_source(case):
    if "error" in case["expect"]:
        with pytest.raises((ValueError, SystemExit)):
            _sources.parse_spec(case["source"])
    else:
        spec = _sources.parse_spec(case["source"])
        got = {"kind": _KINDS[spec.kind], "location": spec.location, "ref": spec.ref, "path": spec.path}
        assert got == case["expect"]


DIGESTS = {case["id"]: case for case in cases("digest.json")}


def _entries(tree: "list[dict]") -> list:
    out = []
    for item in tree:
        raw = base64.b64decode(item["path_base64"]) if "path_base64" in item else item["path"].encode()
        kind = item.get("type", "file")
        data = b""
        if kind == "file":
            data = base64.b64decode(item["base64"]) if "base64" in item else item["text"].encode()
        out.append((tuple(raw.split(b"/")), kind, data))
    return out


@pytest.mark.parametrize("case_id", sorted(DIGESTS))
def test_digest(case_id):
    case = DIGESTS[case_id]
    expect = case["expect"]
    if "error" in expect:
        with pytest.raises(_aos.DigestError) as raised:
            _aos.digest_entries(_entries(case["tree"]))
        assert raised.value.code == expect["error"]
    else:
        digest, lines = _aos.digest_entries(_entries(case["tree"]))
        assert [line.decode("utf-8") for line in lines] == expect["lines"]
        assert digest == expect["digest"]


def _materialize(case: dict, root: Path) -> bool:
    """Builds the case's tree under ``root``; False when this platform cannot hold it."""
    for item in case["tree"]:
        if "path_base64" in item or "\\" in item["path"] or "\0" in item["path"]:
            return False
        path = root / item["path"]
        kind = item.get("type", "file")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if kind == "dir":
                path.mkdir(parents=True, exist_ok=True)
            elif kind == "symlink":
                os.symlink(item["target"], str(path), target_is_directory=item["path"] == "docs")
            elif kind == "special":
                if not hasattr(os, "mkfifo"):
                    return False
                os.mkfifo(str(path))
            else:
                data = base64.b64decode(item["base64"]) if "base64" in item else item["text"].encode()
                path.write_bytes(data)
        except (OSError, NotImplementedError, ValueError):
            return False
        here = root
        for part in item["path"].split("/"):
            if part not in os.listdir(str(here)):
                return False
            here = here / part
    return True


@pytest.mark.parametrize("case_id", sorted(DIGESTS))
def test_digest_on_this_filesystem(case_id, tmp_path):
    case = DIGESTS[case_id]
    if not _materialize(case, tmp_path):
        pytest.skip("this platform cannot hold the tree")
    files = sum(1 for item in case["tree"] if item.get("type", "file") == "file")
    if len({p.as_posix() for p in tmp_path.rglob("*") if p.is_file()}) < files:
        pytest.skip("a case-insensitive filesystem holds one name for two")
    expect = case["expect"]
    if "error" in expect:
        with pytest.raises(_aos.DigestError) as raised:
            _aos.tree_digest(tmp_path)
        assert raised.value.code == expect["error"]
    else:
        assert _aos.tree_digest(tmp_path) == expect["digest"]


def test_digests_compare_without_case():
    digest = DIGESTS["one-file"]["expect"]["digest"]
    assert _aos.same_digest(digest, "sha256:" + digest[7:].upper())
    with pytest.raises(ValueError):
        _aos.same_digest(digest, "sha1:" + "0" * 40)


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
        interpreters=facts.get("interpreters", _aos.INTERPRETERS),
    )


COMPOSE = cases("compose.json")


@pytest.mark.parametrize("case", COMPOSE, ids=ids(COMPOSE))
def test_compose(case):
    overlays = case["overlays"]
    files = case.get("files", {})
    try:
        visited, missing = _aos.resolve(
            lambda name: overlays[name].get("requires", []) if name in overlays else None, case["requested"]
        )
    except _aos.ResolveError as error:
        assert {"error": error.code, "chain": error.chain} == case["expect"]
        return
    active = sorted(visited, key=lambda i: _aos.order_key(
        overlays[i].get("priority", 500), overlays[i].get("name"), i))
    result = {
        "install_order": visited,
        "active": active,
        "missing": missing,
        "root_variables": {i: Overlay.root_var_for(i) for i in active},
        "routing": [line for i in active for line in overlays[i].get("routing", [])],
        "rules": [[i, path] for i in active for path in _aos.rules_of(overlays[i], files.get(i, []))],
        "context": [[i, path] for i in active for path in _aos.context_of(overlays[i], files.get(i, []))],
    }
    winners: "dict[str, tuple[str, str]]" = {}
    collisions: "set[str]" = set()
    for identity in active:
        for name in _aos.skills_of(overlays[identity], files.get(identity, [])):
            if name.lower() in winners:
                collisions.add(name.lower())
            winners[name.lower()] = (name, identity)
    result["skills"] = {name: identity for name, identity in winners.values()}
    result["skill_collisions"] = sorted(collisions)
    if "facts" in case:
        facts = case["facts"]
        env = _aos.Environment(facts.get("base_env", {}), facts["os"] == "windows")
        for identity in active:
            env.set(Overlay.root_var_for(identity), facts["roots"][identity])
        try:
            for identity in active:
                ctx = _context(overlays[identity], facts, identity, facts["roots"][identity],
                               tree=files.get(identity, []))
                _aos.apply_overlay(env, overlays[identity], ctx, [])
        except _aos.Refused as refused:
            result = {"active": active, "env": {"error": refused.code, "subject": refused.subject}}
        else:
            result = {"active": active, "env": env.values}
    assert json.loads(json.dumps(result)) == case["expect"]


EFFECTIVE = cases("effective.json")


@pytest.mark.parametrize("case", EFFECTIVE, ids=ids(EFFECTIVE))
def test_effective(case):
    manifest, facts = case["manifest"], case["facts"]
    ctx = _context(manifest, facts, facts["identity"], facts["root"])
    result = _aos.effective(
        manifest, ctx, base_env=facts.get("base_env", {}), state=facts.get("state"),
        pinned=facts.get("pinned", False), enforces_network=facts.get("enforces_network", True),
    )
    if result.error is not None:
        got = {"error": result.error.code, "subject": result.error.subject}
    else:
        got = dict(result.plan)
        got["env"] = result.env.values
        got["env_scripts"] = result.scripts
        got["weakenings"] = sorted({tuple(w) for w in result.weakenings})
    assert strict(json.loads(json.dumps(got))) == strict(case["expect"])
