# Standard: preferred libraries

Base standard. Index: [README.md](../README.md).

Some problems are already solved in a library we maintain. Use it. A second
implementation is a second set of platform bugs, found one consumer at a time.

## What to reach for

| Need | Use | Not |
| --- | --- | --- |
| A command line | `duho` | hand-rolled `argparse`, `click`, `typer` |
| Network utilities and the common network cases | `netimps` | a local `network.py`, `_sockets.py` or `utils/net.py` |
| Paths that may not be local; copy, move, delete, walk, glob or sync of a tree | `pathapi`, and its scheme distributions for URIs | hand-written transfer loops, `shutil` walks, per-protocol clients |
| Configuration files | `anycfg` | per-format `json`/`yaml`/`tomllib` calls, a local merge or include |
| A TLS context: the OS verifier, client certificates from an OS keystore | `sslcontext` | a hand-built `ssl.SSLContext`, key files written to disk |
| Which proxy a request uses | `proxylib` | reading `*_PROXY` by hand, a local PAC evaluator |
| HTTP that follows the system's proxy, trust and credentials | `syshttp` | a hand-wired `urllib` opener or `requests` session |

**If you are about to write one of these, or notice you already have, stop
and use the library.** An existing local copy is replaced with the library
call and deleted, not kept beside it.

### `netimps`

Interface discovery and "which is my address", parsing and validating
addresses, networks, MACs, hosts and `host:port`, domain names as a value
type, DNS resolution, reachability and ping, free ports and waiting for a
port, binding sockets with the options named, UDP endpoints that report the
arrival interface, multicast joins, routing and MTU, CIDR set arithmetic,
retry with back-off. Code that parses an address, enumerates interfaces,
resolves a name, probes a port or sets socket options by hand is the signal.

### `pathapi`

One `Path` interface over local files, in-memory trees, archive members, FTP
and HTTP, with shared `copy()`, `move()`, `rm()`, `walk()`, `glob()` and
`pathapi.sync.PathSyncer` for one-way tree sync. A location written as a URI
goes through `pathapi-uri` (imported as `uripath`: `file:`, `http(s):`, `dav:`,
`ftp:`, `zip:`, `tar:`), and the other schemes are distributions of their own:
`sftppath` (`sftp:`), `objpath` (`s3:`, `gs:`, `az:`) and `repopath`
(`github:`, `gitlab:`, `repo:`). `pathlib_next` is the old name; it is no
longer maintained and no alias is kept.

**Code that accepts a path a caller might point somewhere other than the
local disk takes a `pathapi` path**, and copying or syncing a tree goes
through its `copy()` or `PathSyncer`. A function that only ever touches local
files it created itself may stay on `pathlib`.

### `anycfg`

`load(path)` and `dump(obj, path)` pick the format from the extension; JSON,
YAML, TOML, INI, dotenv and `.cfg` are built in, and more register as
plugins. A `.cfg` file is read in the format its first line names
(`#!<format>`), else as TOML, and is written as TOML. Layered loading with
merge strategies, confined `!include`, typed results (`into=`), and commands
and templates off by default.

### `sslcontext`

A plain `ssl.SSLContext` whose client certificate stays in the OS keystore
(Windows CNG, the macOS Keychain, a PKCS#11 token), PEM or PKCS#12 credentials
loaded from memory, and `system_context()`: the OS verifies the server, and a
client certificate is chosen as the browser would. PINs come from a value or
a callback, never a prompt.

### `proxylib`

The proxy for a URL, the same way on every OS: the proxy environment
variables when set, else the OS resolver (WinHTTP, CFNetwork, libproxy) and
the OS settings read as data. PAC scripts run as JavaScript with a time
limit; WPAD runs only when asked. The answer is wired into `requests`,
`urllib`, `httpx` and `aiohttp`, with failover along the list.

### `syshttp`

HTTP plumbing on `proxylib` and `sslcontext`: the system's proxy by default,
proxy credentials and prefix gateways, cookie and token jars, URL hooks, TLS
through the OS verifier, and an HTTP client on the standard library.

### `duho`

See [the command-line standard](../specialized/CLI.md).

## When not to

The preference yields only to a recorded exception
([README.md](../README.md), "Exceptions") for one of these reasons:

- the library is one the preferred library itself depends on, so using it
  would be a cycle;
- the library must install with no third-party package at all;
- the preferred library cannot do the job, and the gap is specific to this
  library.

Liking another library better, or the call being short to write by hand, is
not a reason.

## When the preferred library falls short

A helper that is general and missing is added to the preferred library, then
used from there, not written as a private copy with a note to move it later.
A gap specific to one consumer stays in that consumer.

## Declaring it

- A normal dependency, ranged as `../../kb/PYTHON.md` prescribes; `duho` is
  ranged at its latest series.
- Where only an optional feature needs it, it goes in that feature's extra:
  `duho` in `cli`, `pathapi-uri` and the scheme distributions in the extra for
  the feature that takes remote paths.
- The preferred libraries may use one another, never in a cycle, and never
  depend on a library that prefers them.
