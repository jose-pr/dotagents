# Overlays

The configuration is a **base overlay** plus opt-in **overlays**. The base is what
`init` lays down — the `AGENTS.md` managed block, with no opinions. Everything
opinionated ships as a named overlay you add explicitly.

## The overlay model

An overlay is an [AOS](https://boriken-dev.github.io/agent-overlay-spec/) overlay:
a directory, optionally carrying an `OVERLAY.md` or `overlay.cfg` manifest, that installs as a unit
into `<store>/overlays/<name>/` — `~/.agents/overlays/<name>/` for
the user store, `<project>/.agents/overlays/<name>/` for a project. Its files keep
their relative paths inside that directory, and content refers to them through the
overlay's root variable (`$<NAME>_OVERLAY_ROOT/kb/X.md`, see
[below](#what-dotagents-env-wires-for-every-overlay)). `dotagents overlays` resolves
each overlay **by name** against the repos you name (`--repo`, `$AGENTS_OVERLAYS_REPO`,
a `dotagents.{json,toml,yaml}` in a store — see "Where overlays come from" below), and
installs it into the scope's overlays directory. Installed overlays are then
*discovered* by their presence there.

`overlays remove` deletes `<store>/overlays/<name>/` outright, so never author an
overlay inside that directory: keep your source elsewhere and install it from there.
`add` and `sync` refuse a source that lies inside `<store>/overlays/`.

Overlays are **additive**. `add` / `sync` never clobber a file you hand-edited inside
an installed overlay — a file that differs from the source is kept and reported.

## Overlay names

A name starts with an ASCII letter, continues with letters, digits, `_`, `.` or `-`,
and ends in a letter or digit. It is case-insensitive, and `_` and `.` count as `-`:
`My_Overlay`, `my.overlay` and `my-overlay` are one overlay, installed as
`overlays/my-overlay/`. The scope level names (`user`, `project`, `system`,
`project-root`, `default`, `overlay`) and Windows device names (`con`, `nul`, `com1`,
… with or without an extension) are refused.

## Example overlays

The example overlays below live on this repo's separate
[`repo` branch](https://github.com/jose-pr/dotagents/tree/repo), not in `main` —
they are **payloads riding on dotagents**, not part of the tool, so you swap them for
your own. Name the branch as a repo to install any of them:
`dotagents overlays add <name> --repo "https://github.com/jose-pr/dotagents.git@repo#overlays"`,
or `--repo <checkout>/overlays` for a local checkout of the branch.

| Overlay | What it carries |
| --- | --- |
| `engineering` | One opinionated engineering process. `rules/ENGINEERING.md` — always-on discipline merged into `AGENTS.md` (commit shape, release-tag consent, benchmark-as-evidence, token discipline, draft follow-ups). `flows/` — `PLAN.md` (a strong model writes precise, autonomous plans), `EXEC.md` (a cheaper model executes them without re-deriving context), `REVIEW.md` (file-threaded multi-agent plan review), `REPO.md` (the repo standard); `kb/MODELS.md` (executor/model selection). `references/` — language-neutral repo-file templates (README, CHANGELOG, LICENSE, `.gitignore`, docs-index, the plan template). `cmds/` — `plans`, `leak-check`, `eol`, `summarize-run` (command → verdict) and `compare-bench` (diff benchmark JSONs), each a `dotagents` command. The language overlays require it. |
| `python`, `node`, `rust` | Per-language `kb/` conventions + manifest templates + CI workflow templates. |
| `release` | A host-agnostic release helper: an agent-driven commit-plan loop plus tag/CI monitoring across GitHub (`gh`) and GitLab. |
| `private-sync` | The one-private-repo, per-project `.agents` model (`kb/PRIVATE_SYNC.md` + cloud hooks) — see [Private sync](private-sync.md). |
| `net` | HTTP tooling: a drop-in `curl` launcher (curlike), an OS-trust-store `certifi` shim, and syshttp (the system proxy and the OS verifier by default), both packages installed from the overlay's `requirements.txt`; settings are `AGENTS_*` (`AGENTS_PROXY` and kin). |
| `recovery` | A config-recovery playbook for reconstructing a lost `~/.agents`. |

## Managing overlays

```bash
dotagents overlays add python engineering  # install into the scope, publish skills, merge rules/routing
dotagents overlays list                # installed (discovered) + available (from the repos)
dotagents overlays sync 'py*'          # refresh installed overlays matching a glob
dotagents overlays remove python       # delete the overlay dir, unpublish its skills, un-merge its rules
dotagents overlays show python         # describe one (manifest, requires, setup, skills, source)
```

Scope is **project** by default, or **user** with `-g` / `--global` (the configurable
store). Each overlay:

- installs as a directory (kept, discoverable), after what its manifest `requires`;
- is checked first: a manifest that does not validate, a tree with no digest (a
  symbolic link, a name AOS refuses), a machine lacking what it needs, or a pin it
  does not match refuses it before anything is written;
- records the repo and commit it came from, its tree digest and the digest of every
  file it installed, beside it in `<store>/overlays/.dotagents-install/<name>.json`;
- has its `routing` / `rules` merged into `AGENTS.md`'s managed block, which is
  recomposed from the pristine base over every installed overlay in priority order;
- has its skills published into the scope's shared skills dir, so every agent that
  reads that dir sees the same skills.

`sync` refreshes each overlay from the repo it was installed from (`--repo` replaces
that source for the run) and makes the install hold exactly the overlay's files:
changed files are replaced and everything else is removed -- tool caches,
`__pycache__`, anything a setup wrote there -- anything edited there backed up under
`<store>/install_backup/` first. The installed copy then has the overlay's tree
digest; setup writes into its own state directory instead. A `requires` added
upstream is installed. An installed overlay is upstream's copy, not a place for your
edits.

### Your additions to an overlay's files

Put your own text for an overlay file at the **same path under the store root**: an
addition to the rust overlay's `kb/RUST.md` goes in `~/.agents/kb/RUST.md` (or
`<project>/.agents/kb/RUST.md` for one project). `sync` never touches it, and agents
read it alongside the overlay's file, winning on conflict: the base `AGENTS.md` tells
them to, and `dotagents context` lists every addition it finds (for each `.md` an
overlay ships in a subdirectory other than `skills/`). Private names, local tool paths
and links into your own notes belong there, never in the overlay.

Removing an overlay deletes only its directory and unpublishes only the skills **it**
published (a copy the user edited is left alone). Its lines in `AGENTS.md`'s managed
block leave with it: the block is recomposed from the pristine base over the overlays
that remain. `remove` refuses an overlay another installed overlay requires, unless
`--force`; `list` and `show` flag a requirement no installed overlay provides.

## Where overlays come from: repos

`add`, `list` and `show` resolve a name against a list of **repos**, and for `add` the
first repo that offers the name wins. (`sync` goes back to the repo each overlay was
installed from.) A repo is **always a collection** of overlays, in one of two shapes:

- a **directory of overlays** — each subdirectory is an overlay, `<dir>/<name>/` (a
  checkout of the `repo` branch has them under `overlays/`, any folder);
- a **registry** — a JSON, TOML or YAML file mapping `<name-or-alias>` to the
  **source** of that one overlay (the whole document, or its `overlays` key). TOML
  uses `tomli` below Python 3.11 (installed with the package, bundled in the
  `.pyz`); YAML needs `pip install 'dotagents-cli[yaml]'`.

Both the repo and each source are written as a spec, `<location>[@<ref>][#<path>]`:
`<location>` is a local path, an `http(s)://` URL, or a git repository
(`https://…/x.git`, `git@host:org/x.git`, `ssh://…`, a local `…/x.git`; prefix `git+` to
force git); `<ref>` is a branch, tag or commit; `<path>` is inside it. The two differ
in what the path names:

- for a **repo**, the collection: a directory (of overlays) or a registry file in the
  checkout, the checkout root with no path — `--repo …/dotagents.git@repo#overlays`
  names the `overlays/` directory of the `repo` branch;
- for a **source** (a registry value), the overlay: the path is the overlay's root
  directory, and with no path **the repository root is the overlay** —
  `…/dotagents.git@repo#overlays/net` is the `net` overlay itself. Given as `--repo`,
  that form would list `net`'s own subdirectories as overlays.

A `scheme://` location that is not git is a **uripath path** (pathapi's URI layer, the
`pathapi-uri` distribution). With the `uri` extra (`pip install 'dotagents-cli[uri]'`;
`[sftp]`, `[s3]` and `[repo]` add `sftp:`, `s3:` and `github:` / `gitlab:` / `repo:`)
any scheme uripath speaks works like a local path: an `http(s)://`
directory listing, an `sftp`, `s3`, `dav` or `github` tree is a directory of overlays
as a repo or one overlay as a source, a file is a registry; an archive is a tree too —
`zip:<archive-uri>!/<inner path>` (`tar:`, or `archive:` to auto-detect), the archive
itself local (`file:`) or at any URL: `zip:https://h/overlays.zip!/overlays`. Remote content is
materialized into `<user store>/.cache/overlays/uri/`, synced once per run, and used
from there; `file://` is a local path, no copy. Without the extra, an `http(s)://`
location can still be a registry file (fetched with the standard library), and
anything else says which extra it needs. The `.pyz` vendors none of this unless built
with `build-pyz --extras uri`.

A source written as a **relative path** (`./python`, `../shared/net`, `overlays/rust`)
is relative to the registry it is in: the registry file's directory for a local file,
and for a registry inside a git checkout the **same repository at the same ref**, with
the path joined onto the registry file's directory (`overlays/reg.toml` saying `./rust`
means `<repo>@<ref>#overlays/rust`), and for a registry at a URL the URL beside it
(`https://h/cfg/reg.json` saying `./rust` means `https://h/cfg/rust`).

Git checkouts are cached under `<user store>/.cache/overlays/`, one per repository and
ref, and fetched again once per run. That directory carries a `.gitignore` of `*`, so
a store kept in git never records them. Credentials in a repo URL never reach a log
line, a cache directory name or a checkout's `.git/config`, and an install record
keeps the URL without them (so `sync` of such an overlay needs the repo configured
again, or `--repo`).

The repos, in order:

1. `--repo <spec>` (repeatable);
2. `$AGENTS_OVERLAYS_REPO_<KEY>=<spec>`, sorted by `KEY`, then `$AGENTS_OVERLAYS_REPO`
   (the default repo);
3. `<project store>/dotagents.{json,toml,yaml,yml}`, then the user store's.

No build bundles overlays; with none of these configured, `add` fails with an error
that names them.

```toml
# ~/.agents/dotagents.toml
[overlays]
engineering = "https://github.com/you/dotagents.git@repo#overlays/engineering"
python      = "https://github.com/you/dotagents.git@repo#overlays/python"
mytool      = "git@github.com:you/mytool-overlay.git@v2"      # no path: the repo root is the overlay
local       = "~/src/overlays/local"                           # a local overlay directory
mine        = "./my-overlays/mine"                             # relative to this file: ~/.agents/my-overlays/mine
```

```bash
dotagents overlays add engineering -g                                        # from the registry above
dotagents overlays add net -g --repo https://github.com/you/dotagents.git@repo#overlays
dotagents overlays sync -g                                                   # refetches from each recorded repo
```

## What `dotagents env` wires for every overlay

For every active overlay `dotagents env` exports **`$<NAME>_OVERLAY_ROOT`** -- the
overlay's installed directory (`NAME` is the overlay's name upper-cased, `-` → `_`:
`private-sync` → `PRIVATE_SYNC_OVERLAY_ROOT`). Then, overlay by overlay in priority
order, it applies the overlay's environment, each field's defaults first: its root
`env` file (dotenv: `NAME=value` lines) and the env files it declares, its `env`
values, its path additions -- by default `bin/` prepended to
`PATH` and `lib/python/` prepended to `PYTHONPATH` when they hold a file (listed in
`AGENTS_PYTHONPATH` too) -- and last its env scripts: one `env.<ext>`, the first in
the order `.py`, `.js`, `.mjs`, `.cjs`, then `.ps1` on Windows, then `.bash`, `.sh`
elsewhere, that this machine can run, then those it declares. A bare `lib/` means
nothing: Python modules go in `lib/python/`. An entry naming a default's file or
directory (a `paths` entry with `path = "bin"`, an env script `env.py`) replaces
that default with its own settings, in the default's place; `[defaults]` turns one
off.

