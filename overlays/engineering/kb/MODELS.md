# Models - Role and Executor Routing

Load when assigning a model to a plan, delegating to a subagent, or deciding whether
a task needs an expensive model. Plans name a role/subrole, never a raw provider
model ID; the executor resolves the role against the providers and surfaces
available in the current run.

Last reviewed: 2026-07-16. Provider catalogs and entitlements move; refresh this
file when a provider changes its picker. Provider documentation (References) is the
source of truth for names and knobs; the field reports behind these heuristics are
anecdotal.

## Provider Surfaces

| Provider | User surface | Default route |
|---|---|---|
| OpenAI | ChatGPT Plus / Codex with local shell and workspace tools | host-native Codex route |
| Anthropic | Claude Pro app/subagent | host-native Claude route when callable |
| Google | Gemini Pro app/agent | host-native Gemini route when callable |

A subscription is not an API key or a callable local subagent. Choose only a surface
actually available in the current run.

## Roles

Use `family/subrole` in every plan:

- `architecture-planner/plan` - design and plan authoring.
- `code-executor/implemented` - implementation from a ready plan.
- `mechanical-executor/implemented` - exact moves, renames, and specified edits.
- `test-verifier/implemented` - tests, diagnosis, and evidence reporting.
- `reviewer/code` - independent correctness and regression review.
- `reviewer/security` - threat modeling and security review.
- `researcher/current` - current docs, dependency, and provider research.
- `docs-editor/implemented` - README, changelog, and API documentation.
- `release-operator/implemented` - release preparation and authorized publishing.

## Role Assignments

| Role | OpenAI / ChatGPT Plus | Anthropic / Claude Pro | Google / Gemini Pro | Settings |
|---|---|---|---|---|
| `architecture-planner/plan` | GPT-5.6 Luna; Sol escalation | Opus 4.8 | Gemini 3.1 Pro | OpenAI high; Sol low/medium for high-blast or persistent unknowns; Claude adaptive/high; Gemini high/Extended |
| `code-executor/implemented` | GPT-5.6 Luna; GPT-5.3-Codex for Codex-specific runs | Sonnet 5 | Gemini 3.1 Pro | OpenAI high; xhigh after a demonstrated stall; Claude adaptive/xhigh; Gemini high/Extended |
| `mechanical-executor/implemented` | GPT-5.6 Luna; GPT-5.4 mini fallback | Haiku 4.5 | Gemini 3.5 Flash | OpenAI medium/high; Claude adaptive/medium; Gemini low/Standard |
| `test-verifier/implemented` | GPT-5.6 Luna; Terra only for broad tracing | Sonnet 5 | Gemini 3.1 Pro | OpenAI high; Claude adaptive/high; Gemini high/Extended |
| `reviewer/code` | GPT-5.6 Sol | Opus 4.8 | Gemini 3.1 Pro | OpenAI low; medium for high-blast or persistent failures; Claude adaptive/high; Gemini high/Extended |
| `reviewer/security` | GPT-5.6 Sol | Opus 4.8 | Gemini 3.1 Pro | OpenAI medium; high for critical boundaries; Claude adaptive/max; Gemini high/Extended |
| `researcher/current` | GPT-5.6 Terra; Sol for high-stakes synthesis | Opus 4.8 | Gemini 3.1 Pro with Search | OpenAI medium/high; Claude high; Gemini Extended thinking |
| `docs-editor/implemented` | GPT-5.6 Luna | Sonnet 5 | Gemini 3.5 Flash | OpenAI medium; Claude adaptive/medium; Gemini low/Standard |
| `release-operator/implemented` | GPT-5.6 Luna | Sonnet 5 | Gemini 3.1 Pro | OpenAI high; use a separate Sol reviewer for high-blast releases; Claude adaptive/high; Gemini high/Extended |

## Resolution

1. Read `Executor: <role/subrole>` from the plan.
2. Identify the host: Codex/OpenAI, Claude/Anthropic or Gemini/Google. The host
   picks the column, not table order; other columns are fallbacks only when
   callable.
3. Verify that column's model is callable and apply its settings.
4. Record the resolved host/provider/model/settings in plan Progress before editing.
5. Use a cross-provider fallback only when the caller permits it; never downgrade
   architecture or security work to a mechanical role. Never split one plan across
   providers: a fallback is a recorded deviation.

For independent review, use a different provider from the implementation route when
available.

## Escalation

Luna high handles planning and scoped implementation; use xhigh only if verification
still fails after one targeted repair. Choose Terra medium only if unresolved
uncertainty spans multiple subsystems. Sol starts at low for independent review;
medium for contradictory findings or repeated failure, high only for a named critical
boundary; stop or re-scope if it exceeds the stated scope or time box. `max` requires
explicit approval. Escalate by blast radius and recovery cost, not prestige or price.

## Settings Contract

- OpenAI reasoning models use `reasoning_effort=none|low|medium|high|xhigh|max`
  where supported; `ultra` is a Codex multi-agent mode, not a normal setting. Do not
  pass removed sampling parameters.
- Table settings are actual. Map nominal Sol `medium|high|xhigh` to actual
  `low|medium|high`.
- Claude uses `thinking={"type":"adaptive"}` and
  `output_config={"effort":"low|medium|high|xhigh|max"}`.
- Gemini 3 uses `thinking_level=minimal|low|medium|high`; map this to Standard or
  Extended thinking in the app. Deep Think is not assumed.

## References

- OpenAI models and settings: <https://developers.openai.com/api/docs/models>
- Anthropic models and effort: <https://platform.claude.com/docs/en/build-with-claude/effort>
- Google Gemini models and thinking: <https://ai.google.dev/gemini-api/docs/gemini-3>
- Field report and companion config: <https://www.reddit.com/r/codex/comments/1utzi5w/gpt56_sol_vs_terra_vs_luna_my_early_guide_to/>, <https://github.com/nsEytgXm/subagents_configs>
