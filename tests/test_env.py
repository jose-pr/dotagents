"""Characterization tests for `dotagents env` -- FROZEN CONTRACT B (plan 07).

These tests are the SPEC. They pin the observable env-assembly behavior ported
from the precursor `environment.py:get_environment` and MUST stay green through
any refactor of `_env.py`'s shape:

  1. bins prepended to PATH FIRST, before any env eval (`get_bin_paths`);
  2. tier order -- ALL `pre.env(.py)` / `pre.local.env` first, THEN ALL
     `env(.py)` / `local.env`;
  3. within a tier, the contract-A precedence walk (overlays -> system -> user
     -> project -> project-root), reusing `_resolve.py`;
  4. chained eval: each file sees the accumulated env of prior files and LATER
     OVERRIDES EARLIER;
  5. `.py` files are EXECUTED and emit JSON env changes; plain files sourced;
  6. `get_diff` returns only vars differing from the current `os.environ`.

Plus the plan-08 identity/proxy model wired into the env output:
  - `stamp_identity` emits AGENTS_HARNESS/VENDOR/MODEL + AGENT per harness;
  - `AGENTS_PROXY` seeded if unset (AGENTS_WEBFETCH_PROXY_URL else global
    HTTPS/HTTP/ALL_PROXY); existing proxy vars normalized into BOTH cases
    (never creating one that wasn't set; http_proxy stays lowercase-populated);
    AGENTS_PROXY never fanned into global HTTP_PROXY.

tmp dirs only, no network.  Run from repo root: ``python -m pytest tests/``.
"""

import os
import shutil
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from dotagents import _env  # noqa: E402
from dotagents import _resolve  # noqa: E402


HAVE_BASH = shutil.which("bash") is not None


# --------------------------------------------------------------------------
# get_file_paths overlay tier: presence-by-directory, no manifest required (D84).
# --------------------------------------------------------------------------

def test_get_file_paths_resolves_bare_overlay_dir(tmp_path):
    """A BARE overlay dir (no CONTEXT.md, no overlay.toml) with a bin/ subdir is
    resolved by get_file_paths -- the old CONTEXT.md gate (a precursor leftover)
    silently excluded every real dotagents overlay from the Contract-A walk."""
    agents_dir = tmp_path / "agents"
    project_root = tmp_path / "proj"
    bare = agents_dir / "overlays" / "bare"
    (bare / "bin").mkdir(parents=True)
    # Invalid-named dirs are skipped (matches discover_overlays' valid-name rule).
    (agents_dir / "overlays" / ".git" / "bin").mkdir(parents=True)
    (agents_dir / "overlays" / "__pycache__" / "bin").mkdir(parents=True)

    resolved = _resolve.get_file_paths(
        {"default": "bin", "project-root": ""},
        agents_dir=agents_dir,
        project_root=project_root,
        global_scope=False,
        include_missing=True,
    )
    paths = [p for _lvl, p, _root in resolved]
    assert bare / "bin" in paths
    assert agents_dir / "overlays" / ".git" / "bin" not in paths
    assert agents_dir / "overlays" / "__pycache__" / "bin" not in paths


# --------------------------------------------------------------------------
# Helpers to author env.py fixtures. A dotagents env.py reads its base env from
# os.environ (the child inherits the accumulated env) and prints a JSON object
# of the changes it wants applied.
# --------------------------------------------------------------------------

def _py_emit(mapping):
    """A .py file body that unconditionally emits `mapping` as JSON."""
    return "import json, sys\nprint(json.dumps(%r))\n" % (mapping,)


def _py_echo_seen(var, out_key):
    """A .py file that echoes what it SAW for `var` into `out_key` -- proves the
    child observed the accumulated env of earlier files (chaining)."""
    return (
        "import json, os\n"
        "print(json.dumps({%r: os.environ.get(%r, '<unset>')}))\n"
        % (out_key, var)
    )


# --------------------------------------------------------------------------
# Fixture tree: overlays + user + project env files, a mix of .env / .py /
# pre.* / local.*, and a bin dir per level.
# --------------------------------------------------------------------------

@pytest.fixture
def tree(tmp_path):
    agents_dir = tmp_path / "agents"
    project_root = tmp_path / "proj"
    (project_root / ".agents").mkdir(parents=True)

    # Two overlays. An overlay is ANY directory under overlays/ (presence-by-
    # directory, matching discover_overlays; no manifest required -- D84). Overlays
    # sort by name: "aa" before "bb".
    overlays = agents_dir / "overlays"
    for ov in ("aa", "bb"):
        d = overlays / ov
        d.mkdir(parents=True)
        (d / "bin").mkdir()

    (agents_dir / "bin").mkdir(parents=True)
    (project_root / ".agents" / "bin").mkdir(parents=True)

    return agents_dir, project_root


