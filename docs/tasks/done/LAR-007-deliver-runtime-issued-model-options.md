---
tracker: LAR-007
component: agent-runtime
status: done
priority: P1
effort: M
parallel_safe: no
created: 2026-09-09
updated: 2026-09-09
tags: [lar]
---

# LAR-007 - Deliver runtime-issued model options

**Status:** Done
**Priority:** P1 - let consumers configure a provider model without owning provider catalogs
**Effort:** M

## Goal

Give backend consumers one provider-neutral, on-demand catalog of selectable
models for a configured reasoning profile and let a session execute one issued
choice without accepting an arbitrary browser-owned model or silently falling
back.

## Wins

A consumer can render real Codex, Claude, Grok, and LM Studio model choices from
one runtime contract. Provider discovery and maintained model knowledge remain in
the runtime, while the deployment still decides which exact models are qualified
for a task and which processing permissions apply.

## Lean evidence

- **Verdict:** `at risk` - Codex has a structured installed catalog, LM Studio
  has loopback discovery, and Claude has authoritative maintained identifiers.
  Grok exposes only a human-facing list, so its first contract must use a small
  maintained catalog rather than a brittle parser. The existing public session
  contract cannot execute a discovered model independently of its fixed profile.
- **Current evidence:** Finance Vault needs Settings to offer more than the one
  model pinned in a profile. Its current fixture already observes two LM Studio
  models but cannot select the second because Local Agent Runtime 0.2.1 accepts
  only `profile_id` and that profile's immutable model.
- **Smallest complete outcome:** add one on-demand model-options read per
  configured profile, return only exact deployment-qualified choices that the
  provider catalog currently supports, and accept one of those issued choices
  when creating a session. Prove two choices can be enumerated and the second is
  the exact requested model.
- **Necessary complexity:** strict provider-specific catalog adapters; exact
  model/task qualification; per-model reasoning metadata; bounded process and
  HTTP discovery; deterministic stale/unavailable refusal; generated OpenAPI and
  TypeScript client parity; exact requested-model provenance.
- **Deferred:** consumer UI and persistence, arbitrary model strings, browser
  catalog ownership, automatic qualification, continuous polling, model
  download/load/eject, OpenRouter enumeration, multi-connection product UX,
  fallback, public registries, signing, and hosted distribution. A concrete
  consumer need and provider evidence are required before promoting any variant.

## Runtime gates

- A model option belongs to one configured profile and provider connection; it
  does not replace either identity.
- Codex, Claude, and Grok remain external-processing routes. LM Studio remains
  local only through its validated loopback endpoint.
- Catalog membership or local discovery never grants task qualification. A
  selectable model is covered by either an explicit catalog-wide task policy or
  a narrower exact-model task declaration owned by the deployment.
- Browser callers can submit only an option previously defined by runtime
  configuration and still present in the provider catalog. Commands, endpoints,
  credentials, permissions, limits, and task claims remain non-editable.
- Catalog subprocesses and HTTP calls are bounded, content-free, and emit no
  credential, prompt, or provider response in errors or ordinary diagnostics.
- Missing, stale, malformed, incompatible, or removed choices fail explicitly.
  No provider or model fallback is introduced.
- A session snapshots the derived exact model and per-model reasoning choice;
  continuation keeps that snapshot.
- Existing profile-only clients remain compatible when they omit a model option.
- Python gateway, generated OpenAPI, root TypeScript client, and backend host
  types carry equivalent meaning.
- No consumer state, UI code, marketplace, billing, or hosted control plane is
  added.

## Scope

- Add strict profile model-option policy. `catalog_model_tasks` explicitly
  qualifies the runtime-issued reasoning catalog for named tasks without making
  a consumer copy provider model IDs. A deployment that needs narrower evidence
  can instead use exact `model_options`, where each model owns its task
  qualifications and optional reasoning efforts/default. The modes cannot mix.
- Add an on-demand `GET /v1/profiles/{profile_id}/model-options` contract. It
  performs one bounded catalog check and returns supported state, check time,
  safe detail code, and currently selectable runtime-issued options.
- Add an optional `model_option_id` to session creation. Re-enumerate and resolve
  it before accepting the session, require task qualification when `task_code`
  is supplied, then snapshot an exact derived model profile.
- Codex reads its structured app-server `model/list` protocol and intersects
  provider-reported effort metadata with the runtime vocabulary.
- Claude uses the maintained catalog `claude-fable-5-1`, `claude-opus-5`,
  `claude-sonnet-5`, and `claude-haiku-4-5-20251001`, with distinct display
  names. The first three expose low/medium/high/xhigh/max with high defaults;
  Haiku exposes no effort control. Update this runtime-owned catalog when
  Anthropic changes the supported Claude Code lineup.
- Grok uses a maintained catalog for `grok-4.6` and `grok-4.5`; its human-facing
  `grok models` output is not parsed as a stable public contract.
- LM Studio combines compatible and native loopback catalogs, excludes embedding
  and unknown-kind models from reasoning choices, and preserves loaded state
  when the native response establishes it.
- Claude and Grok maintained catalogs are provider availability knowledge, not
  authentication or task qualification.

## Acceptance

- A qualified Codex profile exposes two or more installed structured catalog
  choices with display names and model-specific effort/default metadata.
- Claude exposes the four maintained model choices using accepted exact CLI
  identifiers; Grok exposes its two maintained choices.
- LM Studio exposes only native `llm` entries and never offers an embedding model
  as a reasoning choice.
- Unqualified discovered or maintained models do not appear as selectable
  options.
- Choosing a non-default issued option starts the configured provider with that
  exact model and records it as requested provenance.
