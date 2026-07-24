"""`dotagents env` -- chained env-file assembly + env.py execution (plan 07).

Self-contained block: all logic lives in `_env.py` (frozen contract B) and
`_agents.stamp_identity` (plan 08). The only umbrella touch is registering
`Env` on `Dotagents._subcommands_` (in `cli/__init__.py`). Never logs
DOTAGENTS_*/AGENTS_* VALUES -- output goes to stdout for the caller to consume;
the logger only ever names vars (Leakage rule).
"""

from pathlib import Path

from duho import Cmd, LoggingArgs


def _format_env(env: "dict[str, str]", output_format: str) -> str:
    """Render the assembled env in the requested format (export/json/ini/yaml).

    * ``export`` -- ``export KEY="value"`` lines (shell-eval'able; the default,
      what a SessionStart hook sources).
    * ``json``   -- a sorted JSON object.
    * ``ini``    -- ``KEY=value`` lines under a ``[env]`` section.
    * ``yaml``   -- ``KEY: value`` lines (values quoted when ambiguous).

    Values are emitted verbatim (this is the point of the command); callers must
    treat the output as sensitive.
    """
    import json as _json

    keys = sorted(env)
    if output_format == "json":
        return _json.dumps({k: env[k] for k in keys}, indent=2, sort_keys=True)
    if output_format == "ini":
        return "\n".join(["[env]"] + ["%s=%s" % (k, env[k]) for k in keys])
    if output_format == "yaml":
        lines = []
        for k in keys:
            v = env[k]
            if v == "" or any(c in v for c in ":#'\"\n") or v.strip() != v:
                v = _json.dumps(v)
            lines.append("%s: %s" % (k, v))
        return "\n".join(lines)
    # default: export
    return "\n".join("export %s=%s" % (k, _json.dumps(env[k])) for k in keys)


class Env(LoggingArgs, Cmd):
    """Assemble the chained env (env files + env.py execution) under contract B.

    Prepends overlay/level ``bin`` dirs to ``PATH`` first, then evaluates the
    ``pre.*`` tier and the main tier in precedence order (overlays -> user ->
    project -> project-root), chaining each file over the accumulated env so
    later files win. ``.py`` files are EXECUTED and emit JSON env changes; plain
    files are sourced. Standardized ``AGENTS_*``/``AGENT`` identity vars and the
    ``AGENTS_PROXY`` model are wired in.

    ``--diff`` emits only the vars that differ from the current environment (what
    a SessionStart hook injects); the default emits the full assembled env merged
    over the current one. Output is sensitive -- it may carry secret values."""

    _parsername_ = "env"

    format: str = "export"
    "Output format: export, json, ini, or yaml."
    ("--format",)

    diff: bool = False
    "Emit only vars that differ from the current environment."
    ("--diff",)

    global_scope: bool = False
    "Use global scope (skip project-level env files)."
    ("--global", "-g")

    def __call__(self) -> int:
        import os

        from dotagents import _env

        project_root = Path.cwd()
        agents_dir = Path.home() / ".agents"
        base = dict(os.environ)

        if self.format not in ("export", "json", "ini", "yaml"):
            raise SystemExit(
                "error: --format must be one of export, json, ini, yaml (got %r)"
                % self.format
            )

        if self.diff:
            env = _env.get_diff(
                agents_dir=agents_dir, project_root=project_root,
                base_env=base, global_scope=self.global_scope, logger=self._logger_,
            )
        else:
            changes = _env.get_environment(
                agents_dir=agents_dir, project_root=project_root,
                base_env=base, global_scope=self.global_scope, logger=self._logger_,
            )
            env = dict(base)
            env.update(changes)

        print(_format_env(env, self.format))
        return 0
