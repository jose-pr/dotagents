# Standard: README and AGENTS.md format

Base standard. Every library's `README.md` and `AGENTS.md` files have the
same shape, so a reader who knows one knows where to look in the next. Index:
[README.md](../README.md). Which `AGENTS.md` is which, and what ships, is in
`$ENGINEERING_OVERLAY_ROOT/flows/REPO.md`; this file fixes their format.

## `README.md`

### Title and badges

The title is the distribution name. The badge row follows it directly: these
five badges, in this order, with this alt text, one per line.

```markdown
# <dist>

[![Version](https://img.shields.io/pypi/v/<dist>.svg)](https://pypi.org/project/<dist>/)
[![Python versions](https://img.shields.io/pypi/pyversions/<dist>.svg)](https://pypi.org/project/<dist>/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/<gh_org>/<repo>/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-latest-blue.svg)](https://<gh_org>.github.io/<repo>/)
[![CI](https://img.shields.io/github/actions/workflow/status/<gh_org>/<repo>/test.yml)](https://github.com/<gh_org>/<repo>/actions/workflows/test.yml)
```

1. Version
2. Python versions
3. License
4. Docs
5. CI

- No badge is added, dropped or reordered. A repository with no license yet
  omits the License badge and the License section, and adds both when one is
  chosen.
- The CI badge points at `test.yml`, never the release workflow, and carries
  no custom label.
- Every link is absolute. The README is the package long-description and is
  rendered on the index, where a relative link is broken.

### Opening

Two to four sentences after the badges: what the library is, with the core
promise in bold, and a link to the docs site.

### Sections

These headings, with this spelling, in this order:

| Heading | Holds |
| --- | --- |
| `## Features` | what a user gets, as bullets or a capability table |
| `## Installation` | the install command, then a table of extras |
| `## Quick start` | one runnable snippet per headline capability, smallest first |
| `## Command line` | only when the library ships one; the commands, each runnable as written |
| `## API overview` | a table of public modules and their purpose |
| `## Development` | setup and test commands, then `### Releasing` |
| `## License` | one line and a link |

A section specific to the project goes between `API overview` and
`Development`. Not `Install`, `Quickstart`, `Quick Start`, `CLI` or `Usage`.

## Shipped `AGENTS.md` (`src/<pkg>/AGENTS.md`)

The API header a consumer reads instead of the source.

### Opening

```markdown
# `<pkg>` — public API header

Header-file-style reference for the `<pkg>` package: every public export with
its signature, arguments, contract and gotchas, so the package can be used
without reading its source. It ships inside the package and is
self-contained. Development documentation lives with the source at
<https://github.com/<gh_org>/<repo>>.
```

Then, in this order, one short paragraph each:

1. where names are imported from, and which modules are private;
2. how to install, and what each extra adds;
3. `<pkg>.__version__`.

### Body

- One `##` section per public module or topic, in the order a user meets
  them. The heading names the import path: `## Server (`<pkg>.server`)`.
- Each entry gives the signature in a code block, then what it returns or
  guarantees, then arguments and defaults, then what it raises.
- A sync and async twin are documented together, the differences stated once.

### Tail

These sections close the file, in this order, each present only when it has
content:

1. `## Exceptions`
2. `## Command line`
3. `## Environment variables`
4. `## Gotchas`

### Rules

- **It describes the API as it is.** No history, no "previously", no account
  of how something was found. A changed behaviour is a changelog entry.
- **Self-contained**: no repo-relative links. An installed consumer has no
  repository.
- **Current in the same commit** as the API change it describes.
- A large surface adds per-subpackage headers, `src/<pkg>/<sub>/AGENTS.md`,
  in the same shape; the top header lists them.

## Repo-root `AGENTS.md`

Contributor orientation for a checkout. It does not ship and is not the API
reference. These sections, in this order:

1. a table of the shipped headers and what each covers
2. `## Layout`
3. `## Environment`
4. `## Checks`
5. `## Conventions`
6. `## Releasing`

It links freely within the repository and never into private working notes.
