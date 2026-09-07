---
component: agent-runtime
prefix: LAR
status: active
initiatives:
  - reusable-agent-runtime
updated: 2026-09-07
---

# Agent runtime

## Purpose

Own the provider-neutral application boundary, provider adapters, deployment
profiles, bounded sessions, normalized progress and tool requests, safe
provenance, and optional authenticated loopback gateway.

It also owns the separate [embedding capability](../embeddings.md): configured
vector spaces, bounded text batches, compatibility fingerprints and vector
validation. It does not own chunking, indexes or retrieval.

It does not own a consumer's evidence, financial facts, retrieval, workflows,
tools, approvals, or user interface.

## Direction

- Provide one Python package and optional local process from this repository.
- Generate a TypeScript client from the versioned gateway contract.
- Keep product clients on stable runtime concepts rather than provider SDK
  payloads.
- Model provider choice as configured profiles. The runtime does not expose an
  unconstrained marketplace or arbitrary executable/endpoint selector.
- Let consumer applications register bounded tool capabilities and execute them
  through their own authorization layer.
- Preserve truthful capability differences. Token streaming, structured output,
  tool requests, model enumeration, and session continuation may differ by
  adapter and must be reported rather than emulated dishonestly.
- Keep provider selection and secrets out of portable consumer data.

## Tasks

- LAR-003 — in progress — [Publish the typed Python distribution](../../tasks/LAR-003-publish-typed-python-distribution.md)
- LAR-002 — done — [Release initial consumer artifacts](../../tasks/done/LAR-002-release-initial-consumer-artifacts.md)
- LAR-001 — done — [Establish the provider-neutral local agent runtime](../../tasks/done/LAR-001-establish-provider-neutral-local-agent-runtime.md)
