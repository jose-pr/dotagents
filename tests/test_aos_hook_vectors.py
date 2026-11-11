"""The AOS hook conformance vectors, run through what dotagents gives the hook modules: its
adapters over ``coqui_aos`` (``hooks_of``, ``hook_plan``, ``fire_event``, ``apply_pre_env``).

``hooks.json`` is the specification's file of that name. ``hooks_validation.json`` and
``hooks_compose.json`` hold the hook cases of its ``validation.json`` and ``compose.json`` (the
rest of those files are ``coqui_aos``'s to run); with ``AGENTS_AOS_VECTORS`` naming a
checkout of the specification the same cases are taken from its files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dotagents._vendor import coqui_aos

from dotagents import _aos

HERE = Path(__file__).parent / "aos_vectors"
SPEC = os.environ.get("AGENTS_AOS_VECTORS")


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
    tree = coqui_aos.OverlayTree.from_texts(files) if isinstance(files, dict) else coqui_aos.OverlayTree(files)
    manifest = case["manifest"] if "manifest" in case else coqui_aos.read_overlay(tree).manifest
    issues = [issue.at for issue in coqui_aos.validate(manifest, tree)]
    facts["files"] = list(files)
    return dict(_aos.hook_plan(manifest, facts), issues=sorted(set(issues)))


def test_the_front_matter_cases_are_run():
    """A manifest in OVERLAY.md reads like one in overlay.cfg: both forms' cases are in the file."""
    front = [c["id"] for c in HOOKS if c["id"].startswith("plan-") and "OVERLAY.md" in c["facts"]["files"]]
    assert front == ["plan-overlay-md", "plan-overlay-md-toml"]


@pytest.mark.parametrize("case", HOOKS, ids=ids(HOOKS))
def test_hooks(case):
    kind = case["id"].split("-", 1)[0]
    if kind == "plan":
        got = hook_plan(case)
    elif kind == "payload":
        got = coqui_aos.hook_payload(coqui_aos.HookEvent(case["event"]), case["known"])
    elif kind == "output":
        try:
            output = coqui_aos.hook_result(
                coqui_aos.HookEvent(case["event"]), _aos.hook_run(case["run"]), _aos.hook_settings(case.get("limits")))
            got = {"output": {key: value for key, value in coqui_aos.to_data(output).items() if value is not None}}
        except coqui_aos.HookError as failure:
            got = {"failed": failure.failure.value}
    elif kind == "match":
        got = {"runs": coqui_aos.hook_matches(coqui_aos.HookEvent(case["event"]), case["match"], case["value"])}
    else:
        got = _aos.fire_event(case["event"], case["runs"], case.get("applies"), case.get("limits"),
                              case.get("os") == "windows")
    assert strict(json.loads(json.dumps(got))) == strict(case["expect"])


VALIDATION = cases("validation.json", lambda c: "hooks" in c["id"])


@pytest.mark.parametrize("case", VALIDATION, ids=ids(VALIDATION))
def test_validation(case):
    tree = coqui_aos.OverlayTree(case["files"]) if "files" in case else None
    issues = [(issue.code.value, issue.at) for issue in coqui_aos.validate(case["document"], tree)]
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
    active = coqui_aos.composition_order(overlays, coqui_aos.resolve(overlays, case["requested"]).install_order)
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


# What dotagents adds to coqui_aos's fire_event: a run is started only when its turn comes.


def test_a_run_after_a_deny_is_never_started():
    started = []

    def run(source, out):
        def start():
            started.append(source)
            return {"stdout": out}
        return {"source": source, "start": start}

    fired = _aos.fire_event(
        "pre-tool",
        [run("a", ""), run("b", '{"decision": "deny", "reason": "no"}'), run("c", "")],
        ["deny"])
    assert started == ["a", "b"] and fired["decision"] == "deny" and fired["by"] == "b"
    assert fired["ran"] == ["a", "b"]


def test_a_run_after_the_events_bound_is_not_started_and_fails_as_event_timeout():
    started = []

    def slow():
        started.append("a")
        return {"seconds": 200}

    def late():
        started.append("b")
        return {}

    fired = _aos.fire_event("prompt", [{"source": "a", "start": slow}, {"source": "b", "start": late}],
                            limits={"event_timeout": 100})
    assert started == ["a"]
    assert ["hook-failed", "b", "prompt", "event-timeout"] in fired["reports"]


def test_a_guard_after_the_events_bound_still_refuses():
    fired = _aos.fire_event(
        "pre-env",
        [{"source": "a", "seconds": 500}, {"source": "g", "on_failure": "refuse", "start": lambda: {}}],
        limits={"event_timeout": 100})
    assert fired["decision"] == "refuse" and fired["by"] == "g"


def test_output_the_runner_cut_at_the_bound_is_too_large():
    run = {"source": "a", "stdout_bytes": b"x" * 10, "too_large": True}
    try:
        coqui_aos.hook_result(coqui_aos.HookEvent.PROMPT, _aos.hook_run(run, 10), _aos.hook_settings({"max_output": 10}))
    except coqui_aos.HookError as failure:
        assert failure.failure is coqui_aos.HookFailure.TOO_LARGE
    else:
        raise AssertionError("a cut output was accepted")