**Substitution.** A text value -- in `env`, an `aos` or `dotenv` env file, or an MCP
server's `env` -- can use other variables: `CACHE = "${TOOL_HOME}/cache"`. Each value is
substituted once, when it is set, against the environment as it stands: every earlier
overlay's values, every overlay's root and state variables, this overlay's earlier env
files and the earlier lines of the same `dotenv` file, and its own `AOS_OVERLAY_ROOT`,
`AOS_OVERLAY_NAME` and `AOS_OVERLAY_STATE`. The entries of one mapping (`env`, one
`aos` file) do not see each other. Only the braced form is read: `${NAME}`,
`${NAME:-default}`, `${NAME-default}`, `${NAME:+alt}`, `${#NAME}`, `${NAME:1:3}`,
`${NAME/pattern/text}`, `${NAME#prefix}`, `${NAME%suffix}`, `${NAME^^}`, `${NAME,,}`;
`$$` writes a `$`, and `$NAME`, `$(...)` and backticks are characters. A name that is
not set is an error unless the value gives a default, and a placeholder never reads a
variable that holds a secret: either refuses the overlay's environment, and the report
names the variable and the placeholder, never a value. A single-quoted `dotenv` value is
kept as written:

```text
TOOL_HOME=${AOS_OVERLAY_ROOT}/tool
TOOL_CACHE=${TOOL_CACHE_DIR:-${TOOL_HOME}/cache}
TOOL_PROMPT='${USER}$ '
```

