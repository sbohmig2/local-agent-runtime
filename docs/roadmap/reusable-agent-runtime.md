---
initiative: reusable-agent-runtime
status: active
created: 2026-09-07
updated: 2026-09-07
components:
  - agent-runtime
---

# Reusable agent runtime

## Goal

Deliver one independently versioned runtime that multiple applications can use
to select and invoke supported agent/model routes without duplicating provider
and safety code.

## Finite outcome

1. Establish the package, public runtime contract, provider profiles, and
   bounded configuration model.
2. Support Codex CLI, Claude CLI, Grok CLI, loopback LM Studio, and OpenRouter
   through explicit adapters.
3. Provide safe health, exact profile selection, normalized session events,
   tool requests, cancellation, errors, and route provenance.
4. Provide an authenticated loopback gateway and generated TypeScript client.
5. Prove deterministic adapter behavior and isolated synthetic end-to-end
   operation without consumer data or live product writes.
6. Provide reusable Node host primitives once two independent consumers
   demonstrate the same process, MCP, session, and local HTTP integration need.

Consumer integration remains owned by each consuming repository. Completion
does not publish to a public package registry, migrate a consumer, add a
marketplace, or authorize provider egress with real data.

## Sequence

LAR-001 established the implementation. LAR-002 and LAR-003 published reviewed,
immutable consumer artifacts. LAR-004 adds the independently earned TypeScript
host toolkit. Registry publication remains deferred.