def _run(agents_dir, project_root, base_env, **kw):
    return _env.get_environment(
        agents_dir=agents_dir,
        project_root=project_root,
        base_env=dict(base_env),
        **kw,
    )


# --------------------------------------------------------------------------
# 1. Bins prepended to PATH FIRST (before any env eval).
# --------------------------------------------------------------------------

def test_bins_prepended_to_path_first(tree):
    agents_dir, project_root = tree
    base = {"PATH": os.pathsep.join(["/usr/bin", "/bin"])}
    env = _run(agents_dir, project_root, base)
    parts = env["PATH"].split(os.pathsep)
    # Every level's bin dir EXCEPT project-root is prepended, ahead of the
    # inherited PATH. Overlay bins, user bin, project(.agents) bin all present.
    assert str(agents_dir / "overlays" / "aa" / "bin") in parts
    assert str(agents_dir / "overlays" / "bb" / "bin") in parts
    assert str(agents_dir / "bin") in parts
    assert str(project_root / ".agents" / "bin") in parts
    # The inherited entries remain, AFTER the prepended bins.
    assert parts.index(str(agents_dir / "bin")) < parts.index("/usr/bin")


def test_env_py_sees_prepended_path(tree):
    """A .py file runs AFTER bins are on PATH -- it must observe them (the whole
    point of PATH-first: env scripts can call overlay helpers by name)."""
    agents_dir, project_root = tree
    (agents_dir / "env.py").write_text(_py_echo_seen("PATH", "SEEN_PATH"), encoding="utf-8")
    env = _run(agents_dir, project_root, {"PATH": "/usr/bin"})
    assert str(agents_dir / "bin") in env["SEEN_PATH"]


def test_bin_paths_excludes_project_root(tree):
    agents_dir, project_root = tree
    (project_root / "bin").mkdir()  # a project-ROOT bin must NOT be picked up
    env = _run(agents_dir, project_root, {"PATH": "/usr/bin"})
    assert str(project_root / "bin") not in env["PATH"].split(os.pathsep)


# --------------------------------------------------------------------------
# 2 + 3. Tier order (all pre.* then all env.*) and within-tier precedence walk.
# --------------------------------------------------------------------------

def test_resolved_file_order(tree):
    """Exact resolved (level, filename) list: pre-tier fully before main tier,
    contract-A precedence within each tier."""
    agents_dir, project_root = tree

    def touch(p):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")

    # pre-tier at user + project + project-root
    touch(agents_dir / "pre.env")
    touch(project_root / ".agents" / "pre.local.env")
    touch(project_root / "pre.local.env")
    # main-tier at overlays + user + project + project-root
    touch(agents_dir / "overlays" / "aa" / "env")
    touch(agents_dir / "env")
    touch(project_root / ".agents" / "local.env")
    touch(project_root / "local.env")

    resolved = _env.resolve_env_files(
        agents_dir=agents_dir, project_root=project_root, global_scope=False
    )
    order = [(lvl, p.name) for lvl, p, _ in resolved]

    # The pre-tier (all of it) precedes the main tier.
    pre_end = max(i for i, (_l, n) in enumerate(order) if n.startswith("pre."))
    main_start = min(i for i, (_l, n) in enumerate(order) if not n.startswith("pre."))
    assert pre_end < main_start

    # Within the main tier: overlay(s) -> user -> project -> project-root.
    main = [(lvl, n) for lvl, n in order if not n.startswith("pre.")]
    levels = [lvl for lvl, _ in main]
    assert levels.index("aa") < levels.index("user")
    assert levels.index("user") < levels.index("project")
    assert levels.index("project") < levels.index("project-root")


def test_global_scope_drops_project_levels(tree):
    agents_dir, project_root = tree
    (project_root / ".agents" / "env").write_text("", encoding="utf-8")
    (agents_dir / "env").write_text("", encoding="utf-8")
    resolved = _env.resolve_env_files(
        agents_dir=agents_dir, project_root=project_root, global_scope=True
    )
    levels = {lvl for lvl, _p, _r in resolved}
    assert "project" not in levels and "project-root" not in levels
    assert "user" in levels


