# Overlays

The configuration is a **base overlay** plus opt-in **overlays**. The base is what
`init` / `install` lay down — neutral scaffolding with no opinions. Everything
opinionated ships as a named overlay you add explicitly.

## The overlay model

An overlay is a directory (optionally carrying an `overlay.toml` manifest) whose files
install to the same relative path in the destination scope. `dotagents overlays`
resolves each overlay **by name** against a source directory you point it at with
`--source <dir>` (or `$DOTAGENTS_OVERLAYS_SRC`), and installs it into the scope's
overlays directory. There is **no registry file**: installed overlays are *discovered*
by their presence there.

Overlays are **additive**. `add` / `sync` never clobber a file you hand-edited inside
an installed overlay — an already-present file is skipped.

## Example overlays

The example overlays below live on this repo's separate
[`overlays` branch](https://github.com/jose-pr/dotagents/tree/overlays), not in `main` —
they are **payloads riding on dotagents**, not part of the tool, so you swap them for
your own. Point `--source` at a checkout of that branch to install any with
`dotagents overlays add <name> --source <overlays-checkout>`.

| Overlay | What it carries |
| --- | --- |
| `flows` | A planning/execution/review workflow set. `PLAN.md` has a strong model write precise, autonomous plans; `EXEC.md` has a cheaper model execute them without re-deriving context; `REVIEW.md` runs file-threaded multi-agent plan review; `REPO.md` is the repo standard; `MODELS.md` guides executor/model selection. This is one opinionated way to drive agents — the *architect/executor split* — not something dotagents imposes. |
| `engineering` | Always-on engineering discipline: commit shape, release-tag consent, benchmark-as-evidence, token discipline, draft follow-ups. |
| `python`, `node`, `rust` | Per-language `kb/` conventions + manifest templates + CI workflow templates. |
| `references` | Language-neutral repo-file templates (README, CHANGELOG, LICENSE, `.gitignore`, docs-index, a plan-shape example). |
| `release` | A host-agnostic release helper: an agent-driven commit-plan loop plus tag/CI monitoring across GitHub (`gh`) and GitLab. |
| `private-sync` | The one-private-repo, per-project `.agents` model (`kb/PRIVATE_SYNC.md` + cloud hooks) — see [Private sync](private-sync.md). |
| `net` | Dependency-free HTTP tooling (a drop-in `curl` shim, an OS-trust-store `certifi` shim, an `httplib` session toolkit reading `AGENTS_PROXY`). |
| `recovery` | A config-recovery playbook for reconstructing a lost `~/.agents`. |
| `tools` | Opt-in helper scripts (`summarize_run`, `compare_bench`). |

## Managing overlays

```bash
dotagents overlays add python flows    # install into the scope, publish skills, merge rules/routing
dotagents overlays list                # installed (discovered) + available (from source)
dotagents overlays sync 'py*'          # refresh installed overlays matching a glob
dotagents overlays remove python       # delete the overlay dir + unpublish its skills
```

Scope is **project** by default, or **user** with `-g` / `--global` (the configurable
store). Each overlay:

- installs as a directory (kept, discoverable);
- has its `routing` / `rules` merged **additively** into `AGENTS.md`'s managed block
  (D59);
- has its skills published into the scope's shared skills dir, so every agent that
  reads that dir sees the same skills.

Removing an overlay deletes only its directory and unpublishes only the skills **it**
published. Its lines in `AGENTS.md`'s managed block are **not** auto-pruned — a warning
points at the manual edit (or re-run `install`).

## `overlay.toml`

The manifest is read by the `overlays` command and the context assembler. Keys:

| Key | Meaning |
| --- | --- |
| `name` | The overlay's canonical name. |
| `description` | One-line summary shown by `overlays list`. |
| `requires` | Other overlays this one depends on. |
| `routing` | Lines appended to the core's "Load on demand" routing table. |
| `rules` | Overlay-relative markdown paths whose rule bullets append to "Always-on rules". |
| `priority` | Merge order (lower sorts earlier; unprioritized default is 500). |

## Skills

An overlay may ship `skills/<skill-name>/` directories. Publishing symlinks each (or
copies, where symlinks are unavailable) into the scope's shared skills dir, so every
agent that reads that dir picks up the same skills. Removing the overlay unpublishes
only the skills it published, then sweeps any now-broken symlinks.

## Setup scripts

An overlay may ship an **idempotent** `setup.py` at its root — the recommended,
OS-agnostic form: it runs under the same Python that runs dotagents, so it works on
every platform (the bundled `net` overlay uses exactly this). An extensionless `setup`
(a POSIX shell script) is still honored as a **legacy fallback**, but discouraged — a
shell script isn't portable to Windows without a shell. When both are present, `setup.py`
wins. After `add` / `sync` copies the overlay in, dotagents runs the script
automatically — so anything a human would otherwise hand-follow (PATH/lib wiring,
self-registration) is one script the tool runs, not a doc. Presence of a script is the
opt-in; skip it with `--no-setup`. The author contract:

- **Idempotent** — safe on every `add` / `sync`; check-then-act, never blindly append.
- **cwd** is the installed overlay dir, so reference your own files by relative path.
- **Environment** carries the resolved store path and the overlay's own installed dir
  (never hardcode a home path).
- A **non-zero exit fails the install** with a clear error, not a silent skip. Any
  outward or irreversible action must be confirmed by the *script* itself.

See [Authoring an overlay](authoring.md) to build your own.
