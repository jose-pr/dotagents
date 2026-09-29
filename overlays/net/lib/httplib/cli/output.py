"""Output options: where the body and headers go, how loud the fallback is,
and what an HTTP error status does (``-f`` / ``--fail-with-body``)."""
import contextlib
import os
import sys
import urllib.parse
from typing import Optional

from ._duho import NS, Arg
from .args import Group
from .errors import EXIT_WRITE, LocalError


def output_path(name):
    """The file to open for ``name``: ``/dev/null`` is the null device on
    every platform -- Windows has no such path (its name is ``NUL``), and
    ``-o /dev/null -w '%{http_code}'`` is the usual status probe."""
    return os.devnull if name == '/dev/null' else name


class OutputArgs(Group):
    """Body and header destinations, verbosity, and failure on HTTP errors."""

    #: The -o / -O file once resolved (``output_target``).
    body_target = None

    output: Optional[str] = None
    "Write the body to this file (- for stdout)"
    ("-o", "--output")

    remote_name: bool = False
    "Write the body to a file named like the URL's last path segment"
    ("-O", "--remote-name")

    remote_name_all: bool = False
    "-O for every URL (there is one here)"
    ("--remote-name-all",)

    output_dir: Arg[Optional[str], NS(metavar='DIR')] = None
    "Directory for -o / -O files"
    ("--output-dir",)

    create_dirs: bool = False
    "Create the output file's missing directories"
    ("--create-dirs",)

    dump_header: Arg[Optional[str], NS(metavar='FILE')] = None
    "Write response headers to file (or - for stdout)"
    ("-D", "--dump-header")

    include: bool = False
    "Include response headers in output"
    ("-i", "--include", "--show-headers")

    head: bool = False
    "Fetch headers only"
    ("-I", "--head")

    silent: bool = False
    "Silent mode"
    ("-s", "--silent")

    show_error: bool = False
    "Show errors even when silent"
    ("-S", "--show-error")

    verbose: bool = False
    "Verbose mode"
    ("-v", "--verbose")

    fail: bool = False
    "Fail silently (exit 22, no body) on HTTP errors"
    ("-f", "--fail")

    fail_with_body: bool = False
    "Exit 22 on HTTP errors, still writing the body"
    ("--fail-with-body",)

    # No-ops: the fallback prints no progress meter and writes the body in one go.
    no_buffer: bool = False
    "Disable output buffering (a no-op)"
    ("-N", "--no-buffer")

    progress_bar: bool = False
    "Progress bar (a no-op: no progress is shown)"
    ("-#", "--progress-bar")

    no_progress_meter: bool = False
    "No progress meter (a no-op: none is shown)"
    ("--no-progress-meter",)

    styled_output: bool = False
    "Styled header output (a no-op: none is styled)"
    ("--styled-output",)

    stderr: Arg[Optional[str], NS(metavar='FILE')] = None
    "Write what goes to stderr to FILE instead (- for stdout)"
    ("--stderr",)

    @contextlib.contextmanager
    def stderr_redirected(self):
        """``--stderr``: stderr goes to the file (or stdout) for the run."""
        if not self.stderr:
            yield
            return
        target = sys.stdout if self.stderr == '-' else open(self.stderr, 'w', encoding='utf-8')
        saved, sys.stderr = sys.stderr, target
        try:
            yield
        finally:
            sys.stderr = saved
            if target is not sys.stdout:
                target.close()

    def _check(self):
        if self.fail and self.fail_with_body:
            raise ValueError('You must select either --fail or --fail-with-body, not both.')

    def fails_on(self, status):
        """Does ``status`` end the transfer as curl's exit 22?"""
        return (self.fail or self.fail_with_body) and status >= 400

    def output_target(self, url):
        """Resolve and remember where the body goes (``body_target``): -o's
        file, or -O's (the URL's last path segment), under --output-dir;
        ``None`` for stdout (``-o -`` too). --create-dirs makes the missing
        directories. A URL with no file name for -O is curl's exit 23."""
        target = self.output
        if target is None and (self.remote_name or self.remote_name_all):
            target = urllib.parse.urlsplit(url).path.rsplit('/', 1)[-1]
            if not target:
                raise LocalError(EXIT_WRITE, 'Remote file name has no length')
        if target is None or target == '-':
            self.body_target = None
            return None
        if self.output_dir and target != '/dev/null' and not os.path.isabs(target):
            target = os.path.join(self.output_dir, target)
        if self.create_dirs and target != '/dev/null' and os.path.dirname(target):
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
            except OSError as exc:
                raise LocalError(EXIT_WRITE, 'Failed to create the directory for %s: %s' % (target, exc))
        self.body_target = target
        return target

    def should_print_error(self):
        return (not self.silent) or self.show_error

    def write_header_dump(self, header_bytes):
        if not self.dump_header:
            return
        if self.dump_header == '-':
            sys.stdout.buffer.write(header_bytes)
            sys.stdout.buffer.flush()
            return
        with open(output_path(self.dump_header), 'wb') as handle:
            handle.write(header_bytes)

    def emit_output(self, header_bytes, content):
        if self.body_target:
            # 'ab': a 206 answering a resume (-C) continues the file.
            mode = 'ab' if getattr(self, 'append_output', False) else 'wb'
            with open(output_path(self.body_target), mode) as handle:
                if self.include or self.head:
                    handle.write(header_bytes)
                handle.write(content)
            if not self.silent:
                print('Output written to %s' % self.body_target, file=sys.stderr)
            return
        if self.include or self.head:
            sys.stdout.buffer.write(header_bytes)
        if not self.head:
            sys.stdout.buffer.write(content)
        sys.stdout.buffer.flush()
