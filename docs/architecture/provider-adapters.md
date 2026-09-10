# Provider adapters and extension points

The application depends on two separate structural interfaces: ProviderPort
for reasoning and EmbeddingProviderPort for embeddings. Selection persistence is
also an injected port. Only bootstrap/registry code wires concrete adapters.

Each provider owns its policy in adapters/providers:

- cli_base.py: shared process lifecycle and trusted-executable resolution;
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
the registered schema. Native shell, filesystem mutation, MCP inheritance,
subagent, browser-automation, and computer-control tools remain disabled. Codex,
Claude, and Grok explicitly allowlist only their provider-native public-web
search/fetch capability; provider account and administrator policy remain
authoritative. Structured final responses are locally schema-validated;
compatibility does not imply that every model will successfully produce them.

To add a provider, implement the relevant port(s), register their factories,
define validated connection/processing/credential rules, document provider
constraints, and add deterministic contract/security tests. The application
services and consumer contracts should not acquire provider branches. No plugin
discovery framework or shared consumer database is needed.

The registry's explicit allowlist is a deliberate trust boundary, not a generic
HTTP fallback. Separate Python packages may inject a provider factory; deployment
configuration still needs an explicit validated provider definition.

Token streaming is an adapter capability, not a behavior synthesized by the
gateway. LM Studio uses its loopback OpenAI-compatible SSE response and emits
bounded `assistant_text_delta` events containing only display-safe assistant
text. The first fragment is forwarded promptly and later fragments are
coalesced so token-sized upstream chunks cannot exhaust the session event
limit. `round` distinguishes provider calls separated by application-tool work,
and the existing event sequence remains the ordering and replay cursor.

Codex, Claude, Grok, and OpenRouter continue to report token streaming as false.
Their completed output is never split into invented deltas. Structured-output
invocations also retain the complete-result path even on LM Studio because
provisional JSON is not a display-safe assistant answer. A completion remains
authoritative and must exactly reconcile with the streamed text for that
provider round; failure or cancellation emits no fabricated completion.

LM Studio's grammar compiler rejects the otherwise valid JSON Schema string
keywords `minLength` and `maxLength`. Its adapter recursively omits only those
keywords from tool and structured-output schemas on the provider wire. The
runtime retains the original application schemas unchanged and validates tool
arguments and final structured output against those originals, so transport
compatibility never relaxes the authoritative contract. Other adapters receive
their existing request schemas unchanged.

LM Studio may return a context-window overflow inside an HTTP-200 SSE error
frame. The adapter classifies the bounded structured native error as
`context_window_exceeded` using its code, type, prompt-token count, and active
context size. Those native counts and messages remain provider diagnostics;
only the stable code and a fixed redacted explanation enter the public session.
A bounded text pattern is retained only for compatible older responses that do
not contain the structured fields.

Grok authentication is reported inconclusive when no reliable content-free
probe is available. CLI effective model remains unknown unless native output
establishes it. Configured
qualified-task labels are owner assertions, not qualification performed by the
runtime. These distinctions must remain visible in consumer settings.

`token_limit_control` is true for HTTP routes that receive `max_tokens` and
false for CLI routes that do not expose an equivalent verified control.
Character/output-byte and time bounds apply to every route. A CLI profile's
token preference must not be presented as an enforced token budget.

## Reasoning effort

Each adapter owns two sets. `TRANSPORT_EFFORTS` is what the route can encode at
all, taken from the provider's own accepted list rather than from this runtime's
vocabulary. `VERIFIED_EFFORTS` is keyed by exact model identity, because a CLI
accepting a level is not evidence that a particular model behind it supports one.
No model is pre-qualified in this repository, and an alias such as `default`
names no exact model, so an unconfigured profile publishes nothing.

A deployment declares `reasoning_efforts` on the profile to attest what its model
supports. That declaration is intersected with `TRANSPORT_EFFORTS` and rejected
outright when it names a level the route cannot send — attesting model support
never overrides protocol impossibility, and an invalid declaration fails rather
than being quietly dropped. `reasoning_effort_control` follows the resolved set,
so a profile with no supported effort publishes an empty list and a consumer
renders no selector.

A forwarded effort is a request, not provider confirmation. Nothing on any
installed route reports the level it actually applied, so
`effective_reasoning_effort` stays null unless native metadata establishes it.
The same rule governs the effective model: Grok's `modelUsage` is used only when
it holds exactly one entry, and Claude's may include helper models, so that route
claims nothing.

Every CLI route resolves its trusted command to the concrete installation binary
before spawning, because a packaged CLI that re-executes itself cannot do so
under a restrictive sandbox when the launcher symlink is the spelling. Resolution
is strict and repeated on each invocation: a dangling, non-regular or
non-executable target reports the provider unavailable rather than launching
something else, no release path is ever retained, and health and completion
describe the same installation. This is an executable-identity fix and grants no
filesystem access.

Raw process output is bounded before any native wrapper is parsed. A wrapper
carries fields the envelope never sees — Grok's `thought`, Claude's per-model
usage — so checking only the decoded envelope would let them bypass the profile's
output limit.

The envelope's output schema constrains `tool_calls.name` to an enum of the exact
catalog names for that invocation, and the instructions name them. Requests for
anything else still fail closed in the application; the schema simply stops the
common case at the provider. Guessed names are never repaired.

