---
tracker: LAR-013
component: agent-runtime
status: review
priority: P0
effort: S
parallel_safe: no
created: 2026-09-23
updated: 2026-09-24
tags: [lar]
---

# LAR-013 - Restore unattended structured-work parity

**Status:** Review
**Priority:** P0 - Knowledge Vault's retirement of its own provider adapters
(its MPR-005) is blocked until the runtime can run CLI routes without web and
handles two LM Studio behaviors.
**Effort:** S

## Goal

Close three gaps where the runtime is less capable than the adapters Knowledge
Vault is retiring:

0. Let an unattended consumer run the Codex, Claude, and Grok CLI routes with no
   provider-native web tools for an invocation. This is a privacy and security
   gap, and the highest priority of the three.
1. Accept a schema-bound answer that an LM Studio reasoning model returns in
   `message.reasoning_content` (or `message.reasoning`) with empty `content`.
2. Stop removing `minLength` and `maxLength` from the LM Studio
   structured-output schema, so LM Studio enforces the bounds where it
   supports them. Do this without reintroducing the tool-schema refusal that
   LAR-010 fixed.

## Wins

- Knowledge Vault can send private vault evidence to CLI routes without a
  prompt-injected document being able to make the model fetch an attacker URL
  carrying that evidence. Finance Vault and chat keep native web by default.
- Knowledge Vault's structured tasks (insights, dynamic pages, ingestion
  preparation) keep their provider-enforced string-length bounds through the
  runtime. They no longer fail application validation after LM Studio
  overshoots a bound it was never sent.
- Reasoning models that separate their structured answer from ordinary content
  become usable for schema-bound unattended work, without exposing hidden
  free-form reasoning.

## Lean evidence

- Verdict: pass. All three gaps are demonstrated by a consumer migration. The
  LM Studio gaps were reproduced or characterized locally, and the CLI disabled
  forms were checked against the installed CLIs.
- Gap 0 evidence: since LAR-008 every CLI invocation enables native web
  unconditionally. Knowledge Vault's retired adapters disabled it for their
  unattended tasks, which send private vault evidence.
- Current evidence: Knowledge Vault MPR-005 recorded the Qwen reasoning-channel
  shape (2026-08-05). It also recorded a synthetic 200-character proof schema
  that ministral 3 8B honored on 3/3 runs through its old adapter, and overshot
  on 3/3 runs through runtime `v0.6.0`.
- Smallest complete outcome for Gap 0: a defaulted `Invocation` field that the
  CLI adapters honor, with no HTTP or session surface.
- Smallest complete outcome for the LM Studio gaps: stop removing length keywords from the
  structured-output wire schema, and add an adapter opt-in to the shared chat
  decoder for the reasoning-channel answer. No retry machinery and no public
  HTTP contract change.
- Necessary complexity: the output bound, a JSON-only candidate, content
  precedence, and a narrow request predicate (schema-bound, tool-free,
  non-streaming, LM Studio only).
- Deferred: a session or HTTP native-web switch; lifting tool-schema stripping, which current engine evidence
  suggests is no longer needed, and any grammar-refusal retry.

## Runtime gates

- Provider and profile identity: unchanged. The reasoning-channel rule is
  LM-Studio-only, and OpenRouter keeps refusing a reasoning-only response.
- External egress: a consumer can deny provider-native web for each invocation.
  The route's `provider_native_web` capability still reports what it can do.
- Local versus external processing: unchanged; the live probes used loopback LM
  Studio only.
- Credentials and redaction: no new diagnostics. A grammar refusal surfaces
  only fixed runtime text.
- Capability and qualification: no qualification claim. Engine evidence is
  recorded in the provider documentation.
- Streaming: unchanged. The service never streams a schema-bound invocation,
  and reasoning deltas are never promoted.
- Tools: tool input schemas keep LAR-010's wire translation, and tool-bearing
  requests never promote reasoning text.
- Provenance: unchanged.
- Package compatibility: paired minor `0.7.0`, because the public Python
  contract gains a field; API `1.5.0` unchanged, because HTTP and OpenAPI are
  unchanged.
- No fallback: a grammar refusal is never retried with a weakened schema or
  another model.

## Scope

