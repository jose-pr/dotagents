# Standard: public API

Base standard: applies to every Python library. Index: [README.md](../README.md).

## Names

- **Distribution, import package and repository share one name**: lowercase,
  short, underscores only where unavoidable. The console script, if any, has
  the same name. (**house**; PyPA treats matching names as convention only.)
- **Uppercase every acronym in a CapWords name**: `DHCPServer`, `TFTPError`,
  `UDPEndpoint`, `FQDN`, `URIList`. Never `Dhcp`, `Tftp`, `Udp`, `Fqdn`.
  (PEP 8: "HTTPServerError is better than HttpServerError".)
  - A mixed-case token that is not an acronym keeps its spelling:
    `IPv4Address`, `PktInfo`.
  - Two acronyms may touch (`HTTPSConnection`). When three would, reword.
  - One word stays one word: `Opcode`, not `OpCode`.
- **Functions and methods are snake_case; constants and enum members are
  UPPER_CASE.** (PEP 8.)
- **A public name says what it is when imported bare beside another
  library.** A generic noun takes the package's subject as a prefix: `Client`,
  `Server`, `Request`, `Response`, `Error`, `Message`, `Options`, `Handler`,
  `Context`, `Opcode`, `ErrorCode`, `Flags`. A term that already belongs to
  the subject (`Lease`, `OptionAck`, `TransferResult`) needs none.
  (**house**; the stdlib does this in every protocol module: `HTTPServer`,
  `SMTPException`, `TCPServer`.)
- **No non-exception class is named `Error` or `Exception`**, and no class
  takes the name of a builtin with a different meaning.

## Public surface

- **Every public module declares `__all__`.** A name outside it is not API.
  (PEP 8. In a `py.typed` package the typing specification makes this binding:
  an imported symbol is private unless `__all__` lists it.)
- **A private module starts with an underscore.** A module without one
  promises its import path will not move. (PEP 8.)
- **The root re-exports the same objects**, never a wrapper or a subclass made
  for naming. Each public name has one home, stated in the docs.
- **The root holds what the common task needs**: the main classes, the main
  data type, enums a caller must pass, the exceptions, one-shot functions.
  Large families of specialised classes stay in their topic module.
- **Internal code imports a name from the module that owns it**, never from a
  re-exporting `__init__`, so a split cannot create an import cycle.
- **`__version__` at the root comes from installed metadata**
  (`importlib.metadata.version`), never a restated literal.
- **Signatures are honest.** No `# type: ignore` to hide two methods sharing a
  name with different return types.

### Surface shape

Pick one when the library is created. (**house**)

- **Role-based**: the root plus a few role and topic modules are public
  (`<pkg>.client`, `<pkg>.server`, `<pkg>.exceptions`); everything else is
  private. For a library with distinct roles and many types. (stdlib `http`,
  paramiko, websockets.)
- **Root-only**: every module is private and the root `__all__` is the whole
  API. For a toolkit of functions and value types. (httpx, h11.)

### Module size

A module stays small enough to review in one sitting, a few hundred lines.
Split by responsibility into a package and keep its `__init__` re-exporting
the names callers already import, so a split never moves a public path. No
`utils.py`, `base.py` or `manager.py`: name a module for what it owns.

## Exceptions

- **One module, `exceptions.py`.** (requests, urllib3, httpx, websockets.)
- **One base class per package**, named for the subject and ending in `Error`:
  `DHCPError`. Category classes under it end in `Error` too. (PEP 8.)
- **Also inherit from the builtin a caller would already catch.** Malformed
  input is `(Base, ValueError)`; a timeout is `(Base, TimeoutError)`; a
  failure carrying an errno is an `OSError` subclass. (stdlib:
  `JSONDecodeError(ValueError)`, `SMTPException(OSError)`.)
- **A caller's mistake is not a package error.** A bad argument raises plain
  `ValueError` or `TypeError`. The base is for what went wrong at run time,
  and the two are distinguishable by type.
- **A leaf class may use the subject's own term** for a defined status or
  error code: `FileNotFound` under `RemoteError`. (stdlib `BadStatusLine`.)
- **Let `OSError` through** when it already describes the failure.
- On a floor below 3.11, `asyncio.TimeoutError` and `socket.timeout` are not
  the builtin `TimeoutError`. Translate them at the boundary.

## Synchronous and asynchronous

- **A class with a lifecycle has a twin, `X` and `AsyncX`**, exported from the
  same modules. `Async` comes first: `AsyncDHCPServer`. No public `aio`,
  `asyncio` or `sync` namespace holding a second class of the same name.
  (httpx, psycopg, pymongo, elasticsearch.)
- **The twins are siblings, never parent and child.** A method returning a
  value and one returning an awaitable are different contracts. Share through
  a private base holding configuration and pure logic, through composition,
  or through a common core.
- **Same method names on both.** A method is a coroutine when it waits on I/O
  and plain when it does not. No `a` prefix on `AsyncX` methods, except the
  closer, `aclose()`.
- **One object used from both worlds stays one class** and pairs each blocking
  method with an `a`-prefixed coroutine: `recv()` and `arecv()`. For
  endpoints, streams and responses, not for roles. (httpx `Response`.)
- **No flag that changes whether a method is awaited, and no return type of
  `Union[T, Awaitable[T]]`.**
- **Say what "async" covers.** If a method runs blocking code in an executor,
  or receiving is async while policy runs on a worker thread, the docstring
  says so.
- **A library never touches the caller's event loop or the loop policy**, and
  never calls `asyncio.run()`. Where both twins exist, the sync one is not a
  private loop wrapped around the async one.
- **A sync library that must drive an async-only dependency** uses one shared
  loop on a dedicated daemon thread, submits to it with
  `run_coroutine_threadsafe`, and documents that it does. (asyncua's sync
  API.)
- Every background task has an owner that awaits it and sees its exception.
  Below 3.11 there is no `TaskGroup` or `asyncio.timeout()`.

## Resources and constructors

- **A constructor validates and stores; it performs no I/O.**
- **Options after the first one or two are keyword-only.** Durations are
  seconds as `float`: `timeout` for one attempt, `deadline` for a whole
  operation.
- **Anything holding a resource is a context manager**: `with` on the sync
  class, `async with` on the async one.
- **`close()` on a sync class, `aclose()` on an async class.** Each is
  complete when it returns and harmless to call twice. An async class has no
  `async def close()`: an unawaited one does nothing and says nothing.
  (httpx, anyio, trio; stdlib `contextlib.aclosing`.)
- **State who closes what.** A stream or socket the caller passed in stays
  the caller's unless the docstring says ownership transfers.

## Process-wide state

A library changes nothing outside its own objects, on import or on start:

- no signal handlers; the application or the command line installs them;
- no logging configuration; get `logging.getLogger(__name__)` and emit;
- no patching of stdlib modules on import;
- no change to `sys.path`, the working directory or the event-loop policy;
- no `print()` and no `sys.exit()`; a library returns, raises or logs.

A library whose purpose is to patch something does it through an explicit
call that can be undone, never as a side effect of import.

## Convenience

- **A one-shot module-level function for each common stateless task**, beside
  the class that gives full control. (`httpx.get`, `requests.get`.)