An overlay's own processes (its env scripts, setup, MCP servers) get its values as they
were substituted for the session. The launchers of its
declared python `scripts` (in `<venv>/overlays/` of the agents' venv) go first on
`PATH`. An overlay with a state directory also gets `$<NAME>_OVERLAY_STATE`.

The root variable is how overlay content refers to itself and to other overlays: a
routing line reads `$ENGINEERING_OVERLAY_ROOT/flows/PLAN.md`, never `~/.agents/flows/PLAN.md`
-- overlays install under `overlays/<name>/`, and the store itself can live anywhere
(`$AGENTS_HOME`). `dotagents context` expands both forms, `$ENGINEERING_OVERLAY_ROOT` and
`<ENGINEERING_OVERLAY_ROOT>`, at assembly time for its stdout and JSON output; a file
written by `context --write-agent` keeps the variable.

An env script runs with the overlay's own environment and the root as its
working directory, and prints one JSON object -- across several lines if it likes --
or one object per line (`{"NAME": "value"}`, `null` to unset); a default one gets no
command-line arguments. Its store can turn env scripts off
(below); each run is reported. An overlay's `pre.env*` and `local.env` mean nothing:
those are the stores' own files (a store's `pre.*` files run before every overlay,
its `env.py` / `env` / `local.env` after them, so they read and override what the
overlays set). An overlay whose environment cannot be applied (a secret not
granted, a missing env file) contributes nothing, and `env` says which variable and
why. Every activation reports the overlay's **weakenings** in one
warning line -- `unpinned` for an overlay with no pin, `env-sensitive`,
`env-passthrough`, `secret-read`, `env-script`, ... -- naming variables and paths,
never values.

