# Line endings

Every text file is LF, in every repository, on every platform.

A repository has one line ending. A tool that writes LF into a CRLF file
leaves it **mixed**, and mixed is the state that costs time: it defeats diffs,
makes byte-for-byte fixtures fail for reasons unrelated to their content, and
hides real changes inside whitespace churn.

## What makes it hold

- `.gitattributes` with `* text=auto eol=lf`, created with the repository
  (`$ENGINEERING_OVERLAY_ROOT/references/.gitattributes`).
- `git config --global core.autocrlf false`.

## Two exceptions, both written in `.gitattributes`

| Kind | Mark | Test |
| --- | --- | --- |
| A file the platform requires to be CRLF: some `.bat` and `.cmd` files, some signed manifests | `*.bat text eol=crlf` | the platform refuses it otherwise |
| Recorded evidence: a captured response saved as a fixture, a wire payload, a golden file copied from a third party | `tests/fixtures/** -text`, with a comment saying why | **who chose the bytes?** If someone else did and we are recording it, they are a measurement |

Normalising recorded evidence does not break a test. It makes the test assert
against bytes the source never sent, while it keeps passing.

## Checking and converting

```bash
dotagents eol check [path ...]            # exit 1 lists every file that is wrong
dotagents eol check --control [path ...]  # also stray control bytes
dotagents eol fix [path ...] [--dry-run]
```

Over MCP: `dotagents.eol.check`, `dotagents.eol.fix`.

The command reads the repository's attributes, so `eol=crlf` files are
expected to be CRLF and `-text` files are left alone. It counts bytes, reads
all of a file before writing any of it, and replaces the file by rename.

**Use it instead of a one-liner.** The obvious ones are wrong:

- `open(p, "wb").write(open(p, "rb").read().replace(...))` opens the file for
  writing, which empties it, before it reads it.
- `grep` for a carriage return can report every line of an LF file as CRLF,
  depending on the shell's build.

The measurement that is always right:

```python
data = path.read_bytes()
crlf, lf = data.count(b"\r\n"), data.count(b"\n")
# crlf == 0 -> LF      crlf == lf -> CRLF      otherwise -> MIXED
```

`git ls-files --eol` answers a different question — the index, the working
tree and the attributes, for tracked files only — and complements it.

## Writing files from a script

On Windows, text mode turns `\n` into `\r\n`.

```python
with open(path, "w", encoding="utf-8", newline="") as handle:   # any Python
    handle.write(text)
path.write_bytes(text.encode("utf-8"))                          # any Python
path.write_text(text, encoding="utf-8", newline="\n")           # 3.10 and later
```

Write an edit script to a file and run it. A script passed inline through a
shell can lose a level of backslashes on the way, turning `\r\n` written as
text into a real carriage return and `\b` into a backspace byte.
`dotagents eol check --control` finds those.

## Changing an existing file

Match the file you found. Convert a whole file deliberately, as its own
change, or leave it alone: a partial conversion is the mixed state this rule
exists to prevent.

Where another writer is active in the same tree, a conversion will not stick;
leave it for when the tree is yours.