- Add `Invocation.provider_native_web: bool = True`. Codex, Claude, and Grok
  enable native web only when both the route capability and the invocation
  permit it. Otherwise:
  - Codex sends `web_search="disabled"`;
  - Claude sends `--tools ""` and `--disallowedTools WebSearch,WebFetch`;
  - Grok sends `--tools ""`, `--disallowed-tools
    search_tool,use_tool,web_search,web_fetch`, and `--disable-web-search`,
    without `GROK_WEB_FETCH`;
  - the prompt forbids every provider-native tool.

  All other isolation arguments are identical in both modes.
- Deliberate boundary: `RuntimeService` sessions and the HTTP gateway keep the
  default. Knowledge Vault calls adapters directly through `build_provider`, so
  it needs no session surface. Exposing the switch there would be an API change
  and is deferred until a gateway consumer needs it.
- Send the LM Studio `response_format` schema exactly as the application
  supplied it. Keep the recursive `minLength` and `maxLength` omission for tool
  input schemas only.
- In LM Studio non-streaming completions only, promote the first non-blank
  `reasoning_content` or `reasoning` string to the answer when all of these
  hold:
  - the invocation has an output schema and no tools;
  - the response has no non-blank content and no tool calls;
  - `finish_reason` is already acceptable;
  - the text fits `max_output_chars`;
  - the text parses as JSON.
- The shared codec receives this as an explicit adapter opt-in rather than
  branching on a driver name.

## Acceptance

- For each CLI route, the argument list, environment, and prompt differ exactly
  between native web on and off. The default equals on, and today's arguments
  are unchanged. LM Studio and OpenRouter request bodies are identical either
  way.
- The LM Studio wire `response_format` schema equals the application schema,
  including nested string-length bounds. Tool schemas in the same request are
  still translated, and caller-owned schemas are not mutated.
- A grammar refusal, or any other LM Studio error, is sent once, fails
  explicitly, and is never retried or stripped.
- A reasoning-channel answer is accepted only under the scoped conditions.
  Content wins, and oversized or non-JSON reasoning, unstructured requests,
  tool-bearing requests, streaming, and OpenRouter are refused.
- Through the service, a promoted answer is still validated against the
  original schema: a 201-character summary against `maxLength: 200` fails
  `schema_validation_failed`.
- Documentation and the prepared `0.7.0` version match the behavior. The full
  repository gate passes.

## Test cases

- `tests/test_providers.py`: the per-invocation native-web matrix (3 CLIs by
  default, on, and off); a route without the capability sends its disabled form
  even when permitted; the default field value; HTTP routes are unaffected.
- `tests/test_providers.py`: wire schemas for streaming and non-streaming
  bodies; the grammar refusal delivered as a compatible payload and as HTTP
  400, plus an unrelated 400, each with exactly one request; reasoning-channel
  acceptance and refusal matrices; streaming non-promotion; OpenRouter refusal.
- `tests/test_runtime.py`: end-to-end LM Studio adapter behind the service,
  where a reasoning-channel answer passes at 200 characters and fails
  validation at 201.

## Dependencies

None. Knowledge Vault MPR-005 depends on the release of this task.

## Key files

- `src/local_agent_runtime/contracts.py` - `Invocation.provider_native_web`.
- `src/local_agent_runtime/adapters/cli_base.py` and
  `src/local_agent_runtime/adapters/providers/{codex,claude,grok}.py` - the
  native-web switch.
- `src/local_agent_runtime/adapters/providers/lmstudio.py` - wire-schema
  translation and the opt-in.
- `src/local_agent_runtime/adapters/chat_codec.py` - the reasoning-channel rule.
- `tests/test_providers.py`, `tests/test_runtime.py`
- `docs/architecture/provider-adapters.md` - behavior.
- `docs/providers/model-providers.md` - LM Studio engine evidence.
- `docs/integration.md` - consumer guidance.
- `docs/releases/v0.7.0.md` - prepared release notes.

## Progress Notes

- 2026-09-23: Specification from Knowledge Vault's MPR-005 findings. Created in
  a separate worktree on branch `sbohmig2/lar-013-lmstudio-structured-parity`.
