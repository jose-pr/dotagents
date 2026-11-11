"""The AOS hook conformance vectors, run through dotagents' own reader.

``hooks.json`` is the specification's file of that name. ``hooks_validation.json`` and
``hooks_compose.json`` hold the hook cases of its ``validation.json`` and ``compose.json`` (the
rest of those files cover what this release does not read); with ``AGENTS_AOS_VECTORS`` naming a
checkout of the specification the same cases are taken from its files.

``OVERLAY.md`` is not read by this release, so the one case that gives hooks in front matter is
the single case not run; ``test_the_overlay_md_case_is_the_only_one_not_run`` pins that.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dotagents import _aos

HERE = Path(__file__).parent / "aos_vectors"
SPEC = os.environ.get("AGENTS_AOS_VECTORS")

#: Cases whose manifest is written in OVERLAY.md front matter, which dotagents does not read yet.
FRONT_MATTER = ("plan-overlay-md", "plan-overlay-md-toml")


def cases(name: str, keep=None) -> "list[dict]":
    if SPEC and keep is not None:
        return [c for c in json.loads((Path(SPEC) / name).read_text(encoding="utf-8"))["cases"] if keep(c)]
    local = name if keep is None else "hooks_" + name
    return json.loads((HERE / local).read_text(encoding="utf-8"))["cases"]


def ids(items: "list[dict]") -> "list[str]":
    return [case["id"] for case in items]


def strict(value):
    """A value with its types made visible: 30 and 30.0, 1 and True differ."""
    if isinstance(value, dict):
        return {key: strict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [strict(item) for item in value]
    return (type(value).__name__, value)


HOOKS = cases("hooks.json")


def hook_plan(case: dict) -> dict:
    facts = dict(case["facts"])
    files = facts["files"]
    manifest = case["manifest"] if "manifest" in case else _aos.read_document(
        files["overlay.cfg"].encode("utf-8"))
    issues = [at for _code, at in _aos.validate(manifest, set(files))]
    facts["files"] = list(files)
    return dict(_aos.hook_plan(manifest, facts), issues=sorted(set(issues)))


def test_the_overlay_md_case_is_the_only_one_not_run():
    unread = [c["id"] for c in HOOKS if c["id"].startswith("plan-") and "manifest" not in c and "overlay.cfg" not in c["facts"]["files"]]
    assert unread == list(FRONT_MATTER)


RUN = [c for c in HOOKS if c["id"] not in FRONT_MATTER]


@pytest.mark.parametrize("case", RUN, ids=ids(RUN))
def test_hooks(case):
    kind = case["id"].split("-", 1)[0]
    if kind == "plan":
        got = hook_plan(case)
    elif kind == "payload":
        got = _aos.hook_payload(case["event"], case["known"])
    elif kind == "output":
        try:
            got = {"output": _aos.hook_result(case["event"], case["run"], case.get("limits", {}))}
        except _aos.HookFailure as failure:
            got = {"failed": failure.cause}
    elif kind == "match":
        got = {"runs": _aos.hook_matches(case["event"], case["match"], case["value"])}
    else:
        got = _aos.fire_event(case["event"], case["runs"], case.get("applies"), case.get("limits"),
                              case.get("os") == "windows")
    assert strict(json.loads(json.dumps(got))) == strict(case["expect"])


VALIDATION = cases("validation.json", lambda c: "hooks" in c["id"])


@pytest.mark.parametrize("case", VALIDATION, ids=ids(VALIDATION))
def test_validation(case):
    files = set(case["files"]) if "files" in case else None
    issues = _aos.validate(case["document"], files)
    if case["expect"]["valid"]:
        assert not issues, issues
        return
    at = case["expect"]["at"]
    if "class" in case["expect"]:
        assert (case["expect"]["class"], at) in issues, issues
    assert any(found == at or found.startswith(at + "/") or (found and at.startswith(found + "/"))
               for _code, found in issues), (at, issues)


COMPOSE = cases("compose.json", lambda c: c["id"].startswith("env-pre-env"))


@pytest.mark.parametrize("case", COMPOSE, ids=ids(COMPOSE))
def test_compose_pre_env(case):
    overlays, files, facts = case["overlays"], case.get("files", {}), case["facts"]
    visited, _missing = _aos.resolve(
        lambda name: overlays[name].get("requires", []) if name in overlays else None, case["requested"])
    active = sorted(visited, key=lambda i: _aos.order_key(
        overlays[i].get("priority", 500), overlays[i].get("name"), i))
    env = _aos.Environment(facts.get("base_env", {}), facts["os"] == "windows")
    for identity in active:
        env.set(_aos.root_variable(identity), facts["roots"][identity])
    reports: "list[list[str]]" = []
    non_defaults: "list[list[str]]" = []
    result: dict = {"active": active}
    try:
        _aos.apply_pre_env(env, active, facts["pre_env"], facts.get("hook_limits"), reports, non_defaults)
        for identity in active:
            text = files.get(identity, {})
            texts = text if isinstance(text, dict) else {}
            secrets = _aos.Secrets(grants=facts.get("grants", {}), host_env=facts.get("host_env", {}),
                                   named=lambda name: facts.get("secrets", {}).get(name),
                                   read_file=lambda key: None, windows=facts["os"] == "windows")
            ctx = _aos.Context(
                os_name=facts["os"], root=facts["roots"][identity], identity=identity,
                read=lambda rel, texts=texts: texts[rel].encode("utf-8") if rel in texts else None,
                secrets=secrets, env_scripts_enabled=facts.get("env_scripts_enabled", True),
                files=list(text), interpreters=facts.get("interpreters", _aos.INTERPRETERS),
                state=facts.get("state"))
            _aos.apply_overlay(env, overlays[identity], ctx, [])
    except _aos.Refused as refused:
        result["env"] = {"error": refused.code, "subject": refused.subject}
    else:
        result["env"] = env.values
    expect = case["expect"]
    if "hook_reports" in expect:
        result["hook_reports"] = reports
    if "hook_non_defaults" in expect:
        result["hook_non_defaults"] = non_defaults
    assert json.loads(json.dumps(result)) == expect
