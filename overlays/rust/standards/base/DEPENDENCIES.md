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
| Settings a file, a person or a peer fills, or values crossing a wire: one description for parsing, validation, defaults and docs | `guatiao` with `derive` (`ToValue`, `FromValue`, `Schema`), read through `guatiao-serde`'s `text::{json,toml,yaml}` ([CONFIG.md](CONFIG.md), "Settings as data") | `#[serde]` attributes beside them, a hand-written JSON Schema, the struct declared again on the other side |
| A form for settings | `guatiao-intake`: `form_for`, `#[derive(Form)]` for sections and widgets | a form laid out by hand from the field list |
| An extension point (handlers, providers, plugins) and loading its extensions | `guatiao` kinds, with `load` and a `guatiao-source-*` per language ([PLUGINS.md](../specialized/PLUGINS.md)) | `libloading` plus a hand-written entry symbol and registry; a Rust trait object across a library boundary |
| A channel of whole messages over a stream, its framing, or a stream adapter (memory, stdio, TCP) | `coqui-transport`: `Channel`, `StreamChannel::builder(codec)`, `frame::LengthPrefix`, `Split`, `Duplex`, modules `memory`, `stdio`, `tcp` | a channel, a length prefix or a reader thread written per transport crate |
| A command line | `clap` with the derive API ([APPLICATION.md](../specialized/APPLICATION.md)) | a hand-written parser over `std::env::args`, `argh`, `pico-args` |
| Serialisation | `serde` | hand-written visitors over a format crate's tree |
| JSON, TOML (keeping a user's file and comments: `toml_edit`), YAML | `serde_json`, `toml`, `serde-saphyr` | `serde_yaml` (unmaintained) |
| Error boilerplate | `thiserror`, where the crate takes dependencies | `anyhow` in a library signature |
| Diagnostics | `tracing`; `tracing-subscriber` in the binary only | `println!`/`eprintln!` gated by a variable; `env_logger` in a library |
| Async runtime and server | `tokio` (only the features used), `axum` | a second runtime in the same binary |
| Blocking HTTP client | `ureq` | an async client wrapped in `block_on` |
| TLS | `rustls` with the `ring` provider | `openssl` where `rustls` will do |
| Hashes, password hashing | the RustCrypto crates: `sha2`, `argon2` | a hand-written primitive, ever |
| Constant-time comparison | `subtle` (`ConstantTimeEq`); it answers at once for slices of unequal length, so where the length is secret compare fixed-length digests of both sides | a hand-written `constant_time_eq` |
| Dates, times, RFC 3339 | `time` (`OffsetDateTime`, `format_description::well_known::Rfc3339`, features `parsing`, `formatting`): one date crate per build, and it is already in each of ours (**house**) | civil-date arithmetic by hand; `jiff` beside it |
| Base64 | `base64` (`engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD}`) | a hand encoder over a 64-character table |
| Hex | `data-encoding` (`HEXLOWER`, `HEXLOWER_PERMISSIVE`), already beneath `coqui-peer` | a `hex(bytes)` helper per crate |
| IP networks, CIDR | `coqui-net-acl` (`IpNet`: `FromStr`, `contains`, IPv4-mapped addresses) | parsing and masking prefixes by hand |
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