## `OVERLAY.md` and `overlay.cfg`

The manifest is AOS's, in one of two forms. **`OVERLAY.md`** is the standard one: the
manifest is its YAML front matter (between `---` lines; a first line inside the fence
`#!toml` or `#!json` names another format), and anything after the front matter is the
overlay's context, in place of `CONTEXT.md`. A `CONTEXT.md` beside a body is ignored,
whatever names it, and reported (a warning, and `overlays show`). **`overlay.cfg`**
stays readable: TOML by default, JSON with a first line `#!json`, YAML with `#!yaml`.
An overlay has one of the two: both is refused. YAML is read without anchors, aliases,
tags, merge keys or more than one document, and TOML, JSON and YAML read to the same
data. A manifest is static data, read without running anything; one that does not read
or validate is ignored whole, with a warning saying where it is wrong, and
`overlays add` refuses it. `overlay.toml` is not read any more (a warning names it):
rename it, and move what was inferred into declarations (see the
[changelog](../changelog.md)). The full grammar is the
[specification's](https://boriken-dev.github.io/agent-overlay-spec/spec/v0/manifest/).
Keys dotagents applies:

| Key | Meaning |
| --- | --- |
| `name`, `description`, `priority` | Display name, summary, merge order (lower sorts earlier; default 500). |
| `requires` | Other overlays this one depends on; `add` installs them first. |
| `routing`, `rules` | Lines for "Load on demand"; markdown files whose rule bullets join "Always-on rules", after the default `rules/<name>.md`. |
| `context` | Files added to the agent's context, after the default: the body of `OVERLAY.md`, else `CONTEXT.md`. |
| `skills` | Skills added to the default ones (every `skills/<name>/SKILL.md`). |
| `[defaults]` | `bin`, `lib`, `env`, `env_scripts`, `setup`, `context`, `skills`, `rules = false` each turn one default off; any other key is refused. |
| `env`, `env_files`, `paths` | Environment values (text with `${NAME}` placeholders, a path in the overlay, a secret reference), env files (`aos` or `dotenv`), and further path additions (`prepend` / `append`, per OS). |
| `env_scripts` | Programs that compute variables (`interpreter`, `path`, `timeout`), after the default `env.<ext>`; one naming a root `env.<ext>` configures it; the store can turn them off. |
| `runtime` | `os`, `commands`, `python` (version, `requirements` or `lock` + `lock_digest`, `environment`), `node` (version, `package`, `environment`): probed before an install. |
| `setup` | Variants per OS (`interpreter`, `path`, `args`, `timeout`, `network`), tried before the default `setup.<ext>`; one naming a root `setup.<ext>` configures it. Told its phase: `install`, `sync`, `remove`. |
| `[scripts]` | Commands: `name = "path.py"` or `{ path, interpreter, os }`; dotagents launches python scripts. No file is a command by its name. |
| `[harness.<id>]` | `context` and `skills = false` for one harness (`claude-code`, `codex`, `gemini-cli`, ...); `model` / `effort` are shown, not applied. |
| `[mcp]` | MCP servers and the skills that expose them. See [MCP servers](#mcp-servers). |

```toml
# engineering/overlay.cfg: what is not a default
name = "engineering"
requires = []
rules = ["rules/ENGINEERING.md"]
routing = ["- Write or revise a plan → $ENGINEERING_OVERLAY_ROOT/flows/PLAN.md"]

[runtime]
commands = ["git"]
```

The same in `OVERLAY.md`, with the context after it:

```markdown
---
name: engineering
requires: []
rules:
  - rules/ENGINEERING.md
routing:
  - "- Write or revise a plan → $ENGINEERING_OVERLAY_ROOT/flows/PLAN.md"
runtime:
  commands: [git]
---
What every session of this overlay is told, as Markdown.
```

## Pins, grants and env scripts: the store's own settings

What an overlay may do is the store's decision, never the overlay's. It lives in the
store's registry document, `<store>/dotagents.toml` (or `.json`, `.yaml`), in its
`overlays` form; an overlay uses the settings of the store it is installed in:

```toml
secrets_dir = "~/.agent-secrets"      # where `file` secret references read
env_scripts = false                   # this store's overlays run no env script (default true)

[overlays.docs-tools]
source = "https://example.org/agents.git@<commit>#overlays/docs-tools"
pin = "sha256:<64 hex digits>"        # its tree digest (`overlays show` prints it)
env_scripts = true
grants = { env = ["DOCS_TOKEN"], secret = ["release-signing"], file = ["docs/token"] }

[secrets.release-signing]
provider = "env"
key = "RELEASE_SIGNING_KEY"
```

- **A pin** refuses any other bytes: `add` and `sync` check the source before writing
  it, and every session start (`env`, `context`, command discovery) checks the
  installed copy; a mismatch, or a cache such as `__pycache__` inside the copy, makes
  the overlay contribute nothing, with a warning. Change the pin to accept new bytes.
  An overlay with no pin is reported `unpinned` each time it is activated.
- **A grant** lets the overlay read one secret: a host variable (`env`), a named
  secret (`secret`), or a file under `secrets_dir` (`file`). Nothing is granted by
  default, and an ungranted reference leaves the overlay's environment unapplied
  (unless it is `optional`). MCP `env_passthrough` and `headers_env` need an `env`
  grant too.
- **`env_scripts = false`** stops the overlay's env scripts (its `env.<ext>` and
  the ones it declares) from running; they run by default, each run reported.

## MCP servers

`dotagents mcp` forwards the tools of the MCP servers an overlay declares. Each
`[[mcp.servers]]` entry has a `name`, a `transport` and an optional `timeout` in
seconds (default 60):

| `transport` | Keys |
| --- | --- |
| `stdio` | `command` (a name on `PATH`, or a path in the overlay), `args`; `env` sets values (a literal, a path in the overlay, or a granted secret reference); `env_passthrough` names variables passed on from the environment, when granted. |
| `streamable-http` | `url` (no credentials, no fragment); `headers_env` maps a header name to the granted variable holding its value. Needs `dotagents-cli[mcp-http]`. |

```toml
[mcp]

[[mcp.servers]]
name = "builder"
transport = "stdio"
command = "uvx"
args = ["builder-mcp"]
env = { BUILDER_MCP = "stdio" }
env_passthrough = ["BUILDER_TOKEN"]
timeout = 60

[[mcp.servers]]
name = "docs"
transport = "streamable-http"
url = "https://docs.example/mcp"
headers_env = { Authorization = "DOCS_TOKEN" }
timeout = 30

[[mcp.skills]]
name = "code-search"
server = "builder"
```

A `[[mcp.skills]]` entry names a skill of the overlay (`skills/code-search/SKILL.md`
must exist) and the server behind it; that server's tools are then listed as
`code-search.<tool>` instead of `builder.<tool>`, once per skill when it backs
several. `dotagents mcp --prefix 'dotagents.{name}'` puts every forwarded name under
`dotagents.`.

A stdio server starts in its overlay's root and gets `PATH`, `HOME`, `USERPROFILE`,
`SYSTEMROOT`, `TEMP` and `TMP`, the granted variables in `env_passthrough`, its `env`
and its overlay's root variable; nothing else of the environment. A manifest that
does not validate declares no server; a server that fails to start is left out with a
warning. Only tools are forwarded.

## Skills

An overlay's skills are its `skills` list when it has one, else every
`skills/<skill-name>/` holding a `SKILL.md`. Publishing symlinks each (or copies,
where symlinks are unavailable) into the scope's shared skills dir, so every agent
that reads that dir picks up the same skills; `[harness.<id>] skills = false` keeps
them out of that harness's own skills dir (the context's skills listing still names
them). Removing the overlay unpublishes only the skills it published, then sweeps any
now-broken symlinks.

