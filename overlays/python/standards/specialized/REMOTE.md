# Standard: clients of a remote service

Specialized standard: applies to a library that calls a service over a
network — an HTTP or WebSocket API, SSH, a directory, a cloud store. Adds to
the base standards; index: [README.md](../README.md). A library that
implements the wire protocol itself follows [PROTOCOL.md](PROTOCOL.md).

## Time

- **Every network operation has a finite timeout by default**, for
  connecting and for reading. A default of "wait forever" is a hang waiting
  for a caller who forgot. (httpx, not requests.)
- `timeout` is one attempt; `deadline` is the whole operation including
  retries. Both are settable per client and per call.
- A timeout raises the package's timeout error, a `TimeoutError`.

## Trust

- **TLS verification is on.** Turning it off is an explicit argument on one
  client, never global, and is logged as a warning each time such a client is
  created.
- A private certificate authority is a parameter, not a reason to disable
  verification.
- Host keys are checked; "accept any" is opt-in and named for what it is.

## Credentials

- Accepted as arguments, from the documented environment variables, or from
  a file ([CONFIG.md](../base/CONFIG.md)).
- **Never in a log line, an error message, a `repr`, an exception attribute
  or a URL that is logged.**
- Never passed to a child process as an argument.
- A token that expires is refreshed by the client, once, before the failure
  reaches the caller.

## The client object

- **One client owns the connection or session**, is a context manager, and
  releases it in `close()` or `aclose()`.
- It states whether it may be shared between threads or tasks.
- Sync and async clients are twins, per the base API standard.
- The `User-Agent` or equivalent names the library and its version.

## Retries

- **Off unless the operation is safe to repeat.** Reads are; a write is only
  when the service makes it idempotent.
- Bounded attempts, exponential back-off with jitter, and the service's own
  retry hint honoured.
- **Never on an authentication, permission or validation failure.**

## Failures

- **The transport library's exceptions do not cross the API.** A caller
  catches this package's errors: could not connect (an `OSError`), timed
  out, not authenticated, not permitted, not found, rejected as invalid, the
  service failed.
- Each carries what the service reported — a status, a code, a request
  identifier — and none of the request's secrets.

## Reading and sending

- **Liberal on what is read**: an unknown field in a response is ignored, an
  unknown enum value is preserved.
- **Strict on what is sent**: validated before it leaves.
- Anything listed is an iterator that fetches pages as it goes, with a limit
  the caller can set.
- The supported range of service versions is stated, and an unsupported one
  is reported as that.

## Tests

- **The default test run needs no network.** It runs against a local fake
  server or recorded exchanges.
- Tests against a real service are separate, skipped visibly without their
  credentials, and never run against production.
- Covered: timeout, refused connection, expired credentials, a retried read,
  a write that must not be retried.
