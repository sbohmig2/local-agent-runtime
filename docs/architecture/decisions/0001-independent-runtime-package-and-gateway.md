---
decision: 0001-independent-runtime-package-and-gateway
status: accepted
created: 2026-09-07
updated: 2026-09-07
---

# Independent runtime package and gateway

## Decision

Local Agent Runtime lives in its own repository. It produces:

- a Python package containing provider-neutral contracts and adapters;
- an optional authenticated loopback gateway for process and language
  independence; and
- a TypeScript client generated from the versioned gateway contract.

Consumer applications use pinned released versions. They do not import one
another, copy runtime source, use Git
submodules, or track a mutable branch.

Provider configuration is profile-based and deployment-local. The first
provider set is Codex CLI, Claude CLI, Grok CLI, LM Studio, and OpenRouter.
Credentials remain behind secret references. The public contract exposes
capabilities, safe health, session lifecycle, normalized events, tool requests,
and effective-route provenance without exposing provider-specific secrets or
raw process details.

Consumer products own tool authorization and execution. The runtime cannot
grant itself filesystem, process, network, MCP, database, or material
application authority.

## Rationale

Existing applications commonly accumulate useful but duplicated provider
adapters, while browser-facing clients benefit from a language-neutral local
API. An independent package prevents one consumer from becoming another's
dependency and provides one versioned source for future applications.

The optional gateway allows Node.js and other consumers to reuse the Python
implementation without duplicating provider logic. It does not become a shared
product database or a remote multi-tenant service.

## Consequences

- Existing consumer code may serve as reference evidence, never as a permanent
  private dependency. Each consumer migration remains separately owned.
- A consumer may keep its own Node host for browser and operating-system
  integration while delegating generic provider behavior through the gateway.
- Package and protocol compatibility require versioning and conformance tests.
- Public registry publication, hosted operation, automatic fallback, billing,
  and a provider marketplace remain deferred.

## 2026-09-07 distribution amendment

Public GitHub source and Release distribution use the MIT License because
authenticated private-release URLs are an unsuitable default package-manager
boundary. This changes distribution and
licensing only: consumers still pin immutable artifacts, TypeScript remains
private against accidental npm publication, and public registry publication
remains deferred.
