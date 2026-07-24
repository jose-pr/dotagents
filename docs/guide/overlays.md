# Overlays

The configuration is a **base overlay** plus opt-in **overlays**. The base is what
`init` / `install` lay down — neutral scaffolding with no opinions. Everything
opinionated ships as a named overlay you add explicitly.

## The overlay model

An overlay is a directory (optionally carrying an `overlay.toml` manifest) whose files
install to the same relative path in the destination scope. `dotagents overlays`
resolves each overlay **by name** against a source (the bundled overlay set by default;
override with `--source <dir>` or `$DOTAGENTS_OVERLAYS_SRC`), and installs it into the
scope's overlays directory. There is **no registry file**: installed overlays are
*discovered* by their presence there.

Overlays are **additive**. `add` / `sync` never clobber a file you hand-edited inside
an installed overlay — an already-present file is skipped.

## Managing overlays

```bash
python install.py overlays add python flows    # install into the scope, publish skills, merge rules/routing
python install.py overlays list                # installed (discovered) + available (from source)
python install.py overlays sync 'py*'          # refresh installed overlays matching a glob
python install.py overlays remove python       # delete the overlay dir + unpublish its skills
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

An overlay may ship an **idempotent** `setup` (extensionless POSIX script) or
`setup.py` at its root. After `add` / `sync` copies the overlay in, dotagents runs that
script automatically — so anything a human would otherwise hand-follow (PATH/lib
wiring, self-registration) is one script the tool runs, not a doc. Presence of the
script is the opt-in; skip it with `--no-setup`. The author contract:

- **Idempotent** — safe on every `add` / `sync`; check-then-act, never blindly append.
- **cwd** is the installed overlay dir, so reference your own files by relative path.
- **Environment** carries the resolved store path and the overlay's own installed dir
  (never hardcode a home path).
- A **non-zero exit fails the install** with a clear error, not a silent skip. Any
  outward or irreversible action must be confirmed by the *script* itself.

See [Authoring an overlay](authoring.md) to build your own.
