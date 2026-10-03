"""leak_check.py finds what it claims to, says when a class of pattern did not
run, and never reports "not examined" as PASS or FAIL.

Temp directories and a hermetic pattern file only. The strings the tool hunts
for are assembled from pieces, so this file does not contain them.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

pytest = __import__("pytest")
duho = pytest.importorskip("duho")

TOOL = Path(__file__).resolve().parents[1] / "cmds" / "leak_check.py"
_spec = importlib.util.spec_from_file_location("leak_check", TOOL)
leak_check = importlib.util.module_from_spec(_spec)
# duho resolves a command's field annotations through sys.modules.
sys.modules[_spec.name] = leak_check
_spec.loader.exec_module(leak_check)

NOTES = "." + "agents"  # the private notes directory
PHASE = "Phase" + " 2"
DECISION = "D" + "46"
TRAILER = "Claude" + "-Session:"

# A marker no fixture contains by accident, and an allowlisted string that
# contains it: the pair is what makes the blanking test mean something.
MARKER = "zzprivatezz"
ALLOWED = "https://example.invalid/zzprivatezz"
INFRA_ID = "hermetic-host"
INFRA_RE = r"(?<![0-9A-Za-z_])zzlabhostzz(?![0-9A-Za-z_])"
INFRA_HIT = "zzlabhostzz"

GIT_ENV = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)


def git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-C", str(cwd), *args],
        check=True, capture_output=True, env=GIT_ENV,
    )


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def run(target: str, *flags: str) -> tuple[int, str]:
    """Build the command from an argument vector and call it."""
    command = duho.parse(leak_check.LeakCheck, [target, *flags])
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = command()
    return code, buffer.getvalue()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="leakchk-test-")
        self.root = Path(self._tmp.name).resolve()
        # Without this every test would read the machine's own pattern file.
        self.patterns = self.root / "patterns.json"
        write(self.patterns, json.dumps({
            "personal": [MARKER],
            "public_allowlist": [ALLOWED],
            "infra": [{"id": INFRA_ID, "why": "fixture", "regex": INFRA_RE}],
        }) + "\n")
        self._saved_env = os.environ.get(leak_check.PATTERNS_ENV)
        os.environ[leak_check.PATTERNS_ENV] = str(self.patterns)

    def tearDown(self) -> None:
        if self._saved_env is None:
            os.environ.pop(leak_check.PATTERNS_ENV, None)
        else:
            os.environ[leak_check.PATTERNS_ENV] = self._saved_env
        self._tmp.cleanup()

    def empty_plans(self) -> Path:
        """A plans directory with no plans, isolating a test from whatever
        plans exist above the temp directory."""
        plans = self.root / "no-plans"
        plans.mkdir(exist_ok=True)
        return plans

    def repo(self, *files: tuple[str, str], name: str = "crate") -> Path:
        crate = self.root / name
        if not crate.exists():
            crate.mkdir()
            git(crate, "init", "-q")
        for rel, text in files:
            write(crate / rel, text)
        return crate

    def scan(self, target: Path, *extra: str) -> tuple[int, str]:
        return run(str(target), "--plans", str(self.empty_plans()), *extra)


class PlanHarvest(Base):
    def test_plans_are_found_above_the_repo(self) -> None:
        """A repository inside a workspace: the plans sit at the workspace
        root, outside it."""
        write(self.root / NOTES / "plans/completed/network_efficiency.md", "x\n")
        crate = self.repo(("src/transport.rs", "// see network_efficiency.md\n"))
        code, out = run(str(crate))
        self.assertEqual(code, leak_check.EXIT_HITS, out)
        self.assertIn("plan basename 'network_efficiency.md'", out)

    def test_nested_plans_are_harvested(self) -> None:
        write(self.root / NOTES / "plans/completed/rdp_backend.md", "x\n")
        write(self.root / NOTES / "plans/on-hold/av1_encode.md", "x\n")
        distinctive, generic = leak_check.harvest_plan_names(
            self.root / NOTES / "plans")
        self.assertEqual(distinctive, {"rdp_backend.md", "av1_encode.md"})
        self.assertEqual(generic, set())

    def test_a_generic_basename_warns_and_a_distinctive_one_fails(self) -> None:
        write(self.root / NOTES / "plans/terminal.md", "x\n")
        write(self.root / NOTES / "plans/remote_engine.md", "x\n")
        crate = self.repo(("docs.md", "See terminal.md for the widget.\n"))
        code, out = run(str(crate))
        self.assertEqual(code, leak_check.EXIT_OK, out)
        self.assertIn("generic plan basename 'terminal.md'", out)

        write(crate / "docs.md", "See remote_engine.md for the widget.\n")
        code, out = run(str(crate))
        self.assertEqual(code, leak_check.EXIT_HITS, out)

    def test_no_plans_is_incomplete_not_a_bare_pass(self) -> None:
        crate = self.repo(("src/lib.rs", "// nothing private here\n"))
        code, out = self.scan(crate)
        self.assertEqual(code, leak_check.EXIT_OK, out)
        self.assertIn("NO PLAN BASENAMES WERE HARVESTED", out)
        # The verdict line carries it too: a truncated output shows only the end.
        self.assertIn("INCOMPLETE", out.strip().splitlines()[-1])


class WorkingTree(Base):
    def test_an_untracked_file_is_scanned(self) -> None:
        crate = self.repo(("src/tracked.rs", "// clean\n"))
        git(crate, "add", "src/tracked.rs")
        git(crate, "commit", "-qm", "add tracked")
        write(crate / "src/pixels.rs", "//! Layout as [[%s]] describes.\n" % DECISION)
        code, out = self.scan(crate)
        self.assertEqual(code, leak_check.EXIT_HITS, out)
        self.assertIn("src/pixels.rs:1", out)

    def test_a_gitignored_file_is_not_scanned(self) -> None:
        """The ignored directory is deliberately not one the built-in prune
        list would skip anyway, so this proves git's ignore rules are used."""
        crate = self.repo(
            (".gitignore", "vendor-notes/\n"),
            ("vendor-notes/scratch.md", "// %s/plans/secret.md\n" % NOTES),
            ("src/lib.rs", "// clean\n"))
        code, out = self.scan(crate)
        self.assertEqual(code, leak_check.EXIT_OK, out)
        self.assertNotIn("scratch.md", out)


