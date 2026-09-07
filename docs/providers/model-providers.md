# Initial model providers

| Route | Transport | Processing | Credential source | Initial boundary |
|---|---|---|---|---|
| Codex | Trusted local CLI | External | Existing CLI authentication | Bounded isolated session; exact model when supported |
| Claude | Trusted local CLI | External | Existing CLI authentication | Bounded isolated session; exact model when supported |
| Grok | Trusted local CLI | External | Existing CLI authentication | Bounded isolated session and cleanup; exact model when supported |
| LM Studio | Loopback HTTP | Local | None by default | Explicit endpoint and model; reject non-loopback |
| OpenRouter | HTTPS API | External | Environment-backed secret reference | Explicit profile/model, egress permission, and effective upstream provenance |

## Qualification

Implementation proceeds through separate evidence levels:

1. configuration validates;
2. executable or endpoint is available;
3. authentication and health pass;
4. protocol and bounded capability tests pass;
5. a task-specific synthetic suite qualifies the exact profile; and
6. a consumer explicitly permits the route for its data and operation.

No earlier level implies a later one. The runtime exposes safe status and
provenance but does not decide whether a consumer's private data may leave the
machine.

## Secrets and diagnostics

Configuration stores references such as environment variable names, not
credential values. Provider requests and responses are content-bearing and do
not enter ordinary logs. Diagnostics may report provider/profile identity,
capabilities, health category, bounded timings, and safe usage totals.
