# dotagents example overlays

Example **base overlays** for [dotagents](https://github.com/jose-pr/dotagents) — the
"dotfiles for AI coding agents" tool. dotagents is the mechanism (install a neutral
base, then layer in overlays); the overlays here are the **payloads** that carry the
opinions. They live on their own branch, kept separate from the tool so the platform and
its example content never mix.

These are a starting point, not a requirement — swap any of them for your own.

## Using them

Point `dotagents overlays add` at this directory as the source:

```sh
# from a checkout of this branch
dotagents overlays add flows python --source overlays

# or set it once
export AGENTS_OVERLAYS_SRC=/path/to/overlays
dotagents overlays add flows
```

Each overlay installs into your scope's `overlays/<name>/` (what its manifest
`requires` first), merges its always-on rules/routing into `AGENTS.md`, publishes any
skills to the shared skills dir, and runs its `setup.py` if it ships one (none of
these do). `dotagents env` then puts every installed overlay's `bin/` on `PATH`, its
`lib/` on `PYTHONPATH`, and exports one `$<NAME>_OVERLAY_ROOT` per overlay -- the
variable every routing line and cross-reference here uses instead of a hard path, so
the store can live anywhere (`$AGENTS_HOME`) and nothing breaks when it moves. See the dotagents docs for the full overlay model:
<https://jose-pr.github.io/dotagents/guide/overlays/>.

## The overlays

| Overlay | What it carries |
| --- | --- |
| `flows` | A planning/execution/review workflow set — `PLAN.md` (a strong model writes precise, autonomous plans), `EXEC.md` (a cheaper model executes them without re-deriving context), `REVIEW.md` (file-threaded multi-agent plan review), `REPO.md` (the repo standard), `MODELS.md` (executor/model selection). One opinionated way to drive agents — the architect/executor split. |
| `engineering` | Always-on engineering discipline: commit shape, release-tag consent, benchmark-as-evidence, token discipline, draft follow-ups. |
| `python`, `node`, `rust` | Per-language `kb/` conventions + manifest templates + CI workflow templates (test / release / docs). |
| `references` | Language-neutral repo-file templates (README, CHANGELOG, LICENSE, `.gitignore`, docs-index, a plan-shape example). |
| `release` | A host-agnostic release helper: an agent-driven commit-plan loop plus tag/CI monitoring across GitHub (`gh`) and GitLab. |
| `private-sync` | The one-private-repo, per-project `.agents` model (`kb/PRIVATE_SYNC.md` + cloud hooks). |
| `net` | Dependency-free HTTP tooling (a drop-in `curl` shim, an OS-trust-store `certifi` shim, an `httplib` session toolkit reading `AGENTS_PROXY`). |
| `recovery` | A config-recovery playbook for reconstructing a lost `~/.agents`. |
| `tools` | Opt-in helper scripts (`summarize_run`, `compare_bench`). |

## Overlay layout

An overlay is a directory with an `overlay.toml` manifest and files that install to the
same relative path in your scope. Minimal shape:

```
<name>/
  overlay.toml     # name, description, requires, routing lines, optional priority
  kb/…             # knowledge-base files the routing points at ($<NAME>_OVERLAY_ROOT/kb/…)
  bin/, lib/       # optional; put on PATH / PYTHONPATH by `dotagents env`
  cmds/            # optional duho command modules, discovered as `dotagents <name>`
  env.py           # optional; run by `dotagents env`, prints a JSON object of env changes
  CONTEXT.md       # optional; emitted by `dotagents context` (<NAME_OVERLAY_ROOT> expands)
  skills/<skill>/  # optional skills, published to the shared skills dir
  setup.py         # optional idempotent install-time script -- only for real install work
```

Reference another overlay's file as `$<NAME>_OVERLAY_ROOT/<path>` (e.g.
`$FLOWS_OVERLAY_ROOT/flows/REPO.md`), never `~/.agents/<path>`: overlays install under
`overlays/<name>/`, and the variable is what `dotagents env` exports for that dir.

## License

MIT — see [LICENSE](LICENSE).
