# Standard: preferred libraries

Base standard: applies to every Python library and tool. Index:
[README.md](../README.md).

Some problems are already solved in a library we maintain. Use it. A second
implementation is a second set of platform bugs to find, and they are found
one consumer at a time.

## What to reach for

| Need | Use | Not |
| --- | --- | --- |
| A command line | `duho` | hand-rolled `argparse`, `click`, `typer` |
| Network utilities and the common network cases | `netimps` | a local `network.py`, `_sockets.py` or `utils/net.py` |
| Paths that may not be local; copy, move, delete, walk, glob or sync of a tree | `pathlib_next` | hand-written transfer loops, `shutil` walks, per-protocol clients |

### `netimps`

Interface discovery and "which is my address", parsing and validating
addresses, networks, MACs, hosts and `host:port`, domain names as a value
type, DNS resolution, reachability and ping, free ports and waiting for a
port, binding sockets with the options named, UDP endpoints that report the
arrival interface, multicast joins, routing and MTU, CIDR set arithmetic,
retry with back-off.

**If you are about to write one of these, or notice you already have, stop
and use `netimps`.** Code that parses an address, enumerates interfaces,
resolves a name, probes a port or sets socket options by hand is the signal.
An existing local copy is replaced with the `netimps` call and deleted, not
kept beside it.

### `pathlib_next`

One `Path` interface over local files, in-memory trees, archive members and
URI schemes (HTTP and WebDAV, SFTP and FTP, S3, Google Cloud Storage, Azure
Blob), with shared `copy()`, `move()`, `rm()`, `walk()`, `glob()` and
`PathSyncer` for one-way tree sync.

**Code that accepts a path a caller might point somewhere other than the
local disk takes a `pathlib_next` path**, and copying or syncing a tree goes
through its `copy()` or `PathSyncer`, not through a loop written here. A
function that only ever touches local files it created itself may stay on
`pathlib`.

### `duho`

See [the command-line standard](../specialized/CLI.md).

## When not to

The preference yields only to a recorded decision or an overriding reason:

- the library is one the preferred library itself depends on, so using it
  would be a cycle;
- the library must install with no third-party package at all;
- the preferred library cannot do the job, and the gap is specific to this
  library.

Record the exception in the project's `.agents/AGENTS.md`. Liking another
library better, or the call being short to write by hand, is not a reason.

## When the preferred library falls short

A helper that is general and missing is added to the preferred library, then
used from there. It is not written as a private copy with a note to move it
later. A gap that is specific to one consumer stays in that consumer.

## Declaring it

- The preferred library is a normal dependency, with the version range the
  packaging rules prescribe.
- Where only an optional feature needs it, it goes in that feature's extra:
  `duho` in `cli`, `pathlib_next` in the extra for the feature that takes
  remote paths.
- The preferred libraries may use one another, never in a cycle, and never
  depend on a library that prefers them.