### Skills from elsewhere

`skills` also names skills the overlay does not carry, like the lines of a `requirements.txt`:

```toml
skills = [
  "summarize-docs",                      # bundled: skills/summarize-docs/SKILL.md in the overlay
  "pdf",                                 # a name a catalog resolves
  { name = "review", source = "https://github.com/example/skills.git#skills/review", pin = "sha256:..." },
]
```

`overlays add` shows each of them first (the overlay naming it, its source, its catalog, pinned or
`UNPINNED`) and takes **one yes for the overlay and its skills together**: `--yes`, or typed at the
terminal itself. Until then nothing is fetched and nothing is installed, whether the overlay came
from a repo, a catalog or a project. Then each skill is fetched from its Git source without your
credentials, SSH agent or prompts and without following a redirect, read out of Git's object store
(nothing is checked out and nothing in it runs), refused when its tree digest is not the pin
(`pin-mismatch`), when it has no `SKILL.md` (`not-a-skill`) or front matter in YAML (`skill-unreadable`)
or when that names another skill (`skill-name-mismatch`), and kept under
`<store>/overlays/.dotagents-skills/` with a record of what you accepted (source, pin, digest, catalog,
the overlays naming it). A refused skill is reported and not delivered; the overlay still installs and the
command exits 1. Fetched skills are published into the shared skills dir and linked into agents' own
dirs like bundled ones.

