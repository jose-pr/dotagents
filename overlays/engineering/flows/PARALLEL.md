# Flow: Parallel Work

Several agents on one repository at once. The failures here are quiet: an
agent does competent work against the wrong tree, nothing warns, and the
damage appears at integration looking like someone else's mistake.

## Whether to

- **Reads and research parallelise freely.**
- **Writes to disjoint files are better sequenced**, or given to agents in
  one tree in turn. There is no setup, no merge step, and the last agent
  integrates real work.
- **A worktree each is for agents changing the same files at the same time**,
  and only then.
- **Git writes are sequential.** `add`, `commit`, `merge`, `rebase` share one
  index; two at once collide on its lock.

## One executor per working tree

Two agents writing in one tree break each other's evidence: a test run fails
on the other's half-written module, a build cannot replace a binary the
other is running, a file converted to LF is rewritten as CRLF.

- Before the first edit, look for another writer: uncommitted changes or
  fresh modification times outside your files.
- If someone is there, verify in isolation — your own worktree or a copy, and
  a build directory of your own — leave their files alone, line endings
  included, and say in the handoff which failures came from files you do not
  own.

## Before fanning out

1. **Commit.** A worktree starts from a commit; uncommitted work is invisible
   to it.
2. **Record the base**: `git rev-parse --short HEAD`.
3. **Make sure the repository exists.** Worktree isolation is resolved when
   the agents are launched; a repository created by an earlier step of the
   same run is not there yet.
4. **Put the base in every brief**, with a landmark the agent can check:
   "Your base is `<sha>`. Run `git log --oneline -3` first. If the tree does
   not contain `<a file or commit from this session>`, stop and say so."
5. **Say what each agent owns**: which files it may change and which it must
   not touch.
6. **Say what each worktree is for**, in its name or in a note, so a reader
   can judge it later without opening it.

## The agent's first step

Check the base before reading anything else. A worktree can begin at a commit
hours behind the one it was launched from, and it looks perfectly healthy:
`git log` is coherent, just old. An agent on a stale base infers the
repository's conventions from a tree that no longer exists.

## Integrating

- **Count first.** Agents launched, results returned. A failed agent can
  come back as nothing at all, and an empty result is indistinguishable from
  "found nothing" unless the counts are compared.
- **Check each base**: `git -C <worktree> rev-parse --short HEAD` against the
  recorded one. If it differs, everything that agent inferred is suspect, not
  only its diff.
- **Apply the diff, never copy the files.**

  ```bash
  git -C <worktree> diff <its base> -- <paths> > change.patch
  git apply -3 change.patch
  ```

  A three-way apply carries the change relative to the agent's own base and
  conflicts loudly where the main tree moved. Copying files silently reverts
  everything the main tree gained since.
- Integrate one worktree at a time, and run the checks after each.

## Cleaning up

A leftover worktree is not clutter. It is a plausible-looking tree at the
wrong base, and whoever opens it later gets confident, wrong answers.

- **Every worktree ends in one of two states**: integrated and removed, or
  reported as left behind with what it holds and why.
- `git worktree list` shows them all. `git worktree prune` removes only the
  ones whose directory is already gone.
- A clean worktree whose work is integrated: `git worktree remove <path>`,
  then delete its branch.
- **A dirty worktree is read before it is removed.** Uncommitted changes
  there are unfinished work or an integration that never happened. Never
  force-remove one to make the list shorter.
- Do this at the end of the run that created them, not in a later tidy-up:
  later, nobody knows what they were for.
