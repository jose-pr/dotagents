# Standard: dependencies and preferred crates

Base standard: applies to every crate. Index: [README.md](../README.md).
Version ranges, the lockfile and the MSRV are in `../../kb/RUST.md`.

Some problems are already solved, by a crate we maintain or by one the
ecosystem trusts. Use it. A second implementation is a second set of bugs to
find, and they are found one consumer at a time.

## What to reach for

| Need | Use | Not |
| --- | --- | --- |
| Values, a schema, or providers crossing a language or library boundary | `guatiao` (and `guatiao-serde`, `guatiao-intake`) | a hand-written C struct tree, a JSON string across the boundary, raw `libloading` |
| Loading plugins from shared libraries | `guatiao` with `load` | `libloading` plus a hand-written entry symbol and registry |
| A command line | `clap` with the derive API ([APPLICATION.md](../specialized/APPLICATION.md)) | a hand-written parser over `std::env::args`, `argh`, `pico-args` |
| Serialisation | `serde` | hand-written visitors over a format crate's tree |
| JSON, TOML (keeping a user's file and comments: `toml_edit`), YAML | `serde_json`, `toml`, `serde-saphyr` | `serde_yaml` (unmaintained) |
| Error boilerplate | `thiserror`, where the crate takes dependencies | `anyhow` in a library signature |
| Diagnostics | `tracing`; `tracing-subscriber` in the binary only | `println!`/`eprintln!` gated by a variable; `env_logger` in a library |
| Async runtime and server | `tokio` (only the features used), `axum` | a second runtime in the same binary |
| Blocking HTTP client | `ureq` | an async client wrapped in `block_on` |
| TLS | `rustls` with the `ring` provider | `openssl` where `rustls` will do |
| Hashes, password hashing, constant-time comparison | the RustCrypto crates: `sha2`, `argon2`, `subtle` | a hand-written primitive, ever |
| Sockets with options `std` lacks | `socket2` | `libc` calls written here |
| Reading an executable's headers | `object` | parsing PE, ELF or Mach-O by hand |

**If you are about to write one of these, or notice you already have, stop
and use the crate.** An existing local copy is replaced and deleted, not kept
beside it. A hash or a parser backing a trust decision is the thing to borrow
first, not the thing a zero-dependency goal excuses
(`../../kb/RUST.md`, "Don't hand-roll").

## Before adding one

- **Check whether it is already in the build**: `cargo tree -i <crate>` and
  the lockfile. A transitive dependency already compiled costs nothing to
  name.
- **Pick the crate a reputable maintainer group publishes** (rust-lang,
  dtolnay, tokio-rs, RustCrypto, rustls) over a newer one with the same API.
- **`default-features = false`**, then name the features used. A dependency's
  defaults are its author's guess about your build.
- **A pre-1.0 or `alpha` crate needs a reason** written beside it in the
  manifest.
- **Every dependency in a workspace is declared once**, in
  `[workspace.dependencies]`, with a comment saying what it is for; members
  take it with `<name>.workspace = true`. (**house**)

## When not to

The preference yields only to a recorded decision or an overriding reason:

- the crate is one the preferred crate depends on, so using it would be a
  cycle;
- the crate must build with no dependency at all, and the need is not a
  security primitive;
- the preferred crate cannot do the job, and the gap is specific to this
  crate.

Record the exception in the project's `.agents/AGENTS.md`. Liking another
crate better, or the code being short to write by hand, is not a reason.

## When the preferred crate falls short

A capability that is general and missing from a crate we maintain is added
there, then used from there: a request to the upstream project, not a
private copy with a note to move it later. A gap specific to one consumer
stays in that consumer.