- A name no catalog offers is **missing**, one two catalogs offer **ambiguous**: both are reported with
  the overlay naming them, never guessed, and the overlay applies without them. Catalogs are given with
  `--skill-catalog LABEL=FILE|URL` (a document with a `skills` table; repeatable).
- `sync` keeps what you accepted without fetching it again. A skill new or changed since (another
  source, pin or catalog resolution) is an update: it is shown and fetched only with a yes, never
  because a source or a catalog changed.
- Two overlays naming one skill alike are one skill. A skill from elsewhere never takes a name
  another skill already holds.
- Removing or syncing away the last overlay naming a fetched skill **offers** to remove it (`remove
  --yes`, or at the terminal; otherwise it is kept and said so). A skill you placed in the shared dir
  yourself is never replaced or removed. Through `dotagents mcp --strict`, `--yes` is not taken.
- Only Git sources are fetched here; a source that is a plain web URL is reported as not delivered.

## Python dependencies and the scope venv

Every store has a Python venv, `<store>/.pyvenv/<X.Y.Z>-<os>-<arch>/` (for example
`~/.agents/.pyvenv/3.14.7-nt-arm64`), made from the Python that runs dotagents.
A store's `.pyvenv/` holds these **agents' venvs only**: skills, overlay helpers and
setup scripts run there. A project's own code gets its venvs beside the store, in
the project root's `.pyvenv/` (`<project>/.pyvenv/<version>-<os>-<arch>/`, which the
python overlay's `dotagents pyvenv` creates); only a venv dotagents itself synced is
ever used as an agents' venv. `init` and every
`overlays add` / `sync` / `remove` keep it current:

