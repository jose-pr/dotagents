# Node/TypeScript Directives

Node/TypeScript-specific extras/overrides on top of the generic repo standard in
`~/.agents/flows/REPO.md` — read that first. Only Node-specific content here.

## Packaging and Layout

- **Source Layout**: `src/` (TypeScript) compiled to `dist/` (gitignored, published);
  `main`/`types`/`exports` all point into `dist/`, never `src/`. Literal manifest:
  `~/.agents/references/package.json`.
- **Build**: `tsc` alone for a simple single-entry library; a bundler (`tsup`/
  `esbuild`) only when dual ESM+CJS or multiple entry points are actually needed.
- **Node Version**: floor via `engines.node` (e.g. `">=18"`) — Python's
  `requires-python` equivalent, but npm only warns on mismatch.
- **Testing framework**: `vitest` (Jest-compatible, native ESM/TS, no extra config);
  Jest only with a specific reason.
- **Package manager**: npm baseline (portable, preinstalled on CI). pnpm/yarn fine if
  the project already uses one — one lockfile, consistently.
- **Optional Dependencies**: zero *required* runtime deps where feasible. No stdlib
  try/except-extras equivalent — an optional integration is a `peerDependency` with a
  try/catch-guarded dynamic `import`, or a separate sub-package.
- **README badges** (fill the template's badge row): version
  `img.shields.io/npm/v/<project_name>.svg` → npm package page; engines
  `img.shields.io/node/v/<project_name>.svg`.

## Testing and Development

- **Install**: `npm ci` (not `npm install`) in CI and clean setups — lockfile-exact,
  fails instead of silently updating.
- **Typecheck in CI by default**: `tsc --noEmit` — a broken build differs from a
  style opinion. ESLint/Prettier stay opt-in (same spirit as Python's no-ruff/mypy).
- **`.gitignore` language block** (source for the template placeholder):
  `node_modules/`, `dist/`, `*.tsbuildinfo`, `coverage/`.

## CI/CD: Implementing the Two-Workflow Split

Templates: `~/.agents/references/workflows/node/{test,release}.yaml`.
- Matrix over Node LTS versions (plus OS edges) the same way Python matrices over
  Python versions — oldest supported must pass.
- **Publish**: `npm publish --provenance --access public` (OIDC provenance,
  `id-token: write`, npm ≥9.5). npm is rolling out token-free Trusted Publishing —
  verify the current setup on npmjs.com at release time before assuming the
  `NODE_AUTH_TOKEN` fallback is still needed.
- Changelog scraper: the identical Python inline step from the python template
  (rationale in REPO.md) — never reimplement per language.

## Releases and Versioning

- `package.json` `version` is plain SemVer — no PEP 440-style mismatch. No leading
  `v` in the manifest (`1.0.0`); leading `v` on the git tag (`v1.0.0`). Bump in the
  same commit as the changelog entry.

## Documentation Site

- **TypeDoc** generates the API reference (the mkdocstrings analogue). Output to
  `docs/api/` — never point it *at* `docs/`, it would clobber the hand-written
  landing page (REPO.md rule; see `references/docs-index.md`).