# --------------------------------------------------------------------------
# 4. Chained eval: later overrides earlier; each file sees prior accumulation.
# --------------------------------------------------------------------------

def test_later_overrides_earlier_across_tiers(tree):
    """A main-tier file overrides a pre-tier file (pre runs first)."""
    agents_dir, project_root = tree
    (agents_dir / "pre.env.py").write_text(_py_emit({"K": "from_pre"}), encoding="utf-8")
    (agents_dir / "env.py").write_text(_py_emit({"K": "from_main"}), encoding="utf-8")
    env = _run(agents_dir, project_root, {"PATH": "/usr/bin"})
    assert env["K"] == "from_main"


def test_later_overrides_earlier_within_tier(tree):
    """user wins over overlay (later in the precedence walk)."""
    agents_dir, project_root = tree
    (agents_dir / "overlays" / "aa" / "env.py").write_text(
        _py_emit({"K": "overlay"}), encoding="utf-8"
    )
    (agents_dir / "env.py").write_text(_py_emit({"K": "user"}), encoding="utf-8")
    env = _run(agents_dir, project_root, {"PATH": "/usr/bin"})
    assert env["K"] == "user"


def test_chained_child_sees_prior_env(tree):
    """The second .py must SEE the var the first emitted (accumulation)."""
    agents_dir, project_root = tree
    (agents_dir / "pre.env.py").write_text(_py_emit({"FIRST": "v1"}), encoding="utf-8")
    (agents_dir / "env.py").write_text(_py_echo_seen("FIRST", "SEEN_FIRST"), encoding="utf-8")
    env = _run(agents_dir, project_root, {"PATH": "/usr/bin"})
    assert env["SEEN_FIRST"] == "v1"


# --------------------------------------------------------------------------
# 5. .py executed (JSON changes); plain files sourced.
# --------------------------------------------------------------------------

def test_py_json_changes_applied(tree):
    agents_dir, project_root = tree
    (agents_dir / "env.py").write_text(
        _py_emit({"FROM_PY": "yes", "NUM": "42"}), encoding="utf-8"
    )
    env = _run(agents_dir, project_root, {"PATH": "/usr/bin"})
    assert env["FROM_PY"] == "yes"
    assert env["NUM"] == "42"


def test_py_nonzero_is_skipped_not_fatal(tree):
    agents_dir, project_root = tree
    (agents_dir / "env.py").write_text(
        "import sys\nsys.stderr.write('boom')\nsys.exit(3)\n", encoding="utf-8"
    )
    # A failing env.py contributes nothing and does not abort assembly.
    env = _run(agents_dir, project_root, {"PATH": "/usr/bin"})
    assert "PATH" in env  # assembly still produced a result


@pytest.mark.skipif(not HAVE_BASH, reason="plain .env sourcing needs bash")
def test_plain_env_file_sourced(tree):
    agents_dir, project_root = tree
    (agents_dir / "env").write_text("export SOURCED=hello\n", encoding="utf-8")
    env = _run(agents_dir, project_root, {"PATH": "/usr/bin"})
    assert env.get("SOURCED") == "hello"


# --------------------------------------------------------------------------
# 6. get_diff: only vars differing from the current os.environ.
# --------------------------------------------------------------------------

def test_get_diff_only_changed(tree):
    agents_dir, project_root = tree
    (agents_dir / "env.py").write_text(
        _py_emit({"NEWVAR": "new", "SAME": "keep"}), encoding="utf-8"
    )
    base = {"PATH": "/usr/bin", "SAME": "keep"}
    diff = _env.get_diff(
        agents_dir=agents_dir, project_root=project_root, base_env=base
    )
    assert diff.get("NEWVAR") == "new"
    # SAME already equals base -> excluded from the diff.
    assert "SAME" not in diff


# --------------------------------------------------------------------------
# Identity wiring (plan 08): stamp_identity emits AGENTS_*/AGENT.
# --------------------------------------------------------------------------

def test_identity_stamped_claude(tree):
    agents_dir, project_root = tree
    base = {"PATH": "/usr/bin", "CLAUDECODE": "1", "ANTHROPIC_MODEL": "claude-opus-4-8"}
    env = _run(agents_dir, project_root, base)
    assert env["AGENTS_HARNESS"] == "claude-code"
    assert env["AGENT"] == "claude-code"
    assert env["AGENTS_VENDOR"] == "anthropic"
    assert env["AGENTS_MODEL"] == "claude-opus-4-8"


