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

Native web is also a consumer decision for each invocation. `Invocation`
carries `provider_native_web`, which defaults to `true`. A consumer that sends
private content can set it to `false` so that a route with the capability runs
with every native web tool disabled (LAR-013):

- Codex sends `web_search="disabled"` under `--strict-config`.
- Claude sends `--tools ""` with a `WebSearch,WebFetch` deny rule.
- Grok sends `--tools ""`, removes `web_search` and `web_fetch` by name, passes
  `--disable-web-search`, and omits `GROK_WEB_FETCH`.

The prompt then forbids every provider-native tool. The switch lives in the
Python invocation contract, so deployment configuration cannot override it.
Routes without the capability always send their disabled form, and the switch
does not change HTTP routes. The session service and HTTP gateway keep the
default for now; exposing the switch there would be an API change.

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

LM Studio's grammar compiler refused the otherwise valid JSON Schema string
keywords `minLength` and `maxLength` in a tool input schema (LAR-010). Its
adapter recursively omits only those keywords from tool input schemas on the
provider wire. The structured-output (`response_format`) schema is sent
unchanged, because LM Studio accepts and enforces its length bounds (LAR-013).
The runtime retains the original application schemas unchanged and validates tool
arguments and final structured output against those originals, so transport
compatibility never relaxes the authoritative contract. A grammar refusal is an
explicit failure; the adapter never retries with a weakened schema. Other
adapters receive their existing request schemas unchanged.

An LM Studio reasoning model may return a schema-bound answer in
`reasoning_content` (or `reasoning`) with empty `content`. For a non-streaming
invocation that has an output schema and no tools, and whose response carries
neither content nor tool calls, the adapter accepts the first non-blank
reasoning field as the answer. It must fit the ordinary output bound and parse
as JSON. The application then validates it against the original schema.
Non-empty content always wins. Unstructured or tool-bearing requests, streamed
reasoning deltas, and every other adapter never promote reasoning text, so
hidden free-form reasoning cannot become a final answer. The shared codec takes
this as an explicit adapter opt-in rather than branching on a driver name.

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

## Context capacity and bounded history

The application retains a session's whole transcript and bounds only what one
provider call sends. Two limits bound that call's window at once and must not
be confused:

- The profile's `max_input_chars` is a character ceiling on what the runtime
  accepts and on what an adapter serializes. It applies to incoming input where
  it arrives (the opening turn as a whole, instructions plus prompt, measured
  as a request, and each follow-up prompt; a tool result has its own fixed
  limit and then joins the current turn that the next window must fit) and to
  the serialized request of every provider call. It does not bound the
  retained transcript itself: the session event limit bounds how many turns a
  session retains, and a valid conversation may outgrow the ceiling while each
  call is planned to fit it. Output characters
  and tool rounds remain session totals and fail with their existing codes;
  pruning a call's window never lifts them.
- Model-context budgeting bounds the same window to the loaded model's token
  capacity, which is provider evidence rather than configuration.

Planning measures every candidate window as the adapter would send it. Each
shipped adapter implements the optional additive `PromptSizingProviderPort`
(`prompt_chars`): LM Studio reports the larger of its streaming and
non-streaming chat bodies, OpenRouter its chat body and the CLI adapters their
bounded prompt, each exactly what that adapter's own send-time check bounds. An
adapter without the port is sized by a generic estimate that takes the larger
of the two shipped wire shapes plus a framing allowance. An adapter reporting a
non-integer or negative size fails the call as `invalid_provider_contract`.
The adapters' own serialized-body refusal (`input_limit_exceeded`) is unchanged
and remains the final fail-closed check after planning.

Capacity comes from the optional additive `context_window()` port. LM Studio
implements it from the native `/api/v1/models` catalog: the configured identity
must resolve to exactly one native model, by its key or by a loaded instance
identifier, and the reported value is that model's loaded
`config.context_length`. Several loaded instances yield the smallest length
because the serving instance is not observable. A downloaded-but-unloaded
model, a malformed entry, an ambiguous identity, or an older server without the
native route reports unknown capacity; `max_context_length` is never substituted
because it describes what the model could be loaded with, not what is loaded.
The read is a bounded five-second loopback GET inside the session's own timeout
and cancellation scope. Other adapters do not implement the port and keep their
existing behavior. The inferred cause of the original failure is that the
server's own overflow handling discards leading messages before rendering the
chat template, which then rejects the remaining role sequence; that step was not
observed directly.

