# Provider capability baseline

The initial release implements five reasoning routes behind one application
port while preserving their real differences.

| Capability | Required behavior |
|---|---|
| Provider-neutral profiles | Validate driver, model, processing class, limits, endpoint or command, and secret references through versioned configuration. |
| CLI routes | Run Codex, Claude, and Grok through isolated adapters with bounded input, output, time, environment, and cleanup. |
| Local reasoning | Restrict LM Studio to loopback and an explicitly selected model. |
| Hosted routing | Require an explicit OpenRouter profile, secret reference, egress consent, and effective-upstream provenance. |
| Embeddings | Keep LM Studio and OpenRouter embedding profiles independent from reasoning selection and enforce vector-space fingerprints. |
| Health | Distinguish installation, authentication, availability, compatibility, selection, and task qualification. |
| Reasoning effort | Publish only efforts an adapter delivers; refuse unsupported combinations before dispatch and snapshot each turn's choice. |
| Discovery | Enumerate models only where a provider offers a machine-readable catalog; report `supported: false` otherwise. |
| Fallback | Resolve an exact profile and fail explicitly; never switch provider or model silently. |
| Tools | Normalize requests while leaving authorization, validation, execution, and material approval to the consumer. |
| Provenance | Preserve configured and effective provider/model, processing class, timing, usage when available, and validation outcome. |

The runtime also provides bounded sessions, ordered normalized events,
cancellation and concurrency behavior, an authenticated loopback gateway, a
versioned OpenAPI contract, and a generated TypeScript client.

Adding another provider requires a concrete consumer need, truthful native
semantics, processing and credential policy, deterministic contract/security
tests, and separate qualification evidence.
