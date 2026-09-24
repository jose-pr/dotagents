#!/usr/bin/env bash
# Smoke-test a built dotagents.pyz. CI tooling: run by the Test workflow on a
# fresh build and by the Release workflow on the artifact it is about to ship.
#
#   bash tools/pyz_smoke.sh <dotagents.pyz> <overlays-dir>
#
# <overlays-dir> holds the example overlays (`overlays/` of a `repo` branch
# checkout). PYTHON picks the interpreter (default: python).
#
# The main thing guarded is the zipapp shim: duho reads each flag's spelling and
# help from its module's source, which a zipapp does not have on disk, so a
# regression degrades `--from FROM` to a name-derived `--from-` and drops help
# text -- in bundled command modules, in discovered overlay commands, and in a
# base class from another module. Every run happens in a scratch HOME (and
# USERPROFILE, which is what Path.home() reads on Windows) and a scratch
# working directory, so nothing touches the real store or the checkout.
#
# Output is captured before it is matched, so a crashing command fails the run
# (set -e) instead of a grep passing or failing on half its output, and every
# negative check is an explicit `if` (bash's errexit ignores `! pipeline`).
set -euo pipefail

usage() { echo "usage: $0 <dotagents.pyz> <overlays-dir>" >&2; exit 2; }
[ $# -eq 2 ] || usage
[ -f "$1" ] && [ -d "$2" ] || usage

PYTHON=${PYTHON:-python}
PYZ=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
OVERLAYS=$(cd "$2" && pwd)

fail() { printf 'pyz_smoke: FAIL: %s\n' "$*" >&2; exit 1; }
# A path as the (possibly Windows) Python sees it.
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
# Run the pyz; CRs stripped (Python on Windows writes CRLF to a pipe).
pyz() { "$PYTHON" "$(native "$PYZ")" "$@" | tr -d '\r'; }
# has <text> <fixed string> / has_re <text> <ERE> / lacks <text> <fixed string>
has() { grep -qF -- "$2" <<<"$1" || fail "expected '$2' in:"$'\n'"$1"; }
has_re() { grep -qE -- "$2" <<<"$1" || fail "expected /$2/ in:"$'\n'"$1"; }
lacks() { if grep -qF -- "$2" <<<"$1"; then fail "unexpected '$2' in:"$'\n'"$1"; fi; }
# A flag exactly as argparse lists it: followed by a space, a comma or the end.
flag() { printf -- '%s([[:space:],]|$)' "$1"; }

SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
export HOME="$SCRATCH/home"
mkdir -p "$HOME" "$SCRATCH/work"
if command -v cygpath >/dev/null 2>&1; then
    USERPROFILE=$(cygpath -w "$HOME")
    export USERPROFILE
fi
unset AGENTS_HOME AGENTS_PROJECT_ROOT AGENTS_SYSTEM_ROOT AGENTS_CMDS_PATH \
      AGENTS_RUNTIME_SET CLAUDE_PROJECT_DIR AGENTS_OVERLAYS_REPO
cd "$SCRATCH/work"
OVL=$(native "$OVERLAYS")

echo "pyz_smoke: $PYZ (overlays: $OVERLAYS)"

# The repo's tools/ is CI tooling and never ships in the artifact.
members=$("$PYTHON" -c "import sys, zipfile; print('\n'.join(zipfile.ZipFile(sys.argv[1]).namelist()))" "$(native "$PYZ")" | tr -d '\r')
if grep -qE '^(dotagents/)?_?tools/|(^|/)(audit\.py|cloud-setup\.sh|pyz_smoke\.sh)$' <<<"$members"; then
    fail "repo tooling shipped in the pyz"
fi
has "$members" "dotagents/_overlay/dotagents/templates/AGENTS.md"
if grep -qE '(^|/)CLAUDE|\.local\.' <<<"$members"; then
    fail "private files shipped in the pyz"
fi

# A declared flag keeps its spelling (--from FROM, not --from-).
h=$(pyz init --help)
has "$h" "--from FROM"
lacks "$h" "--from-"
has_re "$h" "$(flag --agents)"

# The shipped command surface: no private-sync commands (they come from the
# opt-in overlay), no audit (repo CI tooling), and the two bundled command
# modules, findings and launch. Matched on the command-list line, since
# `overlays`' own description mentions its `sync` subcommand.
h=$(pyz --help)
has "$h" "{init,build-pyz,context,env,overlays,about,findings,launch}"
lacks "$h" "link-project"
lacks "$h" "sync-project"
lacks "$h" "audit"

# `about`: the CLI first, then what build-pyz vendored.
a=$(pyz about)
has_re "$(head -1 <<<"$a")" "^dotagents-cli "
has_re "$a" "^duho "
has_re "$a" "^pathlib_next "
a=$(pyz about --json)
"$PYTHON" -c "import sys, json; d = json.load(sys.stdin); assert d['bundle'] and 'duho' in d['packages'], d" <<<"$a"

# launch (bundled command module): positional, declared flags and help.
h=$(pyz launch --help)
has "$h" "[agent]"
has "$h" "--command COMMAND"
lacks "$h" "--command-"
has_re "$h" "$(flag --no-context)"
has_re "$h" "$(flag --dry-run)"
has "$h" "Which agent to start"
# A dry run assembles env + context and prints the command line; the program
# need not exist (a warning, not an error, under --dry-run).
o=$(pyz launch claude --dry-run -g -- -p hi)
has_re "$o" "claude .*-p hi"

# A DISCOVERED command module (the private-sync overlay's link-project and
# sync-project) keeps its flags, help and positional through the zipapp.
pyz overlays add private-sync --repo "$OVL" -g >/dev/null
h=$(pyz --help)
has "$h" "link-project"
has "$h" "sync-project"
h=$(pyz link-project --help)
has_re "$h" "$(flag --copy)"
lacks "$h" "--copy-"
has "$h" "--store-dir STORE_DIR"
grep -qi -- "Project directory to link" <<<"$h" || fail "link-project positional help missing"
h=$(pyz sync-project --help)
has "$h" "--project PROJECT"
has_re "$h" "$(flag --no-pull)"
lacks "$h" "--no-pull-"
has "$h" "--remote REMOTE"
# The positional is accepted (not "unrecognized arguments") and links.
proj="$SCRATCH/proj"
mkdir -p "$proj"
printf '.agents\n' > "$proj/.gitignore"
pyz link-project "$(native "$proj")" >/dev/null
if command -v cygpath >/dev/null 2>&1; then
    # Windows: a symlink, or a junction where symlinks are not permitted.
    [ -L "$proj/.agents" ] || [ -d "$proj/.agents" ] || fail "link-project made no $proj/.agents"
else
    [ -L "$proj/.agents" ] || fail "link-project made no symlink at $proj/.agents"
fi

# context: flags and help survive, including -g/--agents-dir inherited from
# DotAgentsArgs, a base class in ANOTHER module.
h=$(pyz context --help)
has "$h" "--format FORMAT"
has_re "$h" "$(flag --agents)"
has "$h" "Output format: markdown"
has "$h" "--agents-dir AGENTS_DIR"
lacks "$h" "--agents-dir-"
has "$h" "--global, -g"
has "$h" "out"
# context --format json emits parseable JSON.
o=$(pyz context --format json --agents claude -g)
"$PYTHON" -c "import sys, json; json.load(sys.stdin)" <<<"$o"
# Bare context prints to stdout once there is a store. init -g writes claude's
# include and context subtracts what a harness already loads, so use codex.
pyz init -g >/dev/null 2>&1
o=$(pyz context -g --agents codex 2>/dev/null)
[ -n "$o" ] || fail "context -g --agents codex printed nothing"

# env: flags and help survive the zipapp too.
h=$(pyz env --help)
has "$h" "--format FORMAT"
has_re "$h" "$(flag --diff)"
has "$h" "Output format. Default 'auto'"
has "$h" "--agents-dir AGENTS_DIR"
lacks "$h" "--agents-dir-"
has "$h" "--global, -g"
# A store pinned with $AGENTS_HOME is actually walked.
store="$SCRATCH/pinned"
mkdir -p "$store"
printf 'export FROM_PINNED_STORE=yes\n' > "$store/env"
o=$(AGENTS_HOME="$(native "$store")" pyz env --format export -g)
has "$o" "FROM_PINNED_STORE"
o=$(pyz env --diff --format json -g)
"$PYTHON" -c "import sys, json; json.load(sys.stdin)" <<<"$o"
# Each shell format emits its own syntax, for a variable the smoke sets itself
# (identity vars like AGENT are stamped only inside a harness, so a runner has
# none), and --diff so a failure prints the change set, not the environment.
pinned_env() { AGENTS_HOME="$(native "$store")" pyz env --diff "$@" -g; }
has "$(pinned_env --format export)" "export FROM_PINNED_STORE='yes'"
has "$(pinned_env --format powershell)" "\${env:FROM_PINNED_STORE} = 'yes'"
has "$(pinned_env --format cmd)" 'set "FROM_PINNED_STORE=yes"'
has_re "$(pinned_env --format dotenv)" "^FROM_PINNED_STORE=yes$"
has "$(pinned_env)" "FROM_PINNED_STORE"

# overlays add: positional and aliased flags.
h=$(pyz overlays add --help)
has_re "$h" "$(flag --repo)"
lacks "$h" "--repo-"
has_re "$h" "$(flag --no-setup)"
lacks "$h" "--no-setup-"

# Project-scope init writes the project store.
pinit="$SCRATCH/pinit"
mkdir -p "$pinit"
(cd "$pinit" && pyz init >/dev/null 2>&1)
[ -f "$pinit/.agents/AGENTS.md" ] || fail "project-scope init wrote no $pinit/.agents/AGENTS.md"

# pyvenv/py come from the python overlay and INHERIT DotAgentsArgs from
# dotagents.cli._common: that base class's module needs repointing too, or -g
# vanishes and --global degrades to --global-scope.
pyz overlays add python --repo "$OVL" -g >/dev/null
[ -f "$HOME/.agents/overlays/python/kb/PYTHON.md" ] || fail "python overlay not installed"
h=$(pyz pyvenv --help)
has "$h" "--global, -g"
lacks "$h" "--global-scope"
has "$(pyz py --help)" "--global, -g"
pyz pyvenv -g --dry-run >/dev/null
has_re "$(pyz py -g -- -c 'print(1)')" "^1$"

echo "pyz_smoke: PASS"
