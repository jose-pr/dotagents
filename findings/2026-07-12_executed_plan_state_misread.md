# Finding: Executed Plan State Misread

Date: 2026-07-12

## What happened

An agent reviewed a project's recent plan/commit state and incorrectly reported that a
standards-compliance follow-up plan was still pending. In reality, the plan had
already been executed and removed, and the source/tests had been updated accordingly.

The misread happened because the agent reasoned from an earlier in-session summary and
from stale references inside another active plan instead of re-validating the live
plan tree and current source/tests before speaking confidently about execution state.

## Why this was avoidable

The global config had strong rules about where plans live and how to treat
`completed/`, but it did not explicitly tell agents to capture config-caused misses as
reusable findings. Without that prompt, the mistake could have been corrected
locally in conversation and then forgotten, leaving the same failure mode available in
future sessions.

## How it could have been avoided

- Re-check the current top-level plan directory before asserting whether a follow-up
  plan is pending, especially after a user says something has already been executed.
- Prefer current source/tests over stale plan references when the two disagree about
  execution state.
- Treat "plan file no longer exists" as a signal to inspect whether the work landed
  and the plan was cleaned up, not as proof that no execution happened.

## What was done later

- The live plan tree and relevant source/tests were re-checked.
- The agent corrected the project-level assessment: the compliance plan had been
  executed and only the later inventory/triage plan remained.
- The dotagents config was updated so future agents explicitly record global-config
  misses in both the design log and a standalone finding note like this one.
