# Flow: Parallel Work

Several agents on one repository at once. The failures are quiet: an agent does
competent work against the wrong tree, nothing warns, and the damage appears at
integration looking like someone else's mistake.

## Whether to

- **Reads and research parallelise freely.**
- **Writes to disjoint files are better sequenced**, or given to agents in one tree
  in turn: no setup, no merge step, and the last agent integrates real work.
- **A worktree each is for agents changing the same files at the same time**, and
  only then.
- **Git writes are sequential.** `add`, `commit`, `merge`, `rebase` share one index;
  two at once collide on its lock.

## One executor per working tree

Two agents writing in one tree break each other's evidence: a test fails on the
other's half-written module, a build cannot replace a binary the other is running,
a file converted to LF is rewritten as CRLF.

- Before the first edit, look for another writer: uncommitted changes or fresh
  modification times outside your files.
- If someone is there, verify in isolation (your own worktree or a copy, and a
  build directory of your own), leave their files alone, line endings included,
  and say in the handoff which failures came from files you do not own.

## Before fanning out

1. **Commit.** A worktree starts from a commit; uncommitted work is invisible to it.
2. **Record the base**: `git rev-parse --short HEAD`.
3. **Make sure the repository exists, and launch from it.** Worktree isolation is
   resolved when the agents are launched, from the session's current directory: a
   repository created earlier in the same run is not there yet, and a cwd inside
   `<project>/.agents` (itself a checkout of the agents repo) isolates the agents
   in that repo, not the project. `cd` to the project's root first.
4. **Put the base in every brief**, with a landmark the agent can check: "Your base
   is `<sha>`. Run `git log --oneline -3` first. If the tree does not contain
   `<a file or commit from this session>`, stop and say so."
5. **Say what each agent owns**: which files it may change and which it must not
   touch.
6. **Say what each worktree is for**, in its name or a note, so a reader can judge
   it later without opening it.

## The agent's first step

Check the base before reading anything else. A worktree can begin at a commit hours
behind the one it was launched from and still look healthy (`git log` is coherent,
just old); an agent on a stale base infers conventions from a tree that no longer
exists.

## Integrating

- **Count first**: agents launched, results returned. A failed agent can come back
  as nothing, which is indistinguishable from "found nothing" unless counted.
- **Check each base**: `git -C <worktree> rev-parse --short HEAD` against the
  recorded one. If it differs, everything that agent inferred is suspect, not only
  its diff.
- **Apply the diff, never copy the files.**

  ```bash
  git -C <worktree> diff <its base> -- <paths> > change.patch
  git apply -3 change.patch
  ```

  A three-way apply carries the change relative to the agent's base and conflicts
  loudly where the main tree moved; copying files silently reverts what the main
  tree gained since.
- Integrate one worktree at a time, and run the checks after each.

## Cleaning up

A leftover worktree is a plausible-looking tree at the wrong base; whoever opens it
later gets confident, wrong answers.

- **Every worktree ends integrated and removed, or reported as left behind** with
  what it holds and why.
- `git worktree list` shows them all; `git worktree prune` removes only those whose
  directory is already gone.
- A clean, integrated worktree: `git worktree remove <path>`, then delete its branch.
- **A dirty worktree is read before it is removed**: its changes are unfinished work
  or an integration that never happened. Never force-remove one to shorten the list.
- Clean up at the end of the run that created them; later, nobody knows what they
  were for.
