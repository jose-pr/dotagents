# Standard: protocol libraries

Specialized standard: applies to a library that speaks a wire protocol. Adds
to [the base API standard](../base/API.md); index: [README.md](../README.md).

## Roles

`<P>` is the protocol acronym in capitals.

| Responsibility | Name |
| --- | --- |
| Initiates exchanges | `<P>Client`, `Async<P>Client` |
| Owns a service and its lifecycle | `<P>Server`, `Async<P>Server` |
| Forwards between client and server | `<P>Relay`, `Async<P>Relay` |
| Receives and decodes, no policy | `<P>Listener`, `Async<P>Listener` |
| Observes traffic | `<P>Capture`, `Async<P>Capture` |
| One unit on the wire | `<P>Message`, or `<Kind>Packet` per kind |
| Wire discriminators | `<P>Opcode`, `<P>MessageType`, `<P>ErrorCode` |
| Application contract | `<P>Handler`, or a domain name such as `LeaseBackend` |
| Per-request metadata | `<P>RequestContext` |

Implement only the roles the protocol has. A UDP exchange is not a
`Connection`. No class is named for the protocol alone.

## Layout

```text
src/<pkg>/
  __init__.py        curated re-exports, __all__, __version__
  exceptions.py
  client.py          <P>Client, Async<P>Client
  server.py          <P>Server, Async<P>Server
  packet.py          wire types, enums, encode and decode
```

A role that outgrows one file becomes a package at the same import path:

```text
  server/
    __init__.py      the public names
    _core.py         policy and replies: no sockets, threads or loop
    _sync.py         <P>Server
    _asyncio.py      Async<P>Server
```

Drivers import the core. The core imports no driver and no command line.

## Server and relay lifecycle

One vocabulary for both twins. (**house**, assembled from `socketserver` and
`asyncio.Server`.)

| Method | Sync | Async |
| --- | --- | --- |
| `bind()` | Open and bind sockets. Idempotent. Never a coroutine. | same |
| `start()` | `bind()`, serve on a background thread, return once serving. | Coroutine; serves on a background task. |
| `serve_forever()` | `bind()`, serve on the calling thread until shut down. | Coroutine; serves in the caller's task. |
| `shutdown()` | Ask serving to stop. Returns at once. Safe from any thread and from a handler. | Same; never a coroutine. |
| `wait_closed()` | Block until serving has stopped and owned work is done. Optional `timeout`. | Coroutine, same meaning. |
| `close()` / `aclose()` | `shutdown()`, wait, release every socket. Final. | `await aclose()`, same. |
| `with` / `async with` | Enter: `bind()`. Exit: `close()`. | Enter: `bind()`. Exit: `aclose()`. |

- **`shutdown()` never blocks.** Unlike `socketserver.BaseServer.shutdown`,
  which deadlocks when called from the serving thread. A handler that wants
  the server down calls `shutdown()`, never `close()`.
- **`with` guarantees cleanup; it does not start serving.** The body calls
  `serve_forever()` or `start()`. (stdlib.)
- **`bind()` is public**, so a caller can take a privileged port and then
  drop privileges, and a test can bind port 0 and read the address back.
- **A start-up failure reaches the caller.** `start()` raises what `bind()`
  raised and never returns with the server not running.
- **Closed is final.** `start()` or `serve_forever()` after `close()` raises.
- **No `stop()`, `wait()` or `listen()`.**
- State whether shutdown drains in-flight exchanges or aborts them.

A client that keeps no resource between operations has no lifecycle methods.

## The protocol core

- **Protocol rules live in code that performs no I/O**: packet layout, option
  negotiation, retransmission and back-off arithmetic, state transitions,
  error mapping. No sockets, threads, loops or sleeps. (h11, h2, wsproto.)
- **The core never reads a clock.** Time arrives as a `now` argument, and the
  core exposes when it next wants to be called (`deadline`, or
  `get_timer()`). (aioquic.)
- **Input is a call, output is data.** The core returns the datagrams to send,
  or writes them through a `send` callable it was handed where that avoids a
  copy on a hot path. It never blocks.
- **Drivers own the rest**: sockets, the receive loop, threads or tasks,
  timers, and the decision to offload a slow backend.
- Where the core is mostly storage or allocation policy it need not be pure.
  It must be separable from the receive loop, so both drivers call it.

## Wire encoding

- **`decode()` is liberal, `encode()` is strict.** Decode accepts what real
  peers send; encode emits what the RFC specifies.
- **`<P>Message.decode(data)` is a classmethod; `message.encode()` returns
  `bytes`; `__bytes__` delegates to it.** Module-level `encode_*` functions
  are allowed where a hot path must skip object construction.
- **A value on the wire is never rewritten.** An unknown enum value becomes
  an unnamed member carrying the number, so a relay forwards what it
  received.
- **A decode failure raises the package's decode error**, never a bare
  `ValueError`, `IndexError` or `struct.error`.
- **Every codec has a wire vector from its RFC in the tests.**

## Handlers and policy

- **Application behaviour is injected as an object** whose contract is a
  `typing.Protocol`: storage, routing, file access, allocation.
- **Where subclassing is the extension point, hooks are named
  `handle_<message kind>`** and documented as a contract. (stdlib `do_GET`.)
- **One contract per hook**: synchronous or a coroutine, not either.
- **Document where each hook runs**: which thread or task, whether calls are
  serialised, whether it may block, what cancellation does.
- An async server running synchronous policy on a worker thread is valid. It
  is documented as that, and a hook is not `async def` until dispatch awaits
  it.

## Input from the network

- **Anything an unauthenticated peer controls has a bound**: stored state,
  pending exchanges, sessions, queue depth, files created. State what happens
  at the bound.
- **A warning on such a path is rate-limited.**
- UDP gives no back-pressure; bounds belong in admission and in the window.

## Verification

- **Test against a real peer**: a stock client or server for the protocol,
  over real sockets, not only the library against itself.
- **Run the suite and the type checker on Linux and Windows** when a change
  touches sockets, paths or anything platform-conditional.
