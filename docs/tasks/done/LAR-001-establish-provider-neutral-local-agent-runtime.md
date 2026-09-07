---
tracker: LAR-001
component: agent-runtime
status: done
priority: P0
effort: L
parallel_safe: no
created: 2026-09-07
updated: 2026-09-07
tags: [lar, runtime, providers, embeddings]
---

# LAR-001 - Establish the provider-neutral local agent runtime

**Status:** Done
**Priority:** P0 - establish the reusable core
**Effort:** L

## Goal

Deliver one independently versioned runtime with provider-neutral reasoning and
embedding contracts, provider-specific adapters, an authenticated loopback
gateway, and a generated TypeScript client.

## Delivered scope

- Separate adapters for Codex CLI, Claude CLI, Grok CLI, loopback LM Studio, and
  OpenRouter behind common reasoning ports.
- Separate LM Studio and OpenRouter text-embedding adapters with vector-space
  fingerprints and strict response validation.
- Deployment-local provider connections, reasoning profiles, embedding
  profiles, task routes, secret references, and explicit processing consent.
- Bounded sessions, ordered events, correlated tool requests/results,
  cancellation, concurrency limits, explicit errors, and no hidden fallback.
- Authenticated loopback HTTP gateway, OpenAPI `1.0.0`, generated TypeScript
  client, conformance fixtures, and isolated install/startup tests.

## Boundaries

Consumers own prompts, retrieval, facts, persistence, tools, authorization,
approvals, and user experience. Configuration does not expose raw credentials,
arbitrary executable selection, or unrestricted tool authority through portable
contracts. Health compatibility is not model/task qualification.

## Verification

Deterministic provider doubles, mock HTTP transports, subprocess fixtures,
security tests, strict typing, generated-contract checks, package builds, and
isolated gateway/client checks passed. Independent review found the delivered
scope ready to close. No live provider qualification is claimed.
