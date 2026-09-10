---
component: agent-runtime
prefix: LAR
status: active
initiatives:
  - reusable-agent-runtime
updated: 2026-09-09
---

# Agent runtime

## Purpose

Own the provider-neutral application boundary, provider adapters, deployment
profiles, bounded sessions, normalized progress and tool requests, safe
provenance, and optional authenticated loopback gateway.

It also owns the separate [embedding capability](../embeddings.md): configured
vector spaces, bounded text batches, compatibility fingerprints and vector
validation. It does not own chunking, indexes or retrieval.

It does not own a consumer's evidence, domain facts, retrieval, workflows,
tools, approvals, or user interface. The TypeScript host entry point is
backend-only. React components, pages, HTML, browser-side model code, charts,
chips, render instructions, and product action semantics are intentionally
consumer-owned and must not be added to this package.

## Direction

- Provide one Python package and optional local process from this repository.
- Generate a TypeScript client from the versioned gateway contract and expose
  optional Node-only host infrastructure through an additive subpath.
- Keep product clients on stable runtime concepts rather than provider SDK
  payloads.
- Model provider choice as configured profiles. The runtime does not expose an
  unconstrained marketplace or arbitrary executable/endpoint selector.
- Let consumer applications register bounded tool capabilities and execute them
  through their own authorization layer.
- Forward application-owned structured schemas as data-validation contracts,
  never as UI schemas or authorization to perform an action.
- Preserve truthful capability differences. Token streaming, structured output,
  tool requests, model enumeration, and session continuation may differ by
  adapter and must be reported rather than emulated dishonestly.
- Keep provider selection and secrets out of portable consumer data.
- Own the fixed supported-adapter inventory, explicit installation probes, and
  private enablement of operator-approved configured options. Consumers render
  that catalog without duplicating executable discovery or accepting credentials.

## Tasks

- LAR-010 — done — [Harden LM Studio request compatibility](../../tasks/done/LAR-010-normalize-lm-studio-grammar-schemas.md)
- LAR-009 — done — [Stream bounded assistant text](../../tasks/done/LAR-009-stream-bounded-assistant-text.md)
- LAR-008 — done — [Expose provider-native web search](../../tasks/done/LAR-008-expose-provider-native-web-search.md)
- LAR-007 — done — [Deliver runtime-issued model options](../../tasks/done/LAR-007-deliver-runtime-issued-model-options.md)
- LAR-006 — review — [Keep long-running event streams alive](../../tasks/LAR-006-keep-long-running-event-streams-alive.md)
- LAR-005 — done — [Model selection and reasoning controls](../../tasks/done/LAR-005-deliver-model-selection-and-reasoning-controls.md)

- LAR-004 — done — [Deliver the reusable TypeScript host toolkit](../../tasks/done/LAR-004-deliver-reusable-typescript-host-toolkit.md)
- LAR-003 — done — [Publish the typed Python distribution](../../tasks/done/LAR-003-publish-typed-python-distribution.md)
- LAR-002 — done — [Release initial consumer artifacts](../../tasks/done/LAR-002-release-initial-consumer-artifacts.md)
- LAR-001 — done — [Establish the provider-neutral local agent runtime](../../tasks/done/LAR-001-establish-provider-neutral-local-agent-runtime.md)
