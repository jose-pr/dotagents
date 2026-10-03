# <project_name>

<!-- EXECUTOR: badge row — fixed order: Version, [language's extra registry badges per its
     doc], License, Docs, CI. Never reorder, add or drop one; no license yet = no License badge. -->
[![Version](https://img.shields.io/badge/<registry_badge>-blue.svg)](<registry_url>)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/<gh_org>/<project_name>/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-latest-blue.svg)](https://<gh_org>.github.io/<project_name>/)
[![CI](https://img.shields.io/github/actions/workflow/status/<gh_org>/<project_name>/test.yml)](https://github.com/<gh_org>/<project_name>/actions/workflows/test.yml)

<!-- EXECUTOR: 2-4 sentence value proposition; bold the core promise; link docs site. -->
A **<one-line value proposition>**. <What it does, for whom, and the one design
rule that makes it trustworthy.>

## Features

<!-- EXECUTOR: bullets or a capability matrix table; lead with what users get, not internals. -->
- **<Feature>** — <why it matters in one clause>.

## Installation

```bash
<install command>
```

Optional features/extras:

<!-- EXECUTOR: one row per extra/feature flag; delete the section if the project has none. -->
| Extra/flag | Adds | Needed for |
| --- | --- | --- |
| `<extra>` | `<dependency>` | <capability it unlocks> |

## Quick start

<!-- EXECUTOR: one runnable snippet per headline capability, smallest first. -->
```
<minimal example>
```

## Command line

<!-- EXECUTOR: only when the project ships a command; delete the section otherwise.
     Every command shown runs as written. -->
```bash
<command>
```

## API overview

<!-- EXECUTOR: project-specific sections go between "API overview" and "Development";
     the headings above and below keep this spelling and this order. -->
| Module | Purpose |
| --- | --- |
| `<module>` | <one-line purpose> |

## Development

<!-- EXECUTOR: literal setup + test commands from kb/<LANG>.md, venv/lockfile-exact forms. -->
```bash
<setup command>
<test command>
```

### Releasing

This project follows [Semantic Versioning](https://semver.org/) and keeps a
[`CHANGELOG.md`](CHANGELOG.md). Pushing a tag matching `v*` triggers the release
workflow: test gate → build (checking the tag names the version built) → a strict
docs build as a gate → GitHub release → publish. The release workflow never deploys
the docs site itself: for a final release its last job dispatches the docs workflow
at the tag, which owns every Pages deploy.

## License

MIT — see [LICENSE](LICENSE).
