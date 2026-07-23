import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from hosts import ReleaseHost

try:
    import yaml
except Exception:
    yaml = None
class ReleaseManager:
    def __init__(self, repo_path: str = "."):
        self.repo_path = Path(repo_path).resolve()
        self.host = ReleaseHost.from_remote(self._origin_url())
    def run_git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["git", "-C", str(self.repo_path)] + list(args)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"Git command failed: {' '.join(cmd)}\n{result.stderr}")
        return result
    def get_current_version(self) -> Optional[str]:
        result = self.run_git("describe", "--tags", "--abbrev=0", check=False)
        if result.returncode == 0:
            version = result.stdout.strip()
            return version[1:] if version.startswith('v') else version
        return None
    def parse_version(self, version: str) -> Tuple[int, int, int]:
        match = re.match(r'^(\d+)\.(\d+)(?:\.(\d+))?$', version)
        if not match:
            raise ValueError(f"Invalid semantic version: {version}")
        major = int(match.group(1))
        minor = int(match.group(2))
        patch = int(match.group(3) or 0)
        return major, minor, patch
    def increment_version(self, current: str, increment: str) -> str:
        major, minor, patch = self.parse_version(current)
        if increment == "major":
            return f"{major + 1}.0.0"
        elif increment == "minor":
            return f"{major}.{minor + 1}.0"
        elif increment == "patch":
            return f"{major}.{minor}.{patch + 1}"
        else:
            raise ValueError(f"Invalid increment type: {increment}")
    def get_uncommitted_changes(self) -> List[str]:
        result = self.run_git("status", "--porcelain")
        if not result.stdout:
            return []
        files = []
        for line in result.stdout.strip().split('\n'):
            if line:
                status = line[:2]
                filename = line[3:]
                if status.strip():
                    files.append(filename)
        return files
    def get_status_porcelain(self) -> str:
        return self.run_git("status", "--porcelain").stdout
    def get_diff_name_status(self) -> str:
        return self.run_git("diff", "--name-status").stdout
    def get_diff_stat(self) -> str:
        return self.run_git("diff", "--stat").stdout
    def get_diff_numstat(self) -> str:
        return self.run_git("diff", "--numstat").stdout
    def get_changed_file_summaries(self, patch_line_limit: int = 80) -> List[Dict[str, Any]]:
        status_map: Dict[str, str] = {}
        for line in self.get_status_porcelain().splitlines():
            if not line:
                continue
            status = line[:2]
            path = line[3:]
            if path:
                status_map[path] = status
        numstat_map: Dict[str, Dict[str, Union[int, str]]] = {}
        for line in self.get_diff_numstat().splitlines():
            if not line:
                continue
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            added_raw, deleted_raw, path = parts
            numstat_map[path] = {
                "additions": int(added_raw) if added_raw.isdigit() else added_raw,
                "deletions": int(deleted_raw) if deleted_raw.isdigit() else deleted_raw,
            }
        changed_files = sorted(set(status_map) | set(numstat_map))
        summaries: List[Dict[str, Any]] = []
        for path in changed_files:
            patch = self.run_git("diff", "--", path).stdout
            patch_lines = patch.splitlines()
            summary: Dict[str, Any] = {
                "path": path,
                "status": status_map.get(path, "??"),
                "patch_excerpt": "\n".join(patch_lines[:patch_line_limit]),
                "patch_truncated": len(patch_lines) > patch_line_limit,
            }
            if path in numstat_map:
                summary.update(numstat_map[path])
            summaries.append(summary)
        return summaries
    def find_all_release_md(self) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []
        repo_release = self.repo_path / "RELEASE.md"
        if repo_release.exists() and repo_release.is_file():
            findings.append({"path": str(repo_release.relative_to(self.repo_path)), "importance": "repo"})
        agents_release = self.repo_path / ".agents" / "RELEASE.md"
        if agents_release.exists() and agents_release.is_file():
            findings.append({"path": str(agents_release.relative_to(self.repo_path)), "importance": "agents"})
        overlay_root = Path(__file__).resolve().parents[3]
        overlay_release = overlay_root / "kb" / "RELEASE.md"
        if overlay_release.exists() and overlay_release.is_file():
            findings.append({"path": str(overlay_release), "importance": "overlay"})
        return findings
    def read_repo_release_instructions(self) -> Optional[Dict[str, Any]]:
        all_files = self.find_all_release_md()
        priority = {"repo": 3, "agents": 2, "overlay": 1}
        chosen = None
        max_prio = 0
        for entry in all_files:
            imp = entry["importance"]
            if priority.get(imp, 0) > max_prio:
                max_prio = priority[imp]
                chosen = entry
        if not chosen:
            return None
        try:
            content = Path(chosen["path"]).read_text() if Path(chosen["path"]).is_absolute() else (self.repo_path / chosen["path"]).read_text()
            return {"path": chosen["path"], "content": content}
        except Exception:
            return {"path": chosen["path"], "content": None}
    def build_plan_context(self) -> Dict[str, Any]:
        return {
            "repo_path": str(self.repo_path),
            "status_porcelain": self.get_status_porcelain(),
            "diff_name_status": self.get_diff_name_status(),
            "diff_stat": self.get_diff_stat(),
            "changed_file_summaries": self.get_changed_file_summaries(),
            "release_instructions": self.read_repo_release_instructions(),
            "release_md_files": self.find_all_release_md(),
            "agent_guidance": [
                "Use the highest-priority RELEASE.md selected in release_instructions as authoritative guidance.",
                "Inspect changed_file_summaries to review actual content changes before deciding commit boundaries.",
                "Prefer multiple logical commits over one omnibus commit when code, docs, tests, or tooling changes can be separated cleanly.",
            ],
        }
    def emit_plan_context(self) -> None:
        ctx = self.build_plan_context()
        if yaml is not None:
            print(yaml.safe_dump(ctx, sort_keys=False))
        else:
            print(json.dumps(ctx, indent=2))
    def _read_plan_text(self, path_or_dash: str) -> str:
        if path_or_dash == "-":
            return sys.stdin.read()
        return (self.repo_path / path_or_dash).read_text() if not os.path.isabs(path_or_dash) else Path(path_or_dash).read_text()
    def load_commit_plan(self, path_or_dash: str) -> Dict[str, Any]:
        raw = self._read_plan_text(path_or_dash)
        if yaml is not None:
            try:
                data = yaml.safe_load(raw)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(
                "Commit plan must be valid YAML (preferred) or JSON. "
                "PyYAML is not available or YAML parsing failed, and JSON parsing failed."
            ) from e
        if not isinstance(data, dict):
            raise ValueError("Commit plan must be a mapping/object at the top level")
        return data
    def validate_commit_plan(self, plan: Dict[str, Any]) -> None:
        if plan.get("version") != 1:
            raise ValueError("Commit plan version must be 1")
        commits = plan.get("commits")
        if not isinstance(commits, list) or not commits:
            raise ValueError("Commit plan must include non-empty 'commits' list")
        for i, c in enumerate(commits, start=1):
            if not isinstance(c, dict):
                raise ValueError(f"Commit {i}: each commit must be a mapping/object")
            msg = c.get("message")
            if not isinstance(msg, str) or not msg.strip():
                raise ValueError(f"Commit {i}: missing non-empty 'message'")
            add = c.get("add")
            patches = c.get("patches")
            if add is None and patches is None:
                raise ValueError(f"Commit {i}: must include 'add' and/or 'patches'")
            if add is not None:
                if not isinstance(add, list) or not add:
                    raise ValueError(f"Commit {i}: 'add' must be a non-empty list")
                for j, entry in enumerate(add, start=1):
                    if not isinstance(entry, dict) or "path" not in entry:
                        raise ValueError(f"Commit {i} add {j}: each entry must be a mapping with a 'path'")
                    p = entry.get("path")
                    if not isinstance(p, str) or not p.strip():
                        raise ValueError(f"Commit {i} add {j}: 'path' must be a non-empty string")
            if patches is not None:
                if not isinstance(patches, list) or not patches:
                    raise ValueError(f"Commit {i}: 'patches' must be a non-empty list")
                for j, entry in enumerate(patches, start=1):
                    if not isinstance(entry, dict):
                        raise ValueError(f"Commit {i} patch {j}: each entry must be a mapping")
                    if not isinstance(entry.get("path"), str) or not entry.get("path"):
                        raise ValueError(f"Commit {i} patch {j}: 'path' must be a non-empty string")
                    if not isinstance(entry.get("apply"), str) or not entry.get("apply"):
                        raise ValueError(f"Commit {i} patch {j}: 'apply' must be a non-empty patch string")
    def ensure_clean_index(self) -> None:
        staged = self.run_git("diff", "--cached", "--name-only").stdout.strip()
        if staged:
            raise RuntimeError(
                "Index must be clean to apply a commit plan. "
                "Unstage or commit current staged changes first, then re-run with --commit-plan."
            )
    def apply_commit_plan(self, plan: Dict[str, Any], yes: bool = False) -> None:
        self.ensure_clean_index()
        self.validate_commit_plan(plan)
        commits = plan["commits"]
        print(f"✓ Commit plan: {len(commits)} commit(s)")
        for idx, c in enumerate(commits, start=1):
            print(f"  {idx}. {c['message']}")
        if not yes:
            confirm = input("\nApply this commit plan? [Y/n] ").strip().lower()
            if confirm not in ("", "y", "yes"):
                raise RuntimeError("Commit plan application cancelled")
        for idx, c in enumerate(commits, start=1):
            msg = c["message"].strip()
            add = c.get("add") or []
            patches = c.get("patches") or []
            self.run_git("reset")
            if add:
                paths = [e["path"] for e in add]
                self.run_git("add", "--", *paths)
            for p in patches:
                patch_text = p["apply"]
                proc = subprocess.run(
                    ["git", "-C", str(self.repo_path), "apply", "--cached", "--"],
                    input=patch_text,
                    text=True,
                    capture_output=True,
                )
                if proc.returncode != 0:
                    raise RuntimeError(
                        f"git apply --cached failed for commit {idx} ({msg}):\n{proc.stderr.strip()}"
                    )
            staged = self.run_git("diff", "--cached", "--name-only").stdout.strip()
            if not staged:
                raise RuntimeError(f"Commit {idx} ({msg}) staged nothing; check its 'add'/'patches'")
            self.run_git("commit", "-m", msg)
        self.ensure_clean_index()
    def validate_repository(self) -> List[str]:
        issues = []
        readme = self.repo_path / "README.md"
        if not readme.exists():
            issues.append("Missing README.md file")
        else:
            content = readme.read_text()
            if len(content.strip()) < 100:
                issues.append("README.md appears incomplete (less than 100 characters)")
        changelog = self.repo_path / "CHANGELOG.md"
        if not changelog.exists():
            issues.append("Missing CHANGELOG.md file")
        else:
            content = changelog.read_text()
            if "[Unreleased]" not in content:
                issues.append("CHANGELOG.md missing [Unreleased] section")
            if "keep a changelog" not in content.lower() and "keepachangelog" not in content.lower():
                issues.append("CHANGELOG.md should reference Keep a Changelog format")
        groups_md = self.repo_path / "AGENTS.md"
        if groups_md.exists():
            content = groups_md.read_text()
            if "overlay:" in content.lower():
                kb_index = self.repo_path / "kb" / "INDEX.md"
                if (self.repo_path / "kb").exists():
                    if not kb_index.exists():
                        issues.append("kb/ directory exists but missing INDEX.md")
                    else:
                        kb_files = list((self.repo_path / "kb").glob("*.md"))
                        index_content = kb_index.read_text()
                        for kb_file in kb_files:
                            if kb_file.name != "INDEX.md" and kb_file.name not in index_content:
                                issues.append(f"kb/{kb_file.name} not referenced in INDEX.md")
                skills_dir = self.repo_path / "skills"
                if skills_dir.exists():
                    for skill_dir in skills_dir.iterdir():
                        if skill_dir.is_dir():
                            skill_md = skill_dir / "SKILL.md"
                            if not skill_md.exists():
                                issues.append(f"Skill {skill_dir.name} missing SKILL.md")
        return issues
    def create_release_tag(self, version: str, message: Optional[str] = None) -> str:
        tag_message = message or f"Release {version}"
        self.run_git("tag", "-a", f"v{version}", "-m", tag_message)
        return f"v{version}"
    def _origin_url(self) -> Optional[str]:
        result = self.run_git("remote", "get-url", "origin", check=False)
        if result.returncode != 0:
            return None
        url = result.stdout.strip()
        return url or None
    def push_release(self, tag: str) -> None:
        # Delegate to the detected host (default: git push + push tag).
        if self.host is not None:
            self.host.push(self.run_git, tag)
        else:
            self.run_git("push")
            self.run_git("push", "origin", tag)
    def monitor_ci(self, ref: str, timeout: int = 600) -> Dict[str, Any]:
        if self.host is None:
            return {
                "status": "unknown",
                "message": (
                    "CI monitoring unavailable — unrecognized VCS host for origin "
                    "%r (supported: GitHub, GitLab)" % (self._origin_url() or "")
                ),
            }
        return self.host.monitor_ci(ref, timeout=timeout)
