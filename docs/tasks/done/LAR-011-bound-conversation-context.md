---
tracker: LAR-011
component: agent-runtime
status: done
priority: P1
effort: M
parallel_safe: no
created: 2026-09-16
updated: 2026-09-19
tags: [lar, context, recovery]
---

# LAR-011 - Bound conversation context without breaking continuity

**Status:** Done
**Priority:** P1 - unblock reliable multi-turn consumer conversations
**Effort:** M

## Goal

Correct the shared context-handling boundary exposed by Finance Vault APP-010:
longer valid conversations must not depend on provider truncation that breaks
message roles or loses the active working context. Keep product actions and
financial authority outside this runtime. Consumer-supplied resource skills and
active-action context are opaque conversation input: the runtime must preserve
their bounded delivery, but must never interpret a resource name, supply a
resource procedure or introduce consumer-specific fallback instructions.

## Owner authority

On 2026-09-16 Stephan additionally approved a capacity-aware output policy:
keep the configured output allowance whenever it fits; use a bounded per-call
fallback only for a known small context under pressure. Windows of 128,000
tokens or more retain configured output. Unknown capacity remains unknown;
all paths retain complete tool groups and explicit irreducible-input failure.
The fallback has a useful minimum and does not modify configured limits or
global model settings. This approval does not authorize release or closure.

On 2026-09-16 Stephan approved extending APP-010's fix into the shared Local
Agent Runtime rather than adding a consumer Chat workaround. This task owns
that bounded correction, tests and candidate-artifact integration. It does not
authorize commit, push, publication/release, deployment, unrelated LAR-006
closure, or changes to the user's global model settings.

## Wins

Consumers keep one capable conversation across tool work and follow-up
questions. Limits and any context reduction are explicit; state and approvals
are neither invented nor replayed to recover a model response.

## Lean evidence

- Verdict: pass for a bounded shared-runtime correction; implementation design
  requires inspection before selecting the smallest mechanism.
- Current evidence: standalone loopback Ministral 3 8B with a loaded 32,768
  token context accepts a valid 26-message transcript at 28,216 input tokens;
  enlarging the same message sequence beyond that context fails with a Jinja
  role-alternation error. Finance Vault reproduces it after ordinary Chat turns
  and a verified gold proposal. No consumer data is needed to reproduce it.
- Smallest complete outcome: safe context handling at the runtime/provider
  boundary, deterministic regressions, and a synthetic long consumer journey
  retaining current action context and permitted tools.
- Necessary complexity: truthful capacity evidence, coherent message/tool
  groups, explicit reduction or irreducible-input refusal, cancellation,
  streaming and no repeated material tool execution.
- Deferred: global memory, semantic retrieval, summarization frameworks,
  provider/model fallback, new providers and automatic model reconfiguration.

## Runtime gates

- Preserve exact provider/profile/model and local/external processing policy;
  unknown capacity is not fabricated capacity.
- Keep prompts, results and credentials out of ordinary diagnostics. Synthetic
  local probes only; no external disclosure of consumer financial data.
- Consumer context remains consumer-owned; runtime pruning never grants or
  recreates tool/material approval. Preserve complete tool-call/result groups.
- Preserve bounded input/output/time, streaming completion, cancellation and
  concurrency. Fail explicitly when the indispensable input cannot fit.
- Use versioned public contracts and candidate packages for integration, never
  copied runtime source, private imports or modified installed dependencies.
- No UI, resource-specific branching, database or financial workflow ownership
  enters the runtime. Compatibility is not task qualification.
- No consumer resource question, answer interpretation, acquisition recipe,
  field map, helper order, candidate conversion or capability decision enters
  runtime prompts, context-reduction policy, adapters or recovery. The runtime
  handles consumer context structurally and does not need to know whether it
  describes gold, a brokerage account or another resource.

## Scope

- Diagnose the size-triggered error at the shared boundary and implement the
  smallest coherent context policy supported by actual provider capabilities.
- Keep current instructions/request and refreshed consumer working context
  available; disclose reductions rather than silently claiming full history.
- Add any narrowly necessary public contract/client changes consistently.
- Validate the packaged candidate with Finance Vault's existing ordinary Chat.

## Acceptance