- 2026-09-23: Live evidence, all on loopback LM Studio with synthetic prompts
  and schemas. The llama.cpp engine 2.41.0 was selected and installed
  2026-09-19; `ministral-3-8b-instruct-2512` (GGUF) was loaded with an 8192-token
  context. The other chat models installed are 17 to 23 GB MLX models and were
  not loaded.
  - The Knowledge Vault proof schema (`status` const, `summary` `maxLength:
    200`) was accepted on 3/3 runs. Summaries were 171, 200, and 200 characters.
    The same schema without `maxLength` produced 364, 224, and 377.
  - A nested object-array-object schema with `minLength` and `maxLength` was
    accepted on 3/3 runs, and every string was within bounds (titles 20, bodies
    78 to 80).
  - A `minLength: 40` bound was enforced whenever content was returned (40 to
    61 characters, 6 runs).
  - In 3 of 9 runs, ministral wrote free-form, non-JSON text to
    `reasoning_content`, stopped with `finish_reason: length`, and returned
    empty content. In one further run, both non-JSON reasoning and valid
    content were present.
  - Tool schemas with nested `minLength` and `maxLength` were all accepted.
    Non-streaming, this covered 1 tool, 43 tools with bounds on the last, and 43
    tools that all carry bounds (2 runs each, every one calling
    `operation_finalize`). Finance Vault's exact `operation_run_helper` shape
    with `format: date-time` was accepted in five keyword variants, both
    streaming and non-streaming.
- 2026-09-23: History from LM Studio's local server logs, error lines only,
  with no request content inspected.
  - Every grammar refusal in the retained logs is from 2026-09-10, during
    Finance Vault's session: 12 dated lines for ministral 8B and 1 for 3B.
  - Each reads `Engine protocol predict request returned 400: {"error":{"code":
    400,"message":"Failed to initialize samplers: failed to parse
    grammar","type":"invalid_request_error"}}` on streaming requests.
  - At that time the newest installed llama.cpp engine was 2.29.1 (installed
    2026-08-23). The logs do not record the selected engine, and 2.41.0 was
    installed on 2026-09-19.
  - No other day has a grammar refusal.
  - Conclusion: the LAR-010 failure was most likely engine-version-specific.
- 2026-09-23: Gap-2 choice: design 1. Keep stripping tool input schemas, where
  the refusal was observed, and send the structured-output schema unchanged.
  - Live evidence shows LM Studio accepts and enforces nested length bounds in
    `response_format`.
  - This is the smallest change and needs no refusal parsing or retry.
  - It does not reintroduce the recorded tool refusal on older engines.
  - Design 2 (a single stripped retry on a recognized grammar refusal) was not
    needed for the observed engine. It would also need the adapter to read
    HTTP-400 bodies and to avoid retrying after streamed text.
  - A grammar refusal therefore stays explicit and is never retried or
    weakened.
- 2026-09-23: Gap-1 implementation. `decode_chat` gained the keyword-only opt-in
  `structured_reasoning_channel`, which only `LMStudioAdapter.complete` passes.
  - One deliberate tightening over Knowledge Vault's retired rule: the promoted
    text must parse as JSON. Free-form reasoning therefore fails as
    `invalid_provider_response` inside the adapter rather than reaching the
    service as candidate answer text. Knowledge Vault's schema-bound outputs are
    JSON, so no valid answer is lost.
  - Streaming is unchanged, because the service selects `complete` for every
    invocation with an output schema. The existing
    `test_structured_output_uses_non_streaming_completion_path` proves this.
  - The streaming decoder ignores reasoning deltas, and a new test pins that.
  - The Qwen answer-in-reasoning shape was not reproducible with the loaded
    model, so it is unit-tested from constructed payloads.
- 2026-09-23: Live check of the changed adapter against local LM Studio. The
  proof schema returned summaries of 163, 200, and 163 characters on 3/3 runs,
  and all passed validation against the original schema. A 43-tool request with
  nested bounds on `operation_finalize` returned the expected tool call.
- 2026-09-23: First gate, with the LM Studio gaps only and version `0.6.1`:
  `uv run python scripts/check.py` passed after `npm --prefix clients/typescript
  ci` in the fresh worktree, with 362 Python tests passed and 52/52 TypeScript.