def main():
    parser = argparse.ArgumentParser(
        description="Release automation for overlays and repositories"
    )
    parser.add_argument(
        "version",
        nargs="?",
        help="Version to release (x.y.z) or increment (major/minor/patch)"
    )
    parser.add_argument(
        "--no-commit",
        action="store_true",
        help="Skip creating commits for uncommitted changes"
    )
    parser.add_argument(
        "--emit-plan-context",
        action="store_true",
        help="Print commit-plan context (YAML if available, else JSON) and exit"
    )
    parser.add_argument(
        "--commit-plan",
        help="Apply commit plan from YAML/JSON file path, or '-' for stdin"
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Apply commit plan without prompting"
    )
    parser.add_argument(
        "--skip-ci",
        "--skip-pipeline",
        dest="skip_ci",
        action="store_true",
        help="Skip CI verification after push (--skip-pipeline is an alias)"
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Actually push the tag. Without it, a v* release stops before "
             "push and hands back for explicit user consent (ci-* tags push)."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force release even with validation errors"
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="Repository path (default: current directory)"
    )
    args = parser.parse_args()
    try:
        manager = ReleaseManager(args.repo)
        if args.emit_plan_context:
            manager.emit_plan_context()
            return 0
        if args.commit_plan:
            plan = manager.load_commit_plan(args.commit_plan)
            manager.apply_commit_plan(plan, yes=args.yes)
            print("✓ Commit plan applied")
            return 0
        current_version = manager.get_current_version()
        if not current_version:
            print("⚠️  No version tags found. Starting from 0.0.0")
            current_version = "0.0.0"
        else:
            print(f"✓ Current version: {current_version}")
        if not args.version:
            print("\nNo version specified. Choose:")
            print(f"  major -> {manager.increment_version(current_version, 'major')}")
            print(f"  minor -> {manager.increment_version(current_version, 'minor')}")
            print(f"  patch -> {manager.increment_version(current_version, 'patch')}")
            print(f"  x.y.z -> Custom version")
            version_input = input("Enter version or increment: ").strip()
        else:
            version_input = args.version
        if version_input in ("major", "minor", "patch"):
            new_version = manager.increment_version(current_version, version_input)
        else:
            new_version = version_input
            manager.parse_version(new_version)
        print(f"✓ Creating release: {new_version}")
        uncommitted = manager.get_uncommitted_changes()
        if uncommitted and not args.no_commit:
            print(f"\n✓ Detected uncommitted changes in {len(uncommitted)} files")
            print("\nThis script no longer creates a commit plan automatically.")
            print("Generate plan context for your agent:")
            print("  release.py --emit-plan-context")
            print("Then have the agent produce an approved YAML/JSON plan, and apply it:")
            print("  release.py --commit-plan <plan.yml>")
            print("  release.py --commit-plan <plan.yml> -y   # skip the confirm prompt")
            print("\nFiles with changes:")
            for f in uncommitted:
                print(f"  • {f}")
            return 2
        validation_issues = manager.validate_repository()
        if validation_issues:
            print("\n⚠️  Validation issues found:")
            for issue in validation_issues:
                print(f"  • {issue}")
            if not args.force:
                print("\n❌ Release cancelled due to validation errors")
                print("   Use --force to override")
                return 1
        else:
            print("✓ Documentation validation passed")
        tag = manager.create_release_tag(new_version)
        print(f"✓ Created tag: {tag}")
        # v* tags are irreversible once pushed: require explicit per-release
        # consent. Do NOT auto-push — hand back to the user unless --push was
        # passed for THIS release. (ci-* tags would be safe to auto-push, but
        # this tool only mints v* release tags.)
        if tag.startswith("v") and not args.push:
            print("\n⛔ STOP — v* release tag created locally but NOT pushed.")
            print("   Pushing a v* tag is irreversible and needs your explicit")
            print("   consent for THIS release. Review, then push yourself:")
            print(f"     git push && git push origin {tag}")
            print("   or re-run with --push to push now.")
            return 3
        print("✓ Pushing commits and tag...")
        manager.push_release(tag)
        print("✓ Release pushed successfully")
        if not args.skip_ci:
            print("\n✓ Waiting for CI...")
            ci_result = manager.monitor_ci(tag)
            print(f"  {ci_result.get('message', '')}")
        print(f"\n✓ Release {new_version} complete")
        return 0
    except Exception as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        return 1
if __name__ == "__main__":
    sys.exit(main())