- The user store's venv always has **duho**, so any skill can build a command line
  on it.
- A project gets its own venv only when one of its overlays has Python dependencies,
  a library dir or a script; otherwise a project session uses the user venv.
- The venvs **layer like everything else**. A `.pth` file in each venv lists, highest
  precedence first: the store's own overlay libs and its shared Python environment,
  then the user venv's packages, libs and shared environment, then the system
  store's. dotagents itself comes last. So in a project venv, the project's packages
  and libs win, then the user's, then the system's.

### Dependencies and their environments

An overlay's packages install by default from `lib/python/requirements.txt` and
`lib/node/package.json`, on `add` and on every `sync`, after the copy lands and before
setup; `[defaults] deps = false` turns that off, and a `[[runtime.python]]` variant
naming `requirements` or `lock`, or a `[[runtime.node]]` variant naming `package`,
replaces the default file. Every install is reported `deps-network`, and a file that
is not pinned `unlocked-dependencies`.

- **Pinned.** A requirements file is a lock when every requirement has a `--hash`
  (only some is refused, `partial-lock`), a declared `lock` too (its `lock_digest`
  checked first); a lock installs with `--require-hashes --no-deps
  --only-binary=:all:`. Requirement lines only: no `-r`, index option, URL or path. A
  `package.json` is pinned by the `package-lock.json` beside it: `npm ci`, else `npm
  install`. npm works from copies of those two files only, so no `.npmrc` in an
  overlay configures it.
- **Environments.** `environment = "shared"` (the default) is the store's one
  environment per runtime; a name is shared by the store's overlays that choose it;
  `"isolated"` is the overlay's own. Python ones live in
  `<store>/.pyvenv/<triplet>/envs/`, Node.js ones in `<store>/.nodeenv/<os>/`. The
  shared Python environment is in the venv's `.pth`; a named or isolated one goes on
  `PYTHONPATH` after the overlay's `lib/python/`, and Node.js `node_modules` on
  `NODE_PATH` after `lib/node/`.
- **Together.** The overlays of one environment install in one run, in priority
  order: pip in hash-checking mode only when every file is a lock (otherwise the
  hashes are left out and it is reported unlocked), npm with `ci` only for a lone
  locked member. Two of them pinning one package to two versions (Python `==` pins,
  names compared normalized; `package.json` ranges) are both refused, with the
  package named; the environment holds the rest. Give one of them its own
  environment.
- **Rebuilt.** Every `add`, `sync` and `remove` that touches an environment builds it
  again, into a new directory, from the files of the overlays left in it, and switches
  to it only when that worked: a failed build leaves the old one, and nothing a
  removed overlay needed stays behind. An environment no overlay uses any more, and an
  isolated one on removal, is deleted.
- `dotagents env` points **`AGENTS_PYTHON`** at the active venv (and names it in
  `AGENTS_PYVENV`). A `python` setup or env script and the stores' `env.py` run under
  it, and the managed block tells agents to run skill scripts with it. A store's own
  `lib` the venv layers stays off `PYTHONPATH`; an overlay's library dir is a path
  addition, on `PYTHONPATH` for every process of the session.