class NotARepo(Base):
    def test_a_non_repo_is_checked_with_its_own_gitignore(self) -> None:
        app = self.root / "app"
        write(app / ".gitignore", "build/\n")
        write(app / "lib/main.dart", "// see %s/findings/x.md\n" % NOTES)
        write(app / "build/gen.dart", "// [[%s]] in build output\n" % DECISION)
        code, out = self.scan(app)
        self.assertEqual(code, leak_check.EXIT_HITS, out)
        self.assertIn("NOT a git repository", out)
        self.assertIn("lib/main.dart:1: .agents path", out)
        self.assertNotIn("build/gen.dart", out)

    def test_a_clean_non_repo_passes(self) -> None:
        app = self.root / "app"
        write(app / "lib/main.dart", "// nothing private\n")
        code, out = self.scan(app)
        self.assertEqual(code, leak_check.EXIT_OK, out)


class CannotCheckExit(Base):
    def test_a_missing_directory_is_exit_3(self) -> None:
        code, out = self.scan(self.root / "does-not-exist")
        self.assertEqual(code, leak_check.EXIT_CANNOT_CHECK, out)
        self.assertIn("CANNOT CHECK", out)

    def test_a_directory_with_no_readable_file_is_exit_3(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        code, out = self.scan(empty)
        self.assertEqual(code, leak_check.EXIT_CANNOT_CHECK, out)
        self.assertIn("NOT ONE FILE WAS READ", out)


class BuiltInPatterns(Base):
    def test_the_bare_public_filename_is_not_a_hit(self) -> None:
        crate = self.repo(("pyproject.toml", 'include = ["AGENTS.md"]\n'))
        code, out = self.scan(crate)
        self.assertEqual(code, leak_check.EXIT_OK, out)

    def test_notes_path_matches_either_slash_but_not_an_attribute(self) -> None:
        crate = self.repo(
            ("src/lib.rs", "// a\n// %s\\env\n// cfg%s = 3;\n" % (NOTES, NOTES)))
        code, out = self.scan(crate)
        self.assertEqual(code, leak_check.EXIT_HITS, out)
        self.assertIn("src/lib.rs:2: .agents path", out)
        self.assertNotIn("src/lib.rs:3", out)

    def test_decision_ids_both_spellings_and_no_false_positive(self) -> None:
        crate = self.repo(("src/lib.rs", "// as %s says\n// as [[%s]] says\n"
                                         "// on AMD64 and 0xD11\n"
                           % (DECISION, DECISION)))
        code, out = self.scan(crate)
        self.assertEqual(code, leak_check.EXIT_HITS, out)
        self.assertIn("src/lib.rs:1: decision id '%s'" % DECISION, out)
        self.assertIn("src/lib.rs:2: decision id '[[%s]]'" % DECISION, out)
        self.assertNotIn("src/lib.rs:3", out)

    def test_phase_phrasing(self) -> None:
        crate = self.repo(("src/lib.rs", "//! %s additions.\n" % PHASE))
        code, out = self.scan(crate)
        self.assertEqual(code, leak_check.EXIT_HITS, out)
        self.assertIn("'Phase N'", out)


class CommitMessages(Base):
    def commit(self, message: str) -> Path:
        crate = self.repo(("src/lib.rs", "// clean\n"))
        git(crate, "add", ".")
        git(crate, "commit", "-qm", message)
        return crate

    def test_a_session_trailer_is_caught(self) -> None:
        code, out = self.scan(self.commit("feat: thing\n\n%s abc123" % TRAILER))
        self.assertEqual(code, leak_check.EXIT_HITS, out)
        self.assertIn("session trailer '%s'" % TRAILER, out)

    def test_a_co_author_trailer_is_caught(self) -> None:
        trailer = "Co-Authored" + "-By: Some Assistant <a@example.invalid>"
        code, out = self.scan(self.commit("feat: thing\n\n%s" % trailer))
        self.assertEqual(code, leak_check.EXIT_HITS, out)

    def test_prose_mentioning_an_assistant_is_not_a_trailer(self) -> None:
        code, out = self.scan(self.commit("feat: written with claude, no trailer"))
        self.assertEqual(code, leak_check.EXIT_OK, out)

    def test_generated_with_is_a_hit_only_as_a_footer(self) -> None:
        prose = "Generated with" + " the project's own identity, not the default"
        code, out = self.scan(self.commit("fix: identity\n\n%s" % prose))
        self.assertEqual(code, leak_check.EXIT_OK, out)

        footer = "Generated with" + " [Some Tool](https://example.invalid)"
        crate = self.repo(("src/more.rs", "// clean\n"))
        git(crate, "add", ".")
        git(crate, "commit", "-qm", "feat: more\n\n%s" % footer)
        code, out = self.scan(crate)
        self.assertEqual(code, leak_check.EXIT_HITS, out)


class PersonalMarkers(Base):
    def test_a_marker_is_a_hit_and_names_the_line(self) -> None:
        crate = self.repo(("src/lib.rs", "// clean\n// /home/%s/devel\n" % MARKER))
        code, out = self.scan(crate)
        self.assertEqual(code, leak_check.EXIT_HITS, out)
        self.assertIn("src/lib.rs:2: personal marker '%s'" % MARKER, out)
        self.assertNotIn("src/lib.rs:1", out)

    def test_an_allowlisted_string_is_blanked_before_matching(self) -> None:
        crate = self.repo(
            ("src/lib.rs", "// see %s\n// /home/%s\n" % (ALLOWED, MARKER)))
        code, out = self.scan(crate)
        self.assertEqual(code, leak_check.EXIT_HITS, out)
        self.assertIn("src/lib.rs:2: personal marker", out)
        self.assertNotIn("src/lib.rs:1", out)

    def test_no_pattern_file_is_incomplete_not_a_bare_pass(self) -> None:
        crate = self.repo(("src/lib.rs", "// nothing private\n"))
        code, out = self.scan(crate, "--patterns", str(self.root / "nope.json"))
        self.assertEqual(code, leak_check.EXIT_OK, out)
        self.assertIn("NO PERSONAL MARKERS WERE LOADED", out)
        self.assertIn("INCOMPLETE", out.strip().splitlines()[-1])

    def test_a_malformed_pattern_file_does_not_crash(self) -> None:
        bad = self.root / "bad.json"
        write(bad, "{not json\n")
        markers, _allowlist, gap = leak_check.load_personal_patterns(bad)
        self.assertEqual(markers, [])
        self.assertIsNotNone(gap)


class InfraRules(Base):
    def patterns_with(self, infra: list) -> Path:
        path = self.root / "infra-only.json"
        write(path, json.dumps({"personal": [MARKER], "infra": infra}) + "\n")
        return path

    def test_a_rule_hit_names_the_line_and_the_rule(self) -> None:
        crate = self.repo(
            ("src/lib.rs", "//! A console.\n//! Measured against %s.\n" % INFRA_HIT))
        code, out = self.scan(crate)
        self.assertEqual(code, leak_check.EXIT_HITS, out)
        self.assertIn("src/lib.rs:2: internal infra [%s] '%s'"
                      % (INFRA_ID, INFRA_HIT), out)
        self.assertNotIn("src/lib.rs:1", out)

    def test_the_allowlist_applies_to_infra_rules_too(self) -> None:
        allowed = "https://example.invalid/%s" % INFRA_HIT
        write(self.patterns, json.dumps({
            "personal": [MARKER], "public_allowlist": [allowed],
            "infra": [{"id": INFRA_ID, "regex": INFRA_RE}],
        }) + "\n")
        crate = self.repo(("src/lib.rs", "// see %s\n// on %s\n" % (allowed, INFRA_HIT)))
        code, out = self.scan(crate)
        self.assertEqual(code, leak_check.EXIT_HITS, out)
        self.assertIn("src/lib.rs:2: internal infra", out)
        self.assertNotIn("src/lib.rs:1", out)

    def test_no_infra_key_is_incomplete_not_a_bare_pass(self) -> None:
        write(self.patterns, json.dumps({"personal": [MARKER]}) + "\n")
        crate = self.repo(("src/lib.rs", "// nothing internal\n"))
        code, out = self.scan(crate)
        self.assertEqual(code, leak_check.EXIT_OK, out)
        self.assertIn("NO INTERNAL-INFRASTRUCTURE RULES WERE LOADED", out)
        self.assertIn("internal-infrastructure rules not checked",
                      out.strip().splitlines()[-1])

    def test_an_empty_infra_list_is_reported_as_no_rules(self) -> None:
        """Not as a compilation failure, which would send the reader looking
        for a broken regex that does not exist."""
        write(self.patterns, json.dumps({"personal": [MARKER], "infra": []}) + "\n")
        crate = self.repo(("src/lib.rs", "// nothing internal\n"))
        code, out = self.scan(crate)
        self.assertEqual(code, leak_check.EXIT_OK, out)
        self.assertIn("declares no `infra` rules", out)
        self.assertNotIn("NONE of which compiled", out)

    def test_a_rule_that_does_not_compile_is_named_and_the_rest_run(self) -> None:
        write(self.patterns, json.dumps({
            "personal": [MARKER],
            "infra": [{"id": "broken", "regex": "([unclosed"},
                      {"id": INFRA_ID, "regex": INFRA_RE}],
        }) + "\n")
        crate = self.repo(("src/lib.rs", "// on %s\n" % INFRA_HIT))
        code, out = self.scan(crate)
        self.assertEqual(code, leak_check.EXIT_HITS, out)
        self.assertIn("RULE DID NOT COMPILE", out)
        self.assertIn("broken", out)
        self.assertIn("internal infra [%s]" % INFRA_ID, out)
        self.assertIn("AN INFRA RULE DID NOT COMPILE", out)

    def test_a_rule_with_no_regex_is_reported(self) -> None:
        rules, gap, broken = leak_check.load_infra_rules(
            self.patterns_with([{"id": "no-pattern-here", "why": "x"}]))
        self.assertEqual(rules, [])
        self.assertIsNotNone(gap)
        self.assertTrue(any("no-pattern-here" in b for b in broken), broken)

    def test_ignorecase_is_opt_in(self) -> None:
        sensitive = leak_check.load_infra_rules(
            self.patterns_with([{"id": "cs", "regex": "labhost"}]))[0]
        insensitive = leak_check.load_infra_rules(self.patterns_with(
            [{"id": "ci", "regex": "labhost", "ignorecase": True}]))[0]
        self.assertIsNone(sensitive[0][1].search("LabHost"))
        self.assertIsNotNone(insensitive[0][1].search("LabHost"))


class ShipsNowhereMarker(Base):
    """Every test is paired: one that the marker works, one that it cannot be
    used to silence a tree that ships."""

    LEAK = "// see %s/plans/secret.md\n" % NOTES
    GOOD = "ships: nowhere\nreason: workspace machinery, nothing packages it\n"

    def tree(self, *, marker: str | None, cargo: str | None = None,
             remote: bool = False) -> Path:
        d = self.root / "tree"
        write(d / "src/lib.rs", self.LEAK)
        if cargo is not None:
            write(d / "Cargo.toml", cargo)
        if marker is not None:
            write(d / leak_check.OPTOUT_NAME, marker)
        if remote:
            git(d, "init", "-q")
            git(d, "remote", "add", "origin", "https://example.invalid/thing.git")
        return d

    def test_no_marker_means_the_leak_fails(self) -> None:
        code, out = self.scan(self.tree(marker=None))
        self.assertEqual(code, leak_check.EXIT_HITS, out)

    def test_an_honoured_marker_passes_but_prints_every_hit(self) -> None:
        code, out = self.scan(self.tree(marker=self.GOOD))
        self.assertEqual(code, leak_check.EXIT_OK, out)
        self.assertIn("src/lib.rs:1: .agents path", out)
        self.assertIn("[ships-nowhere]", out)
        self.assertIn("NOT counted", out.strip().splitlines()[-1])

    def test_a_marker_cannot_exempt_a_package(self) -> None:
        d = self.tree(marker=self.GOOD,
                      cargo='[package]\nname = "thing"\nversion = "0.1.0"\n')
        code, out = self.scan(d)
        self.assertEqual(code, leak_check.EXIT_HITS, out)
        self.assertIn("REFUSED", out)
        self.assertIn("Cargo.toml declares [package]", out)

    def test_a_workspace_only_manifest_is_not_shipping_evidence(self) -> None:
        d = self.tree(marker=self.GOOD, cargo='[workspace]\nmembers = ["a"]\n')
        code, out = self.scan(d)
        self.assertEqual(code, leak_check.EXIT_OK, out)
        self.assertNotIn("REFUSED", out)

    def test_a_marker_cannot_exempt_a_tree_with_a_remote(self) -> None:
        code, out = self.scan(self.tree(marker=self.GOOD, remote=True))
        self.assertEqual(code, leak_check.EXIT_HITS, out)
        self.assertIn("git remote(s) configured: origin", out)

    def test_a_marker_cannot_exempt_a_tree_with_a_manifest(self) -> None:
        d = self.tree(marker=self.GOOD)
        write(d / "pubspec.yaml", "name: app\n")
        code, out = self.scan(d)
        self.assertEqual(code, leak_check.EXIT_HITS, out)
        self.assertIn("packaging manifest: pubspec.yaml", out)

    def test_a_marker_with_no_reason_is_refused(self) -> None:
        code, out = self.scan(self.tree(marker="ships: nowhere\nreason:\n"))
        self.assertEqual(code, leak_check.EXIT_HITS, out)
        self.assertIn("no non-empty `reason:` line", out)

    def test_an_unrelated_file_of_that_name_does_not_exempt(self) -> None:
        code, out = self.scan(self.tree(marker="# notes to self\nowner: someone\n"))
        self.assertEqual(code, leak_check.EXIT_HITS, out)
        self.assertIn("no `ships: nowhere` line", out)

    def test_the_marker_file_is_not_itself_scanned(self) -> None:
        d = self.root / "tree"
        write(d / "src/lib.rs", "// clean\n")
        write(d / "Cargo.toml", '[package]\nname = "t"\n')
        write(d / leak_check.OPTOUT_NAME,
              "ships: nowhere\nreason: it reads %s/env at runtime\n" % NOTES)
        code, out = self.scan(d)
        self.assertIn("REFUSED", out)
        self.assertEqual(code, leak_check.EXIT_OK, out)
        self.assertFalse([line for line in out.splitlines()
                          if line.startswith(leak_check.OPTOUT_NAME + ":")], out)

    def test_an_honoured_marker_covers_infra_hits(self) -> None:
        d = self.root / "machinery"
        write(d / "run.py", "HOST = '%s'\n" % INFRA_HIT)
        write(d / leak_check.OPTOUT_NAME, self.GOOD)
        code, out = self.scan(d)
        self.assertEqual(code, leak_check.EXIT_OK, out)
        self.assertIn("[ships-nowhere] run.py:1: internal infra", out)


class CommitsOnly(Base):
    def test_it_skips_the_tree_and_still_catches_a_trailer(self) -> None:
        crate = self.repo(("src/lib.rs", "// see %s/plans/x.md\n" % NOTES))
        git(crate, "add", ".")
        git(crate, "commit", "-qm", "feat: thing\n\n%s abc123" % TRAILER)
        code, out = run(str(crate), "--commits-only")
        self.assertEqual(code, leak_check.EXIT_HITS, out)
        self.assertIn("session trailer '%s'" % TRAILER, out)
        self.assertNotIn("src/lib.rs", out)

    def test_on_a_non_repo_it_is_exit_3(self) -> None:
        app = self.root / "app"
        write(app / "main.py", "print(1)\n")
        code, out = run(str(app), "--commits-only")
        self.assertEqual(code, leak_check.EXIT_CANNOT_CHECK, out)


class TheCommand(unittest.TestCase):
    def test_dotagents_runs_it_by_name(self) -> None:
        """End to end through the real command line, with an empty agent store
        so nothing from the machine is discovered."""
        pytest.importorskip("dotagents")
        with tempfile.TemporaryDirectory(prefix="leakchk-cmd-") as tmp:
            root = Path(tmp).resolve()
            app = root / "app"
            write(app / "main.py", "print(1)\n")
            env = dict(GIT_ENV, AGENTS_HOME=str(root / "store"),
                       HOME=str(root), USERPROFILE=str(root))
            env.pop("AGENTS_PROJECT_ROOT", None)
            out = subprocess.run(
                [sys.executable, "-m", "dotagents", "--cmdspath", str(TOOL.parent),
                 "leak-check", str(app), "--commits-only"],
                capture_output=True, text=True, env=env, cwd=str(root))
            self.assertEqual(out.returncode, leak_check.EXIT_CANNOT_CHECK,
                             out.stdout + out.stderr)
            self.assertIn("CANNOT CHECK", out.stdout)
