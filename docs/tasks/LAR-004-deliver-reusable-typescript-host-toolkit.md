---
tracker: LAR-004
component: agent-runtime
status: review
priority: P0
effort: L
parallel_safe: no
created: 2026-09-07
updated: 2026-09-07
tags: [lar]
---

# LAR-004 - Deliver the reusable TypeScript host toolkit

**Status:** Review
**Priority:** P0 - two independent consumers need the same secure local host boundary
**Effort:** L

## Goal

Add a provider-neutral Node host toolkit to the released TypeScript client so
consumer applications can supervise the Python gateway, broker bounded MCP tool
sessions, and expose an authenticated browser-safe local API without copying
generic host code.

## Wins

Each application keeps its prompts, tools, permissions, approvals, and user
experience while reusing the same tested process, session, MCP, event, and local
HTTP mechanics. A provider or host-protocol correction is released once rather
than reimplemented by every consumer.

## Lean evidence

- Verdict: pass. Two current consumers require the same mechanics, while the
  existing release stops at the raw gateway client and would leave substantial
  generic code in each application.
- Current evidence: the first integration produced a large host layer whose
  process supervision, MCP catalog, bounded session coordination, and secure
  HTTP behavior contain no consumer-domain logic.
- Smallest complete outcome: one additive `@local-agent-runtime/client/host`
  entry point with injected product policy and a clean-install integration proof.
- Necessary complexity: Node process lifecycle, literal loopback enforcement,
  secret isolation, bounded MCP/session/event handling, cancellation, exact
  runtime compatibility, and fail-closed policy ports.
- Deferred: durable conversations, remote hosting, generic plugins,
  automatic provider fallback, product configuration formats, and package
  registry publication.
- UI boundary: React components, pages, HTML, browser-side model code, charts,
  chips, rendering hints, and product action semantics are intentionally
  consumer-owned and must not enter this repository. Consumer-owned structured
  schemas validate data only.

## Runtime gates

- Provider and profile identity remain the Python runtime's contract; the host
  maps them without exposing adapter, command, endpoint, or credential details.
- Local/external processing and requested/effective route metadata remain
  truthful. Consumer-supplied processing policy decides whether content may be
  submitted.
- Runtime tokens and product credentials remain backend-only. The supervisor
  accepts an explicit environment exclusion list and creates a random runtime
  token in spawn mode.
- Health, compatibility, and qualification stay distinct.
- Sessions, retained events, prompts, MCP services, MCP catalogs, tool calls,
  tool rounds, active duration, startup, cancellation, restart, and timeouts are
  bounded and tested.
- The host never decides which product tool is safe. No tool executes unless an
  injected product authorizer permits it and its exact name is deployment
  allowlisted.
- Requested/effective model and upstream provenance pass through unchanged.
- Python, gateway, root TypeScript client, and host entry point advance together
  in one immutable release.
- No consumer vocabulary, database, workflow, marketplace, fallback, billing,
  tenancy, or hosted control plane enters the shared package.

## Scope

1. Add Node-only runtime supervision with literal-loopback validation, random
   spawn token, exact package/API compatibility, coalesced startup, bounded
   restart, timeout, and shutdown.
2. Add a bounded MCP stdio catalog using the official client. Require explicit
   server identities and consumer-supplied server specifications; expose no
   arbitrary browser configuration or default product tools.
3. Add a provider-neutral session coordinator. Inject trusted product
   instructions, processing policy, tool authorization, and optional
   consumer-owned structured-output schemas; retain bounded normalized events
   and terminal sessions without durable consumer state.
4. Add an authenticated local HTTP/SSE adapter with exact Host and Origin
   validation, bounded bodies, stable sanitized errors, and no raw runtime or
   provider configuration in browser responses.
5. Export the toolkit through `@local-agent-runtime/client/host` while preserving
   the existing root import. Extend package-layout, clean-install, license,
   secret/path, and compatibility validation for the additive entry point.
6. Document the consumer composition boundary and migrate the generic
   conformance tests into this repository. Consumer-specific policy and
   integration tests remain in each consumer.

## Acceptance

1. A clean install of one released TypeScript tarball can import both
   `@local-agent-runtime/client` and `@local-agent-runtime/client/host` without a
   repository checkout or unpublished dependency.
