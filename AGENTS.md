# Repository agent instructions

## Start here

Read [docs/README.md](./docs/README.md), the
[product vision](./docs/product/vision.md), and the relevant architecture and
provider documents before substantive work.

Use the [operating philosophy](./docs/operating-philosophy.md) as a north star
and the [decision framework](./docs/decision-framework.md) when comparing
directions.

Treat an active task as a flexible working contract. Absorb discoveries that
strengthen its agreed outcome without crossing a material product, authority,
security, provider, cost, dependency, public-contract, effort, or sequencing
boundary. Ask the repository owner before crossing one of those boundaries.

## Runtime gates

- Keep provider connections, model profiles, task routes, and product sessions
  distinct.
- A locally installed CLI is not proof of local inference. Codex, Claude, Grok,
  and OpenRouter are external-processing routes unless verified otherwise.
  LM Studio is local only when its endpoint is restricted to loopback.
- Provider health and protocol compatibility do not establish that a model is
  qualified for a task. Record qualification separately.
- Keep provider and model choice deployment-local. Portable callers request
  capabilities or named tasks rather than raw commands, endpoints, or secrets.
- Never place credentials in source, browser state, URLs, logs, exceptions,
  fixtures, generated clients, or ordinary diagnostics. Store only secret
  references in configuration.
- Do not silently fall back to another provider or model. Unavailable,
  unauthenticated, incompatible, refused, timed-out, and canceled outcomes are
  explicit.
- Provider switches must not forward retained conversation or hidden state to a
  different provider without an explicit product-owned decision.
- The runtime may request tool calls, but the consuming product owns tool
  availability, authorization, validation, execution, and material approval.
- Never expose an unrestricted shell, filesystem, process, network, MCP server,
  or product database through the generic runtime.
- Preserve exact provider, adapter, requested and effective model, processing
  class, times, limits, usage when available, and validation outcome.
- Keep input, output, tool, timeout, cancellation, concurrency, and redaction
  bounds deterministic and testable.
- The shared package owns no consumer facts, evidence, or application state.
- Consumers pin versioned public contracts. Do not couple repositories through
  private imports, mutable branches, copied source, or Git submodules.

## Architecture direction

Begin with a small provider-neutral core and optional local gateway:

    product clients -> public runtime contract -> application
    provider adapters -------------------------> application ports
    bootstrap ---------------------------------> concrete wiring

Domain and application code must not depend on a provider SDK, HTTP framework,
CLI process implementation, consumer repository, or environment-specific
configuration. Adapters implement those boundaries.

## Documentation authority

- Product meaning: docs/product/
- System behavior and decisions: docs/architecture/
- Permanent capability ownership: docs/architecture/components/
- Provider-specific constraints: docs/providers/
- Immutable artifact contents and limits: docs/releases/

Link to canonical owners instead of restating them. Keep exploration separate
from accepted direction.

## Validation

Run after code or documentation changes:

    uv run python scripts/check.py

Report exact commands and results. Do not commit, push, publish, release, or
modify consumer repositories unless the repository owner explicitly asks.