**No ensurepip, no network?** pip runs from its wheel, pinned by version (25.3, the
last for Python 3.9) and hash, found in this order: the store's cache, a copy
vendored into the `.pyz` (`build-pyz --with-pip`), `AGENTS_PIP_SOURCE` (a directory
of wheels, or any URI pathapi-uri reads: `file:`, `s3:`, `sftp:`, ...), pip's own
`find-links`, then your index (`PIP_INDEX_URL` or the mirror in `pip.ini` /
`pip.conf`). `AGENTS_PIP_SOURCE` is also handed to pip as `--find-links` for the
requirements themselves.

Opt out per run with `--no-venv`, or for good with `AGENTS_NO_PYVENV=1`.

## Setup

An overlay's setup is, by default, one `setup.<ext>` at its root, chosen in the
order `.py`, `.js`, `.mjs`, `.cjs`, then `.ps1` on Windows, then `.bash`, `.sh`
elsewhere -- the first that this machine can run -- taken to need the network
(reported `setup-network`). One present that cannot run is reported
`script-not-run`; `[defaults] setup = false` turns it off. Declared `[[setup]]`
variants are tried first, the default last; the first whose `os` matches runs. A
variant naming a root `setup.<ext>` configures that file instead (its interpreter,
args, timeout, os, and `network`, false unless declared):

```toml
[[setup]]
os = ["windows"]
interpreter = "powershell"     # python, bash, sh, powershell, node
path = "setup/setup.ps1"
timeout = 120                  # seconds; default 300, 0 for no limit

[[setup]]
interpreter = "python"
path = "setup/setup.py"
args = ["--quiet"]
network = true                 # reported as the weakening setup-network
```

- It is told its **phase**, as its first argument (before `args`) and as
  `AOS_SETUP_PHASE`: `install` the first time the overlay is prepared here, `sync` on
  every `add` or `sync` after that (whether or not the files changed), and `remove`
  when `overlays remove` deletes the overlay, before its copy, state and isolated
  environment go. Each phase runs after the overlay's dependencies are installed;
  `install` first empties the state directory. A record of the last preparation that
  succeeded (digest and phase) is kept per overlay and OS, and the overlay is
  activated only for that digest: one dotagents finds at another digest (copied in by
  hand, or prepared with `--no-setup`) is refused until `overlays sync` prepares it.
  `--no-setup` prepares nothing.
- It must be **idempotent** -- running it twice leaves what running it once did -- and
  treat a phase it does not know as success with nothing to do (exit 0):

  ```sh
  case "${1:-}" in
    install|sync) ... ;;
    remove) ;;    # nothing outside the state directory to undo
    *) ;;         # a phase this script does not know
  esac
  ```
- **Interpreter**: `python` is the scope venv's (`AGENTS_PYTHON`); the others are
  found on your `PATH` (never an overlay's), PowerShell as `pwsh -NoLogo -NoProfile
  -NonInteractive -File`, Git's bash and sh on Windows, never a `.cmd` or `.bat`.
  Arguments are passed as a list, never through a shell.
- **cwd** is the installed overlay root. **Write only into `AOS_OVERLAY_STATE`**, a
  directory outside the root that belongs to this overlay on this OS, kept across
  syncs (so `sync` updates in place; `install` starts it empty) and deleted on
  removal; the root keeps its tree digest, and sessions find the state as
  `$<NAME>_OVERLAY_STATE`.
- **Environment**: your environment, every overlay's root variable, the path
  additions of the overlays it requires, its own declarations, `AOS_OVERLAY_ROOT`,
  `AOS_OVERLAY_NAME`, `AOS_OVERLAY_STATE`, `AOS_SETUP_PHASE`, and `AGENTS_HOME` (the user store),
  `AGENTS_SCOPE_ROOT` (the store it is installed into), `AGENTS_SCOPE`. No other
  overlay's values or secrets.
- **Time limit**: the variant's `timeout`, or `--setup-timeout`; a step still running
  then is stopped with everything it started, and that is a failure (exit 124).
- **Network**: dotagents cannot deny a process the network, so a variant that does not
  declare it is reported `network-not-enforced`.
- A **non-zero exit fails the install** with a clear error, and a fresh `add` is
  rolled back. An `install` or `sync` that fails leaves the record as it was: a
  sync of the digest already prepared keeps it active, and a new digest is refused
  until a preparation of it succeeds (the copy is replaced in place, so the previous
  digest cannot stay active). A failed `remove` is reported and the overlay is
  removed anyway.

See [Authoring an overlay](authoring.md) to build your own.