With known capacity the budget is the loaded context minus the profile's
`max_output_tokens` and a margin of five percent (at least 128 tokens). The
window always contains the instructions and the entire current turn: the latest
user message and every assistant tool request and tool result since it. Earlier
turns are then kept newest-first while they fit both the token budget and the
character ceiling, as whole turns only, so a tool-call group is never split and
no executed tool is ever replayed. Nothing is summarized; dropped turns simply
leave the prompt while the record keeps them. Unknown capacity claims nothing
about the model: no token budget applies and the session reports
`capacity_source: unknown` with `capacity_tokens: null`. The character ceiling
still applies on its own, keeping the largest suffix of whole turns that fits
it, so an unknown-capacity call can be reduced; its `context_reduced` event
then carries `null` for `capacity_tokens` and `budget_tokens` and the
configured output allowance unchanged.

The profile's `max_output_tokens` is a configured maximum, never a value the
runtime rewrites. Whenever the mandatory window fits beside it, the call uses
it and retains as much history as fits. When it does not fit and the loaded
context is small (below 128,000 tokens, a decimal count), the runtime allocates a smaller output
for that one call instead of failing: it takes the smallest-sized window that
still carries the mandatory set and fits the character ceiling (a calibrated
superset may be smaller than the heuristic mandatory subset), keeps the margin,
and gives the remaining context to output, capped at the configured value.
Optional earlier turns are not retained at the expense of output space. The
allocation must reach the useful floor, `min(max_output_tokens, 2048)`; below
it, or on a context of 128,000 tokens or more whose configured allowance cannot
fit, the session fails with `context_window_exceeded` and a fixed message
before any provider request (a distinct message names a loaded context smaller
than the configured reserve). A mandatory window that exceeds the character
ceiling fails instead with `input_limit_exceeded` and its own fixed message,
again before any provider request and whether or not capacity is known; no
output reallocation can help there. Both refusals leave the transcript retained
and report the configured allowance unchanged.
The provider receives a per-call copy of the limits; the profile, the session's
public `limits` and the next call's starting point are unchanged, so a roomy
follow-up regains the full configured allowance. The allocation is what the
call was planned and sent with, not what the model consumed; consumption is
the provider's reported `completion_tokens` in `usage`. A plan that fails
sends nothing and reports the configured allowance unchanged.

A provider that stops at its output limit reports `length` rather than `stop`;
the shared chat codec turns that into `provider_incomplete` before returning a
completed answer or exposing any tool request. Provisional streamed text may
already be visible, but remains attached to the failed turn, not a completed
answer. A smaller allocation can therefore produce an explicit failure, never
a truncated answer presented as complete or
a partial tool execution.

No installed route exposes a tokenizer, so sizes are estimates and are
published as such. The heuristic charges three ASCII characters per token, one
token per Latin, Greek or Cyrillic character and three per other character
(CJK, symbols, emoji), plus per-message, per-tool, tool-argument, tool-result,
output-schema and chat-template overhead. A loopback measurement of a
Mistral-family tokenizer produced about five characters per token for prose and
four for JSON, so the heuristic prunes earlier than strictly necessary rather
than later. After a provider call reports `prompt_tokens`, the next plan sizes
any superset of that exact message set from the reported count plus the
heuristic for messages added since (`basis: calibrated`); a missing, zero,
fractional or implausible count leaves the heuristic in charge. Candidate
windows are evaluated from the whole transcript downwards so a calibrated
superset that fits is chosen before a heuristic-only subset could refuse.
Both heuristic and calibrated sizes are estimates, never upper bounds: the
measured conservatism for prose and JSON does not guarantee that arbitrary
ASCII text or another tokenizer fits, and a provider may still report an
overflow that the runtime then classifies as it does today.

The public session carries a `context` object with the capacity, its source,
the estimated prompt size, the basis, the reduction counts and the configured
and allocated output tokens of the most recent provider call. Dropping messages
or allocating less output than configured emits a `context_reduced` event with
counts only; which content left the prompt is never disclosed, and neither
prompts nor tool results enter diagnostics.

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
