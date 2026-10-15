# Standard: protocol crates

Specialized standard: applies to a crate that speaks a wire protocol: a
client, a server, a codec, a discovery protocol. Adds to the base standards;
index: [README.md](../README.md).

## A crate per protocol

- **A protocol is a crate of its own that knows nothing of the application**:
  no application types, no configuration, no provider kinds. The application
  adapts it; it does not adapt to the application. (**house**)
- It carries the project's prefix while the project publishes nothing else
  under that protocol's name (`<project>-rtsp`); the README says it stands
  alone.

## Layout and roles

```text
src/
  lib.rs        re-exports, the error type
  message.rs    wire types: one unit on the wire, its enums, encode and decode
  client.rs     Client: initiates exchanges
  server.rs     Server: owns a listener and its lifecycle
```

- `rtsp::Client`, `rtsp::Server`, `rtsp::Message`, `rtsp::Error`: the module
  carries the protocol's name, the type carries the role
  ([API.md](../base/API.md), "Names"). Implement only the roles the
  protocol has.

## The protocol core

- **Protocol rules live in code that does no I/O** where the protocol allows:
  parsing, encoding, state transitions, retransmission arithmetic, error
  mapping. Input is a call (`handle(&[u8], now)`); output is data (the bytes to
  send, the next deadline). (h2, quinn-proto, rustls.)
- **The core reads no clock.** Time arrives as an argument.
- **Drivers own the rest**: sockets, threads or tasks, timers. A blocking
  driver and an async one share the core; neither re-implements it.
- A crate small enough that a sans-I/O split buys nothing may skip it, and
  says so in its module docs.

## Wire encoding

- **`Message::decode(&[u8]) -> Result<Message, Error>` and
  `message.encode() -> Vec<u8>`** (or `encode_into(&mut impl BufMut)` on a hot
  path). (**house**)
- **Decode is liberal, encode is strict**: accept what real peers send, emit
  what the specification says.
- **A value on the wire is never rewritten.** An unknown enum value becomes a
  variant carrying the number or the text, so a relay forwards what it
  received.
- **A decode failure is the crate's error**, never a panic, an index out of
  bounds or an `unwrap` on peer data.
- **Every codec has a vector from its specification in the tests**, and a
  property test feeding arbitrary bytes to `decode`.

## Time and bounds

- **Every network operation has a finite default timeout**, for connecting and
  for reading; "wait forever" is a hang waiting for a caller who forgot.
- **Anything an unauthenticated peer controls has a bound**: buffered bytes,
  pending exchanges, sessions, nesting, header count. The docs say what
  happens at the bound.
- **A warning on such a path is rate-limited.**
- UDP gives no back-pressure; bounds belong in admission.

## Trust

- **TLS verification is on**; turning it off is an explicit argument on one
  client, never global, and is logged each time such a client is built.
- Credentials never reach a log, an error or a `Debug`
  ([CONFIG.md](../base/CONFIG.md)).

## Verification

- **Test against a real peer**: a stock client or server for the protocol,
  or recorded traffic from one, not only the crate against itself.
- Tests that need a device or a service on the network are
  `#[ignore = "<reason>"]` and enabled by `<PROJECT>_TEST_<NAME>`
  ([CONFIG.md](../base/CONFIG.md)).