- A missing or removed option fails before provider invocation and never falls
  back.
- A profile-only session request behaves exactly as before.
- Contract generation, client tests, provider tests, configuration tests,
  gateway tests, security tests, and the full repository gate pass.

## Test cases

- Codex JSON-RPC initialization, pagination, hidden entries, provider efforts,
  unsupported `ultra`, malformed frames, nonzero exit, timeout, and output bound.
- Claude exact maintained IDs/names/efforts and Grok exact maintained IDs/names.
- LM Studio zero/one/many LLMs, mixed LLM/embedding data, loaded/unloaded state,
  absent or malformed native metadata, duplicate IDs, and bounded catalogs.
- Zero/one/many qualified intersections; an observed-but-unqualified model stays
  absent.
- Invalid configuration for duplicate/unknown model IDs, invalid tasks, invalid
  efforts, defaults outside the declared/provider-supported set, and an option
  equal to an unsafe command-like string.
- Gateway auth, unknown profile, unavailable catalog, unknown option, option
  removed between reads, task mismatch, reasoning mismatch, exact-model dispatch,
  continuation snapshot, and profile-only compatibility.
- Generated Python/OpenAPI/TypeScript conformance and clean artifact builds.

## Dependencies

LAR-006 remains independently in review. LAR-007 builds from released 0.2.1 but
does not modify, republish, or close that review lane. The owner authorized the
matched `0.3.0` artifact release and Finance Vault consumer-pin promotion after
the implementation and repository gates pass.

## Key files

- `src/local_agent_runtime/contracts.py` - profile, discovery, and session model identity.
- `src/local_agent_runtime/configuration.py` - deployment-qualified model options.
- `src/local_agent_runtime/adapters/providers/` - provider-owned catalogs.
- `src/local_agent_runtime/service.py` - option projection and atomic session resolution.
- `src/local_agent_runtime/gateway.py` and `api_contract.py` - versioned HTTP contract.
- `clients/typescript/` - generated root client and backend host parity.
- `docs/architecture/provider-adapters.md` - permanent discovery and qualification rules.

## Progress Notes

- 2026-09-09: Owner authorized the independent runtime change. Local inspection
  found Codex 0.153.4 exposes structured `model/list`; Grok 1.0.13 exposes only a
  human-facing list; LM Studio exposes typed native model metadata; Claude Code
  2.1.266 accepts exact model IDs but exposes no stable enumeration command.
  Authoritative Anthropic documentation establishes `claude-fable-5-1`,
  `claude-opus-5`, and `claude-sonnet-5`, all with low through max effort and
  high as default. Implementation is authorized, but commit, push, publication,
  release, consumer edits, and closure are not.
- 2026-09-09: Implemented unreleased API 1.2.0 with on-demand opaque model
  options, catalog-wide or exact deployment qualification, atomic option
  re-resolution at session creation, and requested option/model provenance.
  Codex uses bounded structured pagination; Claude and Grok use maintained
  catalogs; LM Studio intersects compatible IDs with native `llm` type data.
  Generated OpenAPI, root client, Node host forwarding, and the authenticated
  host HTTP adapter carry the same meaning. The regression matrix proves one
  profile can expose multiple runtime-issued choices, dispatch the non-default
  choice exactly, and refuse it after removal without fallback.
- 2026-09-09: `uv run python scripts/check.py` passed with 210 Python tests plus
  47 TypeScript tests, strict typing/lint/formatting, generated-contract checks,
  documentation validation, package-layout validation, and clean isolated wheel
  installation. One explicitly opt-in live-provider test remains skipped. No
  commit, publication, release, consumer edit, or task closure was performed.
- 2026-09-09: The owner authorized release and Finance Vault pin promotion.
  Prepared matched package `0.3.0` / gateway API `1.2.0` metadata and release
  notes. Publication remains contingent on a clean reviewed source commit,
  immutable artifact construction, and remote checksum verification.
- 2026-09-09: Independent Claude review initially blocked release on four
  findings. The corrected snapshot treats effort support as order-independent,
  distinguishes known no-effort models from unknown effort support, authorizes
  model-option reads before query handling, and hides disabled profiles from
  catalog enumeration. Claude re-ran each reproduction, marked `CR-001` through
  `CR-004` resolved, found no new blocker, and returned `READY TO RELEASE`
  contingent on the clean artifact build and checksum verification.
- 2026-09-09: Published immutable GitHub release `v0.3.0` from source commit
  `08f1999016e48dc8a71c741d91155e1c7b846b67`. Anonymous downloads verified
  against `SHA256SUMS`: TypeScript client
  `6718cd815bf0e316da90a3895d0acd279e204a22e811ae13efbe0cce41263425`,
  Python wheel
  `562341b87e7310441d89b127c7a7ddae271288f6a5782118440450db75109711`,
  and source distribution
  `199f55e8dd3a5db6ca96948700301512d29f0bca7ef4b2550c5d5150af92741e`.
  GitHub reports the release immutable. The task remains in review while the
  explicitly authorized Finance Vault consumer-pin promotion is verified.
- 2026-09-09: Closed after the released model-option contract was carried
  forward into immutable `v0.4.0` from source commit
  `b45397a6e139451ccfc8eb76ad8f02d773cd73cd`, and Finance Vault pinned the
  matched Python and TypeScript `0.4.0` artifacts with HTTP API `1.3.0` in
  APP-005 commit `54bb510ef34a6df876cd0a170c6277b757801216`. The complete Finance Vault
  consumer gate and independent UI review passed; LAR-006 remains separately in
  review.
