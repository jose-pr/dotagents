# <project_name>

<!-- EXECUTOR: badge row — registry badge per kb/<LANG>.md, keep CI + license badges. -->
[![Version](https://img.shields.io/badge/<registry_badge>-blue.svg)](<registry_url>)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
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

## API overview

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
workflow: test gate → build → publish → docs deploy.

## License

MIT — see [LICENSE](LICENSE).