def test_identity_no_blanket_rewrite_artifacts(tree):
    agents_dir, project_root = tree
    base = {"PATH": "/usr/bin", "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "abc"}
    env = _run(agents_dir, project_root, base)
    # The dropped precursor rewrite would have produced AGENTS_CODE_SESSION_ID.
    assert "AGENTS_CODE_SESSION_ID" not in env


def test_identity_does_not_clobber_user_value(tree):
    agents_dir, project_root = tree
    base = {"PATH": "/usr/bin", "CLAUDECODE": "1", "AGENTS_HARNESS": "custom"}
    env = _run(agents_dir, project_root, base)
    # A user-set AGENTS_HARNESS is respected -> not re-stamped (absent from diff).
    assert env.get("AGENTS_HARNESS", "custom") == "custom"


def test_env_files_win_over_identity(tree):
    """An env file that sets AGENTS_MODEL should override the stamped default --
    identity is seeded, files run in the chain and can override."""
    agents_dir, project_root = tree
    (agents_dir / "env.py").write_text(
        _py_emit({"AGENTS_MODEL": "pinned-by-file"}), encoding="utf-8"
    )
    base = {"PATH": "/usr/bin", "CLAUDECODE": "1", "ANTHROPIC_MODEL": "claude-opus-4-8"}
    env = _run(agents_dir, project_root, base)
    assert env["AGENTS_MODEL"] == "pinned-by-file"


# --------------------------------------------------------------------------
# Proxy model (plan 08).
# --------------------------------------------------------------------------

def test_proxy_seed_from_global_https(tree):
    agents_dir, project_root = tree
    base = {"PATH": "/usr/bin", "HTTPS_PROXY": "http://p:8080"}
    env = _run(agents_dir, project_root, base)
    assert env["AGENTS_PROXY"] == "http://p:8080"


def test_proxy_seed_prefers_webfetch_var(tree):
    agents_dir, project_root = tree
    base = {
        "PATH": "/usr/bin",
        "HTTPS_PROXY": "http://global:8080",
        "AGENTS_WEBFETCH_PROXY_URL": "http://agent:9090",
    }
    env = _run(agents_dir, project_root, base)
    assert env["AGENTS_PROXY"] == "http://agent:9090"


def test_proxy_existing_agents_proxy_respected(tree):
    agents_dir, project_root = tree
    base = {"PATH": "/usr/bin", "AGENTS_PROXY": "http://mine:1", "HTTPS_PROXY": "http://x:2"}
    env = _run(agents_dir, project_root, base)
    # Already set -> not re-seeded, so it's absent from the changes (or unchanged).
    assert "AGENTS_PROXY" not in env or env["AGENTS_PROXY"] == "http://mine:1"


def test_proxy_not_fanned_to_global(tree):
    """Seeding AGENTS_PROXY must NOT create a global HTTP_PROXY that wasn't set."""
    agents_dir, project_root = tree
    base = {"PATH": "/usr/bin", "HTTPS_PROXY": "http://p:8080"}  # no HTTP_PROXY
    env = _run(agents_dir, project_root, base)
    assert "HTTP_PROXY" not in env  # not fanned out


def test_proxy_case_normalized_both_ways(tree):
    """An existing UPPER proxy var mirrors into lowercase and vice-versa; only
    the missing case is filled (never a var that wasn't set)."""
    agents_dir, project_root = tree
    base = {"PATH": "/usr/bin", "HTTPS_PROXY": "http://up:8080", "no_proxy": "localhost"}
    env = _run(agents_dir, project_root, base)
    full = dict(base)
    full.update(env)
    # HTTPS_PROXY (upper set) -> https_proxy filled to match.
    assert full["https_proxy"] == "http://up:8080"
    # no_proxy (lower set) -> NO_PROXY filled to match.
    assert full["NO_PROXY"] == "localhost"


def test_proxy_case_does_not_create_absent(tree):
    agents_dir, project_root = tree
    base = {"PATH": "/usr/bin"}  # no proxy vars at all
    env = _run(agents_dir, project_root, base)
    for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
        assert k not in env


def test_http_proxy_stays_lowercase_populated(tree):
    """httpoxy: curl reads http_proxy lowercase-only, so when HTTP_PROXY is set
    the lowercase must be populated too."""
    agents_dir, project_root = tree
    base = {"PATH": "/usr/bin", "HTTP_PROXY": "http://p:8080"}
    env = _run(agents_dir, project_root, base)
    full = dict(base)
    full.update(env)
    assert full["http_proxy"] == "http://p:8080"