An unsupported effort fails with `reasoning_effort_unsupported` before any
process starts or request is sent. That check is not cosmetic: the Claude CLI
warns and silently uses its default for an unknown level, and Codex forwards an
unknown level without complaint, so post-hoc detection is not available.

LM Studio accepts the OpenAI-compatible `reasoning_effort` field but reports no
per-model support and a non-reasoning model ignores it silently. The adapter
therefore verifies nothing by default; an operator who knows a loaded model
reasons declares it on the profile.

## Catalog cost

A catalog request without flags touches no provider: it reports configured
identity, capabilities and reasoning options from configuration alone. Health and
discovery probes run only when asked, concurrently and under an individual
timeout, and one probe serves every profile on the same connection. A probe that
fails or times out becomes that profile's own inconclusive state with a detail
code; it never fails the catalog, so a consumer can render the configured list
immediately and refresh readiness separately.

## Model discovery and selectable options

Discovery is a third axis, separate from readiness and from task qualification.
`GET /v1/profiles/{profile_id}/model-options` performs an on-demand bounded
catalog read, then applies one explicit deployment policy. `catalog_model_tasks`
qualifies every runtime-issued reasoning entry for named tasks without copying
provider IDs into a consumer; exact `model_options` narrows qualification to
listed identities when model-specific evidence requires it. The two modes cannot
mix. A catalog entry without qualifying policy is not selectable; an exact
policy whose model has disappeared is not returned. The
session contract accepts only the issued option ID and resolves the catalog
again before dispatch. Missing or changed options fail before provider invocation
and never fall back.

Codex reads the structured, paginated app-server `model/list` protocol and
filters provider-only effort values outside this runtime vocabulary. Claude uses
a maintained runtime catalog of exact documented model identifiers: Fable 5.1,
Opus 5, Sonnet 5, and Haiku 4.5. Grok similarly uses a maintained catalog because
its `grok models` command is human-facing rather than a stable machine contract.
Maintained entries establish provider identity and display labels only; they do
not establish installation, authentication, readiness, or task qualification.

LM Studio combines the compatible loopback `/v1/models` catalog with native
`/api/v1/models` metadata. Only entries the native response classifies as `llm`
become reasoning options; embeddings and unknown-kind entries never do.
`loaded_instances` establishes loaded state when native and compatible identities
can be reconciled. An older, unavailable, malformed, or ambiguous native response
leaves type/loaded state inconclusive. See the
[native API contract](https://lmstudio.ai/docs/developer/rest/list). OpenRouter
enumeration remains deliberately out of scope.

## Supported adapters and activation

`GET /v1/adapters` always lists the five supported reasoning adapters, even when
no deployment profile exists. Supported, configured, enabled, installation
detection, profile readiness, and task qualification remain separate. The plain
read is passive. `?probe=true` explicitly runs bounded, concurrent detection;
CLI detection resolves only trusted executable identities and starts no process.
With multiple configured CLI connections, installed means at least one is found;
profile health describes the exact route. LM Studio detection checks loopback
availability, and an unreachable endpoint leaves installation unknown. Multiple
LM Studio endpoints require per-profile inspection. OpenRouter has no local
installation claim and requires operator setup when unconfigured.

The optional operator `activation_policy.managed_profiles` maps fully configured
profile IDs to their initial enabled booleans. Its entries are the only activation
options a consumer can mutate. Profiles outside the map preserve their existing
always-enabled behavior. `POST /v1/adapter-activation` accepts exactly an issued
`option_id` and a boolean `enabled`; it cannot add commands, paths, endpoints,
models, credentials, upstreams, processing permissions, or qualification claims.
CLI aliases such as `default` or `opus` are allowed only when deliberately present
in the operator profile; enabling a profile never invents a model or attests an
effort. OpenRouter may be enabled only after its complete operator profile and
environment credential reference exist; credentials never pass through a browser.

Enabled profiles are exposed by the existing profile catalog and may be selected
or invoked. Disabled profiles remain visible as catalog options but cannot be
selected or dispatched. Activation does not change the selected profile, retained
session snapshots, external-processing authorization, or task qualification.
Disabling the configured default profile (`default_profile`), a selected profile,
a task-routed profile, or a profile referenced by a
nonexpired runtime session fails with a stable conflict. In-memory activation and
selection mutations share one lock. Sessions retain immutable profile/connection
snapshots and no hot configuration reload is introduced.

An unresolved persisted selection does not prevent reading the adapter catalog:
its selection blocker is omitted so the consumer can expose recovery choices.
Profile reads and dispatch still fail closed until selection is explicitly repaired.
An activation overlay that disables the configured default fails at startup.

The bootstrap writes only profile booleans to an atomic private `activation.json`
overlay under the operator's state root (directory 0700, file 0600). A hash binds
it to the exact approved managed profile/connection definitions. Changed policy,
unknown IDs, unsafe permissions, and symlinks fail closed; the operator must
reconcile or remove a stale overlay before restart. No credential value enters
the overlay. The gateway remains authenticated and loopback-only. Node host types
use the same provider-neutral meaning in camelCase, with optional port methods
so existing injected clients remain source-compatible.
