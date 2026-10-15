"""tools/cloud-setup.sh: where it fetches the private-sync overlay from, and
how it installs what a store's registry names.

Only those pieces are run (the rest of the script clones and installs): they
are read out of the script and evaluated in a real bash, so the test follows
the script rather than a copy of it.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

from _shell import real_bash

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "cloud-setup.sh"


def _overlay_source(env: "dict[str, str]") -> "tuple[str, str]":
    bash = real_bash()
    if bash is None:
        pytest.skip("no POSIX bash")
    text = SCRIPT.read_text(encoding="utf-8")
    lines = re.findall(r"(?m)^_dg_ovl_(?:remote|ref)=.*$", text)
    assert len(lines) == 2, lines
    script = "\n".join(lines) + '\nprintf "%s\\n%s\\n" "$_dg_ovl_remote" "$_dg_ovl_ref"\n'
    clean = {
        k: v for k, v in os.environ.items()
        if not re.match(r"(DOTAGENTS|AGENTS)_OVERLAYS_RE(MOTE|F)$", k)
    }
    clean.update(env)
    proc = subprocess.run([bash, "-c", script], capture_output=True, text=True, env=clean)
    assert proc.returncode == 0, proc.stderr
    remote, ref = proc.stdout.splitlines()
    return remote, ref


def test_defaults_to_this_repo_s_repo_branch():
    assert _overlay_source({}) == ("https://github.com/jose-pr/dotagents.git", "repo")


def test_reads_the_agents_names():
    got = _overlay_source({"AGENTS_OVERLAYS_REMOTE": "https://example.invalid/o.git",
                           "AGENTS_OVERLAYS_REF": "main"})
    assert got == ("https://example.invalid/o.git", "main")


def test_the_old_dotagents_names_still_work_and_lose_to_the_new_ones():
    old = {"DOTAGENTS_OVERLAYS_REMOTE": "https://example.invalid/old.git",
           "DOTAGENTS_OVERLAYS_REF": "old"}
    assert _overlay_source(old) == ("https://example.invalid/old.git", "old")
    both = dict(old, AGENTS_OVERLAYS_REMOTE="https://example.invalid/new.git",
                AGENTS_OVERLAYS_REF="new")
    assert _overlay_source(both) == ("https://example.invalid/new.git", "new")


def _registry_step(tmp_path: Path, *, registry: "str | None", cli_exit: int = 0):
    """Run step 4's registry block with ``dg_cli`` stubbed to record its
    arguments and exit ``cli_exit``. ``(calls, stdout)``."""
    bash = real_bash()
    if bash is None:
        pytest.skip("no POSIX bash")
    text = SCRIPT.read_text(encoding="utf-8")
    block = re.search(r"(?ms)^for _dg_f in dotagents\.toml.*?^done$", text)
    assert block, "the registry block moved or was renamed"
    store = tmp_path / "store"
    store.mkdir()
    if registry:
        (store / registry).write_text("[overlays]\n", encoding="utf-8")
    log = tmp_path / "calls"
    script = (
        'dg_cli() { printf "%%s\\n" "$*" >> "$LOG"; return %d; }\n' % cli_exit
        + 'AGENTS_DIR="$STORE"\n' + block.group(0) + "\n"
    )
    env = dict(os.environ, STORE=store.as_posix(), LOG=log.as_posix())
    proc = subprocess.run([bash, "-c", script], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return calls, proc.stdout


def test_a_registry_store_gets_every_overlay_it_names(tmp_path):
    calls, out = _registry_step(tmp_path, registry="dotagents.toml")
    assert calls == ["overlays add * --agents-dir %s -g" % (tmp_path / "store").as_posix()]
    assert "installing the overlays dotagents.toml names" in out


def test_a_partial_install_is_reported_and_the_setup_goes_on(tmp_path):
    calls, out = _registry_step(tmp_path, registry="dotagents.json", cli_exit=1)
    assert len(calls) == 1 and "did not install" in out


def test_a_store_without_a_registry_installs_nothing_here(tmp_path):
    calls, _out = _registry_step(tmp_path, registry=None)
    assert calls == []
