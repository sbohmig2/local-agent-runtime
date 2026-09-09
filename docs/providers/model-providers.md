# Initial model providers

| Route | Transport | Processing | Credential source | Initial boundary |
|---|---|---|---|---|
| Codex | Trusted local CLI | External | Existing CLI authentication | Bounded isolated session; exact model when supported |
| Claude | Trusted local CLI | External | Existing CLI authentication | Bounded isolated session; exact model when supported |
| Grok | Trusted local CLI | External | Existing CLI authentication | Bounded isolated session and cleanup; exact model when supported |
| LM Studio | Loopback HTTP | Local | None by default | Explicit endpoint and model; reject non-loopback |
| OpenRouter | HTTPS API | External | Environment-backed secret reference | Explicit profile/model, egress permission, and effective upstream provenance |

## Provider-native public web

The Codex, Claude, and Grok CLI routes expose `provider_native_web: true` and
allow only their built-in public-web search and page-retrieval tools. The
installed CLI account and administrator policy remain authoritative, so the
capability describes the adapter surface rather than promising that every
request will be permitted upstream.

This capability does not grant shell execution, filesystem access, raw network
sockets, browser or computer control, plugins, subagents, inherited MCP servers,
or arbitrary provider-native tools. LM Studio and OpenRouter report the
capability as false; a consuming application may separately supply a bounded
web-search tool through the ordinary tool-call contract.

## Reasoning effort and discovery

| Route | Effort control | Levels the route can send | Model discovery |
|---|---|---|---|
| Codex | `-c model_reasoning_effort` | minimal, low, medium, high, xhigh, max | Structured app-server `model/list` |
| Claude | `--effort` | low, medium, high, xhigh, max (no `minimal`) | Runtime-maintained exact catalog |
| Grok | `--reasoning-effort` | low, medium, high, xhigh | Runtime-maintained exact catalog |
| LM Studio | `reasoning_effort` request field | minimal, low, medium, high | Compatible plus typed native loopback catalogs |
| OpenRouter | Out of scope for now | None | Out of scope for now |

That column is what the route can transmit, not task qualification.
`catalog_model_tasks` explicitly qualifies the runtime-issued reasoning catalog
for named tasks without copying model identifiers into a consumer.
`model_options` is the narrower alternative: it maps opaque IDs to exact models,
qualified tasks, and optional operator-narrowed effort policy. The modes cannot
mix. Provider discovery or a maintained catalog never creates qualification by
itself; an option is returned only while catalog and policy match.

The maintained Claude catalog currently maps `Fable 5.1` to
`claude-fable-5-1`, `Opus 5` to `claude-opus-5`, `Sonnet 5` to
`claude-sonnet-5`, and `Haiku 4.5` to
`claude-haiku-4-5-20251001`. The first three publish low through max with high
as provider default. Haiku publishes no effort selector because the documented
model has no `effort` control. Grok maps `Grok 4.6` to `grok-4.6` and `Grok 4.5`
to `grok-4.5`; exact effort support remains deployment evidence rather than a
catalog claim.

Codex and Claude accept an unrecognized level without failing — Claude warns and
uses its default, Codex forwards it — so the runtime validates before dispatch
instead of relying on the provider to object. None of the four reports back the
level it applied, so effective reasoning effort is reported as unknown.

### Observed model support

These are observations of specific installed models on one machine, recorded so a
deployment can write an evidence-backed `reasoning_efforts`. They are not runtime
defaults, and no model inherits them automatically.

| Model | Observed levels | Evidence |
|---|---|---|
| `gpt-6-astra` (Codex) | low, medium, high, xhigh, max | Installed model metadata lists `supported_reasoning_levels` with default `medium`. It also lists `ultra`, which this runtime's vocabulary does not carry. |
| `opus` (Claude alias) | low, medium, high, xhigh, max | Each level accepted with no unknown-level warning; the alias resolved to `claude-opus-5` in that run. An alias is not a model identity, so an operator attests it deliberately. |
| `grok-4.6` (Grok) | low, medium, high, xhigh | Each level accepted, and reported `reasoning_tokens` rose from 26 at `low` to about 60 at the higher levels. Its `modelUsage` names `grok-4.6-build`, which is why the requested and effective identities are reported separately. |

## Route-specific process constraints

A packaged CLI is reached through a launcher symlink on PATH, and Codex
re-executes itself during startup. Under the deny-root permission profile this
runtime requires, the operating system refuses that re-execution when it is
spelled as the symlink (`sandbox-exec: execvp() ... Operation not permitted`) and
the session cannot start. Every route therefore resolves its trusted command to
the concrete installation binary before spawning, re-resolving on each invocation
so an update is picked up and no release path is retained. Health and completion
share that resolution, and a missing or unexecutable target stays unavailable.

Separately, and not as a substitute for that fix, the Codex route sets
`project_doc_max_bytes=0` so project and user `AGENTS.md` files never become
instructions to a generic runtime.

The sandbox is otherwise unchanged and grants no additional path. Verified under
the final profile with the resolved binary: a read inside the disposable
workspace succeeds; a read of a sibling outside it, a write inside or outside it,
and a loopback connection all fail with `Operation not permitted`. Those canaries
hold when the workspace lives under the per-user `TMPDIR` that `tempfile` uses by
default. A `TMPDIR` pointing at the world-shared `/private/tmp` is inside the
seatbelt profile's own permitted set, where reads and writes are no longer
refused, so leave `TMPDIR` at its per-user default.

The Claude CLI resolves its stored login against the invoking account name, so a
minimal environment reports an authenticated installation as logged out. The
adapter forwards `USER`, which is an account label rather than a credential.
`USER` alone restores the authenticated result; `LOGNAME`, `SHELL`,
`SSH_AUTH_SOCK` and the XPC variables each leave it logged out.

That route has one unresolved intermittent failure. On the turn that carries tool
results, the CLI occasionally exits non-zero with no output — once in three live
synthetic runs at `max` effort, and not reproducible in three direct repeats of
the same invocation. The runtime reports `provider_unavailable` and never
substitutes a result, so the behavior is safe but the route is not yet qualified
for unattended multi-turn tool use.

Grok's `--output-format json` returns a session envelope carrying
`structuredOutput`, `text`, `stopReason` and usage, not the bare structured
object. The adapter unwraps it and refuses an envelope whose stop reason shows an
incomplete turn.

A Grok qualification limit remains, separate from that fix. Even with built-in
tools disabled, `grok-4.6-build` sometimes requests its own `search_tool` instead
of a tool from the supplied catalog. Constraining `tool_calls.name` to the exact
catalog enum reduced that from four failures in ten observed synthetic turns to
one in six, but it has not eliminated it. The runtime refuses those turns with
`unknown_tool_request` rather than executing an unlisted tool or repairing the
name, so the route is available and protocol-compatible but is not yet qualified
for unattended tool-assisted work.

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
