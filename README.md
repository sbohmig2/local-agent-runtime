# Local Agent Runtime

Local Agent Runtime is a reusable, provider-neutral runtime for local-first
applications. It exposes locally installed Codex, Claude, and Grok
clients, a genuinely local LM Studio route, and an explicitly configured
OpenRouter route behind one bounded application contract.

Product direction, architecture, provider boundaries, and executable work live
under [docs/](./docs/README.md). The approved pickup order lives in
[next-up.md](./docs/tasks/next-up.md).

The first implementation is complete under LAR-001. It includes five reasoning
adapters and separate LM Studio/OpenRouter text embedding adapters. Deterministic
tests and independent review have passed; they are not live-provider
qualification or release approval.

## Development

The project uses Python 3.13, uv, and Node.js 22+:

    uv sync
    npm --prefix clients/typescript ci --ignore-scripts
    uv run python scripts/check.py

An owner-authorized release is built from a clean checkout with
`uv run python scripts/build_release.py`. It produces matching Python and
TypeScript artifacts plus `dist/SHA256SUMS`; see the
[v0.1.1 release notes](./docs/releases/v0.1.1.md).

## Local gateway

Copy [the example configuration](./config/runtime.example.yaml) to a deployment-local
file. Replace model IDs, embedding dimensions/prefixes, and the OpenRouter
upstream with exact values. Configure the gateway token through the
LOCAL_AGENT_RUNTIME_TOKEN environment variable (at least 32 random characters).
OpenRouter uses an environment-backed credential reference, never an inline key.

    uv run local-agent-runtime serve --config config/runtime.local.yaml --state-root .runtime-state

The gateway defaults to IPv4 loopback; `--host ::1` selects IPv6. Its bearer token
grants access to every session in that runtime instance. Keep it in the consumer's
backend, never browser storage. Use separate instances/state directories/tokens
for products requiring isolation. Conversations are memory-only and disappear on
restart; selection is the only persisted setting. Health requests with provider
probing are explicit, not part of ordinary profile inspection.

Python and Node consumption, artifact pinning, and per-product process isolation
are documented in [Consumer integration](./docs/integration.md).

Use `/v1/embedding-profiles` and `/v1/embeddings` for embeddings, independently of
chat selection. The generated TypeScript client exposes `embeddingProfiles()`
and `embed()`. See [embedding contracts](./docs/architecture/embeddings.md) for
index compatibility and [provider architecture](./docs/architecture/provider-adapters.md)
for extension points.

Do not publish, release, push, or integrate a consumer project without explicit
owner authorization.

## License and security

Local Agent Runtime is available under the [MIT License](./LICENSE). Report
suspected vulnerabilities through the private process in
[SECURITY.md](./SECURITY.md), not a public issue.
