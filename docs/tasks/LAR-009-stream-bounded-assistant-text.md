---
tracker: LAR-009
component: agent-runtime
status: review
priority: P1
effort: M
parallel_safe: no
created: 2026-09-09
updated: 2026-09-10
tags: [lar]
---

# LAR-009 - Stream bounded assistant text

**Status:** Review
**Priority:** P1 - Finance Vault APP-007 needs truthful partial local-model answers
**Effort:** M

## Goal

Let consumers render ordered, display-safe assistant text while a capable
provider is generating it, without inventing deltas for non-streaming routes or
weakening final-output, tool, cancellation, timeout, and size validation.

## Boundary

- `assistant_text_delta` carries only provider-emitted assistant text. It never
  carries reasoning, wrapper JSON, native provider events, tool names or
  arguments, credentials, prompts, or diagnostics.
- The existing event sequence is the ordering and reconnect contract. A
  one-based `round` identifies every provider call across the session, including
  calls after application-tool work and follow-up input.
- `session_completed.text` remains the authoritative validated final text.
  A failed or canceled session may have earlier provisional deltas but never a
  fabricated completion.
- Structured application output remains non-streaming until a display-safe
  route is separately proven. Provider capability does not promise that every
  invocation shape streams.
- Codex, Claude, Grok, and OpenRouter retain their existing non-streaming path.
  This task adds native streaming only to the loopback LM Studio adapter.
- Consumer persistence, interrupted-answer presentation, and UI remain outside
  this repository.

## Scope

1. Add an optional provider-neutral streaming port beside the existing
   completion port and keep non-streaming adapters source-compatible.
2. Emit bounded `assistant_text_delta` session events with `{round, delta}` and
   preserve them through authenticated gateway SSE and cursor replay.
3. Coalesce provider fragments so token-sized chunks cannot exhaust the
   session's event bound; preserve the first non-empty fragment promptly.
4. Implement and validate OpenAI-compatible SSE decoding for LM Studio text and
   chunked tool calls, while ignoring reasoning fields.
5. Advertise `token_streaming: true` only for LM Studio.
6. Normalize the same event name and payload through the TypeScript host.
7. Advance the unreleased package/API contracts together and update generated
   artifacts and integration documentation.

## Acceptance

- Ordered Unicode text fragments from LM Studio are observable before
  `session_completed`, and their concatenation for a round exactly matches the
  result text for that provider call.
- SSE framing may split UTF-8 and JSON arbitrarily without corrupting output;
  malformed, oversized, incomplete, refused, or mismatched responses fail
  explicitly.
- Streaming tool-call fragments are assembled and pass the same application
  validation as non-streaming calls. They are never exposed as text deltas.
- Cancellation or failure after a delta emits no completion and stops further
  output. Non-streaming adapters emit no text deltas.
- Structured-output invocations use the existing complete-result path.
- Runtime and host event cursors remain strictly monotonic and replayable.
- Python/OpenAPI/generated TypeScript/host contracts agree and the full
  repository gate passes.

## Dependencies

Builds on immutable `v0.4.0`. Finance Vault APP-007 is the first consumer. On
2026-09-10 the owner explicitly authorized the backend extension, release, and
matched Finance Vault pin needed to bring the approved APP-007 journey to
closure. Finance Vault closure remains separately gated on the owner's live UI
verification.

## Key files

- `src/local_agent_runtime/{contracts,ports,service}.py`
- `src/local_agent_runtime/adapters/{http_transport,chat_codec}.py`
- `src/local_agent_runtime/adapters/providers/lmstudio.py`
- `clients/typescript/src/host/{contracts,session-coordinator}.ts`
- `tests/test_{providers,runtime}.py`
- `clients/typescript/tests/host.test.mjs`

## Progress Notes

- 2026-09-09: Discovery confirmed that release `v0.4.0` streams lifecycle
  events only. LM Studio's existing loopback chat endpoint supports native SSE;
  the three CLI adapters need distinct, larger protocol work and remain
  truthfully non-streaming in this task.
- 2026-09-09: Implemented the optional streaming provider port, bounded and
  coalesced `assistant_text_delta` events, LM Studio SSE decoding, final-text
  reconciliation, structured-output fallback, and matching TypeScript host
  events on development versions `0.5.0` / API `1.4.0`. The full repository
  gate passed with 227 Python tests, one explicitly opt-in live test skipped,
  48 TypeScript tests, generated-contract comparison, documentation validation,
  and isolated Python/TypeScript artifact installation. No commit, push, tag,
  publication, or release was performed.
- 2026-09-09: Remediated independent release review findings by deriving a
  fixed per-call delta-event budget from the remaining output bound, reserving
  terminal-event capacity, making `round` monotonic across all provider calls,
  validating streaming request fields inside the input bound, closing provider
  streams promptly, and adding direct regression coverage for each corrected
  branch. The full repository gate passed with 233 Python tests, one explicitly
  opt-in live test skipped, 48 TypeScript tests, generated-contract comparison,
  documentation validation, and isolated Python/TypeScript artifact
  installation. A live small-model LM Studio invocation also passed. No commit,
  push, tag, publication, or release was performed.
- 2026-09-10: Hardened the release candidate with a token-, model-, frame-, and
  text-aware LM Studio transport bound; literal SSE line parsing that preserves
  raw U+0085/U+2028/U+2029 content; live LM Studio delta reconstruction evidence;
  a shared conformance delta fixture; explicit OpenRouter non-streaming
  coverage; and an exact-cap cancel/completion race fix. The full repository
  gate passed with 238 Python tests, one explicitly opt-in live test skipped, 48
  TypeScript tests, generated-contract comparison, documentation validation,
  and isolated Python/TypeScript artifact installation. No commit, push, tag,
  publication, or release was performed.