- 2026-09-23: Scope addition from the coordinator: Gap 0, native web for
  unattended CLI invocations. Evidence was gathered from the installed CLIs
  without any model call:
  - Codex 0.155.1: `codex -c web_search="<value>" features list` accepted
    `live`, `disabled`, and `cached`. It rejected `bogus` with ``unknown variant
    `bogus`, expected one of `disabled`, `cached`, `indexed`, `live` ``.
  - Codex and Grok both reject an unknown flag placed before `--help` (exit 2)
    and accept the full disabled form in the same position (exit 0).
  - Claude 2.1.280 `--help` documents `--tools ""` as "disable all tools" and
    documents `--disallowedTools`. Its `--help` short-circuits unknown flags, so
    parsing is not independently evidenced.
  - Grok 1.0.34 `--help` documents `--disable-web-search` as "Disable web search
    and web fetch tools".
  - Implemented as `Invocation.provider_native_web` (default `True`), with the
    effective decision computed by `CLIAdapterBase.native_web` and passed to
    `arguments`, `environment`, and the prompt.
  - Grok previously set `GROK_WEB_FETCH=1` unconditionally. Now it sets it only
    when web is enabled.
  - A route without the capability (a test subclass) previously still sent
    Codex `web_search="live"`. It now sends the disabled form.
- 2026-09-23: Version decision: minor `0.7.0`, not patch. The public Python
  contract gains `Invocation.provider_native_web`, and the CLI adapter hook
  signatures gain a keyword-only `native_web` argument. The repository has
  shipped new capability as minor versions (`0.3.0` to `0.6.0`) and fixes as
  patches. The API stays `1.5.0` because the HTTP and OpenAPI contract is
  unchanged. Prepared `0.7.0` in all paired version files and the draft
  `docs/releases/v0.7.0.md`, which replaces the `0.6.1` draft. Commit, push,
  tag, build for publication, release, and closure remain owner-gated.
- 2026-09-23: Full gate `uv run python scripts/check.py` passed on all three
  gaps at `0.7.0`:
  - ruff format and check, and mypy (45 files), were clean;
  - pytest: 375 passed, 1 opt-in live test skipped;
  - generated contracts were unchanged;
  - TypeScript: 52/52 passed;
  - package layout and docs validation were clean;
  - isolated `0.7.0` wheel, sdist, and tarball installation passed. No release
    manifest was created.
- 2026-09-24: Fresh independent Claude review reported **READY**, with no
  blockers. It inspected implementation, tests, contracts, and documentation
  statically; it did not run tests or a literal Git diff. A before/after
  worktree hash comparison found no reviewer changes. The implementer reran
  `uv run python scripts/check.py` afterward: 375 Python tests passed, one
  opt-in live test skipped, 52 TypeScript tests passed, and the type, package,
  documentation, and development-artifact checks passed. Release publication
  remains a separate step.
- 2026-09-24: Stephan authorized the reviewed release flow. Commit
  `d471b645e26c7e4e85503170255a3e7a93ba38f7` fast-forwarded to
  `origin/main`; GitHub CI passed; annotated tag `v0.7.0` and the four immutable
  GitHub release assets were published. A fresh download passed
  `uv run python scripts/build_release.py --verify` with a checksum manifest
  naming that source commit. The task remains in review pending owner closure;
  publication does not itself close it.

## Residual risk

- Disabled native web was verified by argument construction and local CLI help
  and parsing only, with no model run. An attended live turn could confirm that
  each CLI then refuses web use. Provider or administrator policy cannot
  re-enable a tool the CLI removed, but a future CLI version could rename a flag
  or value. Codex's `--strict-config` would then fail explicitly, while Claude
  and Grok depend on their documented flags.
- Sessions created through the runtime service or HTTP gateway still always
  permit native web on CLI routes.
- An older LM Studio engine that cannot compile string-length keywords in a
  `response_format` schema would now refuse that request explicitly, where
  `v0.6.0` sent a weaker grammar. No such refusal is recorded. Knowledge Vault's
  own adapter sent these bounds before adopting the runtime.
- The tool-schema omission may be unnecessary on current engines. Removing it
  is a separate owner decision.
- Engine evidence covers GGUF/llama.cpp only. MLX-engine grammar behavior was
  not probed, because loading those models would have exceeded the brief.
