# Models — Executor Selection

Load when: assigning a model to a plan, delegating to a subagent, or judging whether
a task is worth an expensive model. Every plan names its executor.

## Claude (primary — Pro sub; $/1M in→out is a *relative cost signal*, not a bill)

| Model | ID | Ctx | $/1M | Role |
|---|---|---|---|---|
| Fable 5 | `claude-fable-5` | 1M | 10→50 | Hardest unsolved design / long autonomous runs. Rare. Thinking always on. |
| Opus 4.8 | `claude-opus-4-8` | 1M | 5→25 | Architect: analysis, design, plan authoring, review. Rarely an executor. |
| Sonnet 5 | `claude-sonnet-5` | 1M | 3→15 | **Default executor.** Judgment calls, subtle logic. Near-Opus on coding/agentic. |
| Haiku 4.5 | `claude-haiku-4-5` | 200K | 1→5 | Mechanical only: renames, moves, import surgery, applying a specified diff. |

IDs are complete — never append a date suffix.

**Heuristic** — *could a competent junior do this with only the plan in front of them?*
- Yes, zero judgment → **Haiku**. It will not rescue an ambiguous step; it will guess.
- Yes, but calls need judgment → **Sonnet**. Most plans.
- No → the plan isn't `ready`. A plan needing Opus to *execute* wasn't finished being written.

## Thinking & effort (matters more than model choice)

- **Thinking**: `thinking={"type":"adaptive"}` on all current models. `budget_tokens` is
  removed (400s on Fable 5 / Opus 4.8 / Sonnet 5). Fable 5: always on, omit the param.
  Opus 4.8 runs *without* thinking if you omit it — set adaptive explicitly.
- **Effort**: `output_config={"effort": ...}` — `low|medium|high|xhigh|max`. Default `high`.
  - `xhigh` — coding and agentic work (Claude Code's default).
  - `high` — intelligence-sensitive work. The usual floor.
  - `medium`/`low` — routine, latency-sensitive, or subagents.
  - `max` — correctness ≫ cost. Prone to overthinking.
- **Prefer lower model + higher effort over higher model + low effort** when cost-bound.
  Higher effort on agentic work often *lowers* total cost by cutting turn count.
- Sampling params (`temperature`/`top_p`/`top_k`) are removed on current models — 400.

## Other subscriptions (fallback capacity, human-in-the-loop only)

Not wired into the harness — no dispatch, no shared context. Paste-in/paste-out only,
so treat as a capacity escape hatch, not an executor.

| Sub | Use for |
|---|---|
| ChatGPT Plus | Frontier-tier reasoning + web/canvas when Claude capacity is out. Tiering and model names move fast — check the picker rather than trusting a name recalled here. |
| Gemini Pro | Huge-context one-shots (whole-repo reads) and Google-ecosystem tasks. |

**Never split one plan across providers** — context doesn't transfer, and re-derivation
costs more than the capacity saved.

## Cost discipline

- The expensive model's job is **to not be needed twice**. An Opus hour on a precise plan
  beats three Sonnet passes on a vague one.
- Never delegate down to save money on work that will bounce back: a wrong Haiku run costs
  a Sonnet re-run *plus* an Opus diagnosis.
- One well-specified pass ≫ iterative nudging. Ambiguity revealed across turns is the most
  expensive failure mode.
- Don't pay tokens to read what a script can decide — see core `AGENTS.md` + `tools/`.
