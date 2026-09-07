# Provider adapters and extension points

The application depends on two separate structural interfaces: ProviderPort
for reasoning and EmbeddingProviderPort for embeddings. Selection persistence is
also an injected port. Only bootstrap/registry code wires concrete adapters.

Each provider owns its policy in adapters/providers:

- codex.py: trusted CLI invocation and ephemeral deny-root restrictions;
- claude.py: safe-mode invocation, authentication parsing and native wrapper;
- grok.py: private HOME/auth-only copy and explicit disposable-session cleanup;
- lmstudio.py and lmstudio_embeddings.py: local reasoning and embedding behavior;
- openrouter.py and openrouter_embeddings.py: hosted reasoning and embeddings;
  openrouter_policy.py shares that provider's credential/upstream policy.

Shared HTTP/process modules implement bounded I/O, timeouts and cancellation.
Shared codecs implement genuinely shared wire formats. They do not select a
provider or branch on a driver name. Application messages and tool requests are
provider-neutral; only codecs translate them into CLI or compatible HTTP payloads.

The CLI envelope uses JSON-encoded argument strings internally so arbitrary
consumer tool schemas do not require an unrestricted native structured-output
object. The application decodes and validates the actual argument object against
the registered schema. Native CLI host tools remain disabled. Structured final
responses are locally schema-validated; compatibility does not imply that every
model will successfully produce them.

To add a provider, implement the relevant port(s), register their factories,
define validated connection/processing/credential rules, document provider
constraints, and add deterministic contract/security tests. The application
services and consumer contracts should not acquire provider branches. No plugin
discovery framework or shared consumer database is needed.

The registry's explicit allowlist is a deliberate trust boundary, not a generic
HTTP fallback. Separate Python packages may inject a provider factory; deployment
configuration still needs an explicit validated provider definition.

Token streaming is currently false: gateway SSE carries normalized lifecycle
events and completed output, not invented token deltas. Grok authentication is
reported inconclusive when no reliable content-free probe is available. CLI
effective model remains unknown unless native output establishes it. Configured
qualified-task labels are owner assertions, not qualification performed by the
runtime. These distinctions must remain visible in consumer settings.

`token_limit_control` is true for HTTP routes that receive `max_tokens` and
false for CLI routes that do not expose an equivalent verified control.
Character/output-byte and time bounds apply to every route. A CLI profile's
token preference must not be presented as an enforced token budget.