- The reproduced long conversation can continue within the supported policy
  without malformed role sequences or a consumer-specific alternate chat path.
- Recent tool execution cannot be orphaned or repeated by recovery; current
  user intent and active consumer context survive any permitted reduction.
- The same runtime behavior preserves two synthetically different resource
  skill/action payloads without resource-specific branches or wording. Missing
  consumer context fails according to the public context contract; the runtime
  never supplies a built-in resource fallback.
- Unknown capacity and irreducibly oversized input have truthful bounded
  behavior; no silent provider/model switch or global context-size change.
- Runtime full gate and relevant security/contract tests pass. Integration
  evidence distinguishes local candidate validation from a released consumer pin.

## Test cases

- Short unchanged transcript; long multi-turn conversation; multiple tool
  calls/results; current system/request/context retention; reduction disclosure.
- Oversized indispensable input, missing/malformed capacity metadata, loaded
  versus maximum model context, output reserve and tool-schema overhead.
- Streaming/nonstreaming, cancellation/timeouts, redaction, no side-effect
  replay, and unchanged non-target provider behavior.
- Real synthetic local conversation through the candidate package and Finance
  Vault, including a pending resource proposal and unrelated follow-up.
- Consumer-opacity regression: pass differently shaped synthetic resource/action
  contexts through initial, continued and reduced turns; assert identical
  structural treatment, preserved current context, and no resource/provider
  names or procedures added by runtime code.

## Dependencies

Finance Vault APP-010 supplies the present consumer and owns its integration,
action persistence, live financial-workflow validation and final UX review.
Release/promotion remains a separate owner gate.

## Key files

- `src/local_agent_runtime/service.py` — shared session/message lifecycle.
- `src/local_agent_runtime/adapters/providers/lmstudio.py` — provider boundary.
- `docs/architecture/provider-adapters.md` — capability and context semantics.
- `clients/typescript/` — public client and host contracts if required.

## Progress Notes

