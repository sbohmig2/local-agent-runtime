---
tracker: LAR-008
component: agent-runtime
status: review
priority: P1
effort: S
parallel_safe: no
created: 2026-09-09
updated: 2026-09-09
tags: [lar]
---

# LAR-008 - Expose provider-native web search

**Status:** Review
**Priority:** P1 - Finance Vault needs external CLI agents to retain their native public-web capability
**Effort:** S

## Goal

Allow the Codex, Claude, and Grok CLI adapters to use only their provider-native
public-web search/fetch capability, subject to the installed CLI account and
administrator policy, and report that route capability through the public
profile contract.

## Boundary

- `provider_native_web` means provider-native public-web search and page retrieval inside
  the external CLI turn. It does not grant raw network sockets to model-issued
  code.
- Shell/command execution, filesystem mutation, computer or browser control,
  image generation, subagents, user/project instructions, plugins, inherited
  MCP servers, and arbitrary native tools remain disabled.
- The consuming product still owns its supplied tool catalog, tool execution,
  financial write authorization, and UX. This runtime stores no consumer choice.
- Provider, account, organization, and managed-device policy remain authoritative.
  A route can advertise adapter support while an individual request can still
  fail explicitly when upstream entitlement or policy refuses web use.
- LM Studio and OpenRouter do not claim this native capability. A consumer may
  separately broker a bounded web tool through the existing tool-call contract.

## Scope

1. Add additive boolean `provider_native_web` capability metadata to the public profile
   contract and generated clients.
2. Enable Codex live web search while retaining the deny-root execution profile
   and all non-web feature disables.
3. Allow only `WebSearch` and `WebFetch` for Claude while retaining safe mode,
   no settings sources, strict empty MCP configuration, and noninteractive
   permissions.
4. Allow only `web_search` and `web_fetch` for Grok while retaining its private
   HOME, disposable session, no subagents, and noninteractive permissions.
5. Prove exact argument allowlists and capability truthfulness. Add opt-in live
   provider evidence without making it part of the deterministic repository gate.

## Acceptance

- The three CLI adapters report `provider_native_web: true`; LM Studio and OpenRouter
  report false.
- CLI invocations retain all existing isolation controls and enable only the
  provider-native web tools named above.
- No consumer-supplied tool, MCP server, shell, filesystem mutation, subagent,
  browser automation, or computer-control capability is added.
- Public Python/OpenAPI/TypeScript contracts remain equivalent and the full
  repository gate passes.
- At least one attended live CLI turn proves current public-web retrieval before
  release. Unsupported account or administrator policy fails explicitly rather
  than silently falling back.

## Dependencies

Builds on the released `v0.3.0` model-option contract. Finance Vault APP-005 is
the first consumer. The owner authorized the matched `v0.4.0` release, Finance
Vault pin promotion, and closure after the clean release and consumer gates pass.

## Key files

- `src/local_agent_runtime/contracts.py`
- `src/local_agent_runtime/adapters/providers/{codex,claude,grok}.py`
- `src/local_agent_runtime/api_contract.py`
- `tests/test_providers.py`
- `clients/typescript/src/generated.ts`

## Progress Notes

- 2026-09-09: Stephan approved native web access for CLI agents and a read-only
  capability presentation in Finance Vault Settings. The implementation is
  intentionally narrower than inheriting every user-configured CLI tool.
- 2026-09-09: Live Codex Terra, Claude Sonnet, and Grok 4.5 turns retrieved named
  public pages through their native web tools. The shared CLI envelope was
  corrected so provider-native web may run internally without being confused
  with or returned as a consumer-supplied tool call. Grok's installed built-in
  sandbox profiles refuse to start on this Mac because `/var/run/docker.sock`
  resolves through a symlink; the adapter therefore retains its prior
  private-home and exact-tool allowlist boundary instead of adding a
  machine-breaking profile. Its native-web progress/final concatenation is
  accepted only when every object validates and no intermediate application
  tool request would be discarded.
- 2026-09-09: Deterministic validation passed with 214 Python tests, one
  explicitly opt-in live test skipped, 47 TypeScript tests, strict linting and
  typing, generated-contract comparison, documentation validation, release
  artifact construction, and isolated package installation. A separate
  read-only reviewer found no release-blocking provider, contract, or consumer
  presentation issue before publication.
