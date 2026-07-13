# Rust Directives

Rust-specific extras/overrides on top of the generic repo standard in
`~/.agents/flows/REPO.md` — read that first. Only Rust-specific content here.

## Packaging and Layout

- **Source Layout**: Cargo's default layout (`src/lib.rs`/`src/main.rs`, `tests/`,
  `examples/`, `benches/`) already satisfies REPO.md's layout rule — don't invent
  extra nesting Cargo doesn't ask for. Build backend is Cargo itself.
  Literal manifest: `~/.agents/references/Cargo.toml`.
- **MSRV**: `rust-version` in `Cargo.toml` (the `requires-python` equivalent) —
  cargo *refuses* to build below it, unlike npm's warn-only `engines.node`.
- **Optional Dependencies**: zero *required* runtime deps where feasible. The
  mechanism is Cargo **features** (`[features]` + `dep:crate`), not runtime import
  fallbacks. Set `[package.metadata.docs.rs] all-features = true` so docs.rs
  documents every feature.
- **README badges** (fill the template's badge row): version
  `img.shields.io/crates/v/<project_name>.svg` → crates.io page; docs
  `img.shields.io/docsrs/<project_name>` → `docs.rs/<project_name>`.

## Testing and Development

- **Tests**: `cargo test --all-features` covers unit (`#[cfg(test)]`) and integration
  (`tests/`) — no separate framework choice.
- **Lint/format in CI by default** (unlike Python/Node): `cargo fmt --check` and
  `cargo clippy --all-features -- -D warnings` — official, cheap, ecosystem-standard.
- **`.gitignore` language block** (source for the template placeholder): `/target/`,
  plus `Cargo.lock` for *libraries only* (binaries commit their lockfile).

## CI/CD: Implementing the Two-Workflow Split

Templates: `~/.agents/references/workflows/rust/{test,release}.yaml`.
- Toolchain via `dtolnay/rust-toolchain` (not unmaintained `actions-rs`) +
  `Swatinem/rust-cache`. Matrix over OS × {`stable`, declared MSRV} — both must pass.
- **Publish**: `cargo publish --token ${{ secrets.CARGO_REGISTRY_TOKEN }}` baseline;
  crates.io is rolling out OIDC Trusted Publishing — verify the current setup at
  release time.
- Changelog scraper: the identical Python inline step from the python template
  (rationale in REPO.md) — never reimplement per language.

## Releases and Versioning

- `Cargo.toml` `version` is plain SemVer — no PEP 440-style mismatch. No leading `v`
  in the manifest; leading `v` on the git tag. Bump in the same commit as the
  changelog entry.

## Documentation Site

- **API reference is nearly free**: crates.io publish auto-builds docs on **docs.rs**
  — no docs workflow job needed for that. Add Pages docs jobs (mirroring the python
  template) only for a narrative guide beyond API docs (e.g. mdBook); the landing
  page stays hand-written (REPO.md rule).