2. The supervisor rejects non-literal-loopback addresses and incompatible
   runtime versions, coalesces concurrent startup, strips named secrets, allows
   only a bounded restart, and shuts down its owned process.
3. The MCP catalog rejects duplicate identities and oversized catalogs, closes
   partial startup, preserves structured product results, and exposes commands,
   paths, environment, and instructions only to backend composition code.
4. The coordinator lists provider-neutral profiles, preserves route provenance,
   applies injected processing policy before content submission, executes only
   doubly permitted tools, normalizes ordered events, and bounds sessions,
   retention, expiry, continuation, cancellation, and stale cursors.
5. The browser adapter requires a strong bearer token, exact Host and allowed
   Origin, safe CORS preflight, bounded exact request bodies, and returns only
   stable error codes and product-safe profile/session/event fields.
6. No consumer-domain name, prompt, tool policy, database setting, or approval
   rule exists in the shared implementation or release documentation.
7. The host's supervised runtime forwards the separately modeled embedding
   profile and embedding operations without taking ownership of indexes,
   retrieval, persistence, or UI.
8. Repository checks, isolated artifact checks, and independent review pass.
   Publishing, release creation, and consumer migrations remain separately
   owner-gated.

## Test cases

Root and host subpath imports; five profiles through one contract; synthetic MCP
tool cycle; denied processing; denied and permitted tool requests; provider
failure; malformed and unknown tool request; cancellation during tool work;
continuation failure; session capacity and expiry; retained/stale event cursor;
invalid Host, Origin, bearer, body, and preflight; duplicate and oversized MCP
catalog; missing executable; startup timeout; incompatible version; concurrent
startup; named-secret exclusion; crash/restart bound; clean tarball install;
release membership, license, secret, and developer-path canaries.

## Dependencies

LAR-003 supplied the typed immutable `v0.1.2` baseline.

## Key files

- `clients/typescript/src/host/` - reusable Node host primitives.
- `clients/typescript/src/` - existing gateway client and generated contract.
- `docs/integration.md` - consumer-owned composition boundary.
- `scripts/build_release.py` - immutable artifact construction and isolation.

## Progress Notes

- 2026-09-07: The owner confirmed that more than one consumer will use the
  component and asked that reusable host code move into this repository. The
  additive host subpath keeps product policy injected and consumer-owned.
- 2026-09-07: The host toolkit was implemented as backend-only infrastructure.
  It composes with the existing reasoning and embedding gateway contract while
  leaving all consumer UI and domain behavior outside the package.
- 2026-09-07: The complete repository gate passes with 123 Python tests and 19
  Node tests. Clean isolated wheel, source-distribution rebuild, and TypeScript
  tarball installation verify both the root client and additive `/host` import.
  LAR-004 entered independent review; publication and consumer migration remain
  owner-gated.
- 2026-09-07: Initial Claude Opus/high and Grok review found lifecycle,
  continuation-policy, MCP trust/cancellation/bounds, concurrent-capacity, and
  SSE shutdown gaps. Remediation keeps MCP instructions attributed and outside
  the trusted prompt by default, enriches the injected tool-policy context, and
  adds explicit loop, connection, result, restart, and shutdown bounds. Fresh
  independent review is required after the expanded tests pass.
- 2026-09-07: A fresh Claude review found that repeated tool-request rounds and
  a provider stream whose next event never resolves still needed independent
  host bounds. The coordinator now limits tool rounds, yields between rounds,
  disposes per-round abort resources, and applies an abortable maximum active
  session duration. Claude's next pass identified cancellation-recovery and
  iterator-cleanup edges while those changes were still moving; every runtime
  cancel is now bounded, terminal state is recorded before best-effort cleanup,
  and rejecting or throwing iterator cleanup cannot escape. The expanded suite
  passes with 123 Python and 34 Node tests, including clean isolated wheel,
  sdist, and TypeScript tarball installs. Grok's fresh review was READY; one final
  Claude verification remained required against the frozen snapshot.
- 2026-09-07: Final Claude Opus/high review of the frozen snapshot returned
  READY after independently probing hanging cancellation, timeout and approval
  settlement, iterator cleanup, cumulative tool-round bounds, event ordering,
  concurrency, and absence of UI or provider-specific host code. Grok's fresh
  review is also READY. Commit, immutable `v0.1.3` publication, and task closure
  remain owner-gated.