- 2026-09-19: Closed. The review candidate was committed on
  `sbohmig2/lar-011-bound-context` (`7d34527`) and rebased onto `main` after
  LAR-012 (`efd308d`); the full gate on the rebased candidate passed (339
  Python tests, one skip; 52 TypeScript tests; contracts current). Codex Sol
  performed the confirming independent review (attempt 3, snapshot
  `14ef8d18…`, `git range-diff` showed the LAR-011 patch unchanged by the
  rebase): every scope and acceptance item addressed except the live Finance
  Vault journey, which the isolated review could not rerun and whose recorded
  candidate evidence it found internally consistent; CR-001/CR-002/CR-006
  remain resolved; LAR-012's per-turn tool-request identity does not interact
  with context planning, whole-turn retention, replay safety or opacity;
  verdict `READY TO CLOSE`. Advisory follow-ups stay open for a later task:
  CR-003 (an irreducible follow-up is retained in a terminally failed session,
  unlike the synchronous opening refusal), CR-004 (a refused plan can leave
  `context.reduced: true` although nothing was sent), CR-005 (opening-turn
  preflight uses the generic estimate, not the adapter's `prompt_chars`),
  CR-007 (quadratic suffix measurement on very long transcripts; stale test
  name `test_unknown_capacity_changes_nothing_and_retained_limits_still_apply`)
  and CR-008 (the `context.py` module docstring omits the character-ceiling
  pruning under unknown capacity). Released as `v0.6.0` / API `1.5.0` on the
  owner's closure instruction ("please bring this to close"); Finance Vault
  pins both packages in the same session.

- 2026-09-16: Claude remediated the accepted formal-review findings CR-001,
  CR-002 and CR-006 without touching CR-003/4/5/7. CR-001: the TypeScript host's
  `context_reduced` handling now accepts and preserves `null` for exactly
  `capacity_tokens` and `budget_tokens` (the runtime's truthful unknown-capacity
  reduction) while every other count stays a strict safe integer; a host
  regression proves the Python-shaped unknown-capacity event completes with
  `capacityTokens`/`budgetTokens` kept as `null` and that a `null` in any other
  count still fails as `invalid_runtime_event` (it failed against the pre-fix
  hunk and passes now). CR-002: `provider-adapters.md`, `model-providers.md`
  and `integration.md` now state that `max_input_chars` bounds incoming input
  where it arrives and each call's serialized window, never the retained
  transcript; that unknown capacity applies no token budget but the character
  ceiling alone can still prune with `null` capacity/budget reported; that a
  character-bound mandatory refusal is `input_limit_exceeded` and a
  token-bound one `context_window_exceeded`; and that every shipped adapter
  implements `PromptSizingProviderPort.prompt_chars`, with the generic estimate
  only as a fallback and the adapters' own refusal unchanged. CR-006: a
  parametrized regression passes two synthetic, differently shaped opaque
  contexts (a structured skill/action record and a prose procedure, equal in
  size) through initial, continued and reduced turns under known and unknown
  capacity, asserting verbatim delivery with nothing added by the runtime,
  survival of only the current copy through reduction beside unchanged tools,
  counts-only disclosure, an unchanged provider/model route, refusal of a
  missing prompt without any fallback, and identical planning traces for both
  shapes. `uv run python scripts/check.py` passed: Ruff format/check, mypy over
  45 files, 337 Python tests with one skip, generated contracts current, 52
  TypeScript tests, docs/package/build/install/sdist checks, no release
  manifest. No version bump, release document, manifest, commit, push,
  publication, consumer edit or closure occurred; re-review is pending.

- 2026-09-16: Mature candidate evidence is complete and the task moved to
  independent review. The final runtime gate passed Ruff format/check, mypy over
  45 files, 331 Python tests with one skip, 51 TypeScript tests, generated
  contract checks, documentation checks and both package builds. Finance Vault
  then exercised the isolated `0.5.3-dev.2` / API `1.5.0` artifacts through one
  real local-model conversation: repeated whole-turn reductions retained the
  active resource instructions and action, ordinary/history/unrelated turns
  remained usable, invalid answers caused no proposal, two exact resource
  actions switched with only one active, both proposals applied once, and a
  final read answered 5 g + 7 g = 12 g with no active action. The adapter-owned
  exact sizing path was exercised after tool-call-heavy rounds and did not hit
  the earlier post-planning wire refusal. This is candidate and consumer
  integration evidence only: no commit, release, publication, consumer pin or
  closure occurred.

- 2026-09-16: Live APP-010 acceptance showed the retained-session ceiling
  defeating the approved policy: `RuntimeService._ensure_schedulable` measured
  the complete retained transcript against `max_input_chars`, so a fourth
  ordinary turn failed with `input_limit_exceeded` before `plan_context` could
  prune earlier turns. Claude removed that whole-transcript check. Incoming
  input stays bounded where it arrives (the opening turn as a whole, each
  follow-up prompt, each tool result), the event limit still bounds how many
  turns a session retains, and the adapters' serialized-body limit after
  planning remains the fail-closed wire ceiling. The former ceiling test was
  replaced by one proving a long retained transcript is pruned per call with
  known capacity, that over-long incoming input is still refused without
  retention, and that with unknown capacity the unpruned invocation is refused
  by `chat_body` before any provider call. Focused and full-gate results are
  recorded by the implementer; no release, commit or closure.

- 2026-09-16: The owner then required the profile's character ceiling to be a
  planning dimension rather than only the adapters' last refusal. `plan_context`
  now measures each candidate window as the adapters serialize it
  (`estimate_prompt_chars`: public message/tool forms, output schema and a
  framing allowance) and keeps the largest suffix of whole turns that fits both
  the known token budget and `max_input_chars`; with unknown capacity the
  character ceiling alone prunes, and capacity fields stay unknown. A mandatory
  window that cannot fit the ceiling fails explicitly with
  `input_limit_exceeded` and `INPUT_IRREDUCIBLE_MESSAGE` before any provider
  call; token-bound refusals keep `context_window_exceeded`. Output allowance
  policy and counts-only context reporting are unchanged, the opening turn is
  checked with the same measure at creation, and the adapters' serialized-body
  refusal remains as the unchanged final check. Regression tests cover
  token-roomy character pruning, unknown capacity under character pressure,
  the irreducible current turn (tool results) and the adapter refusals.

- 2026-09-16: Review noted that sizing a window by its public form could admit
  a request the chat wire refuses (tool-call arguments are re-encoded as JSON
  strings there, tools gain function wrappers and separators are spaced).
  Planning now prefers the adapter's own exact measure through the optional
  `PromptSizingProviderPort.prompt_chars`, implemented by the LM Studio adapter
  (the larger of its streaming and non-streaming bodies), OpenRouter and every
  CLI adapter, each returning exactly what its send-time check bounds; an
  invalid size is an `invalid_provider_contract` failure. Without the port the
  generic estimate stands in and is now the larger of both wire shapes. The
  adapters' own checks are unchanged. Tests prove a compact-fitting window the
  chat body refuses is never admitted, that each adapter sizes as it checks,
  and that every planned invocation is accepted by both `chat_body` and the
  CLI bounded prompt.

- 2026-09-16: Finance Vault's owner made resource-skill ownership a blocking
  architecture rule after finding resource recipes in its application host.
  LAR-011 now records the corresponding runtime boundary: resource/action
  context is opaque consumer input, and no consumer recipe or fallback belongs
  here. This docs-first checkpoint changes no runtime code, candidate artifact,
  release state or prior test evidence.

- 2026-09-16: Claude implemented the owner-approved small-context allocation
  policy. The full runtime gate passed: 320 Python tests, one skip, 51
  TypeScript tests, lint/type/contract/package checks. The exact 32,768-token
  case with a 22,972-token input estimate and configured 8,192 output now plans
  8,157 output tokens. Tests cover the decimal 128,000 threshold, unchanged
  large-context behavior, per-call restoration, floor/refusal boundaries,
  wire-level limits, and incomplete streamed/nonstreamed replies without tool
  execution. The unreleased `0.5.3-dev.2` / API `1.5.0` candidate is frozen in
  `/tmp/lar011-candidate2.QbiSpw/repo/dist`; Codex verified its hashes before
  isolated consumer integration. A documentation-only review correction
  clarifies that provisional stream deltas can precede the explicit failure;
  no completed answer or tool request is returned on length termination.
  Live consumer acceptance and independent whole-task review remain pending.

- 2026-09-16: Claude's bounded implementation completed with API 1.5.0 context
  metadata, counts-only reduction events, whole-turn selection, loaded-model
  capacity evidence and unchanged retained-session ceilings. The full gate
  passed (299 Python tests, one skip; 51 TypeScript tests and package checks).
  Unreleased wheel/client candidate `0.5.3-dev.1` was frozen in
  `/tmp/lar011-candidate.v7zoa2/repo/dist`; no release was made. Synthetic probes
  completed 25 source-tree turns and 10 packaged turns with reductions and no
  role error; the final two malformed-capacity guards were checked separately
  and do not alter that valid-model path. Consumer integration remains blocked:
  at the original 16,384-token output reservation a restored request estimated
  17,294 input tokens against a 14,745 budget. At a diagnostic 8,192 reservation,
  both gold proposals were confirmed successfully, but a later factual read
  required an estimated 22,972 tokens against 22,937. Both refusals were explicit
  and preceded the next provider call, with no financial side-effect replay.
  The task is not ready for closure. A proposed allocation-policy refinement
  (configured output remains a maximum, with a reported smaller per-call
  allocation when required context needs space) awaited owner agreement at
  that checkpoint; the later bounded approval is recorded above. Do not use ad hoc ratio
  changes or global model reconfiguration.

- 2026-09-16: Coordinator approved Claude Fable's bounded plan: an optional
  provider capacity port, LM Studio's exact loaded-instance context evidence,
  a shared estimate-based policy retaining system instructions and the entire
  current user/tool turn, and counts-only reduction disclosure. Preserve the
  full transcript and never replay tools. Unknown capacity stays unknown;
  no operator override or new memory framework is added. Context estimation
  must include tools, output schema and non-ASCII text and must not be described
  as exact tokenization or a guaranteed bound. Finance Vault owns complete
  current action/skill context on each turn. Candidate packages are isolated
  local development artifacts, not permanent local-path consumer dependencies.
  Implementation and validation are in progress; no acceptance pass is claimed.

- 2026-09-16: Owner-approved scope recorded after the isolated size-triggered
  reproduction. Implementation plan and public-boundary implications are being
  inspected before structural changes; no verified fix or release is claimed.
