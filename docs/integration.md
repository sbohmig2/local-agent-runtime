# Consumer integration

The repository is the single implementation source. Consumers pin immutable
artifacts and never copy adapter files, import another consumer, use a Git
submodule, or read this repository's working tree at runtime.

## Python consumer

Python consumers install the built wheel into their own locked environment and
use the public package exports. During local pre-release integration, install an
exact wheel file; after an owner-approved release, use the immutable release
artifact and its checksum through the project's package source.

```python
from pathlib import Path
from local_agent_runtime import build_runtime

runtime = build_runtime(
    Path("deployment/runtime.yaml"),
    Path("deployment/runtime-state"),
)
```

The consumer still owns prompts, retrieval, tool authorization/execution,
approvals, and result persistence. Runtime sessions are in memory. The selected
reasoning profile is the only runtime state persisted by this package.
Trusted product instructions are supplied through the session's separate
`instructions` field; consumers must not concatenate them into untrusted user
text. Gateway health reports both package and API versions so a backend can
reject an incompatible process before sending content.
HTTP providers receive instructions as a distinct system-role message. CLI
providers receive the role-tagged conversation as one escaped JSON document,
so the separation prevents structural prompt injection but is not a
provider-enforced privilege boundary.

## Node backend or language-independent consumer

A Node backend can install the matching packed TypeScript client. Its root
entry point is a typed gateway client. The optional `/host` entry point adds
Node-only process supervision, MCP transport, bounded session coordination, and
an authenticated local HTTP adapter. Both entry points ship in the same tarball.
Frontend components, HTML, view models, and browser-side model code are outside
the package by design and remain owned by each consuming product.

```ts
import { RuntimeClient } from "@local-agent-runtime/client";

const runtime = new RuntimeClient({
  baseUrl: "http://127.0.0.1:8765",
  bearerToken: process.env.LOCAL_AGENT_RUNTIME_TOKEN!,
});

const profiles = await runtime.profiles(false);
```

## Model selection and reasoning effort

A consumer renders enabled agent/profile choices from `profiles`, then loads
that profile's selectable models on demand with `modelOptions(profileId)`. It
stores and submits the opaque option ID, display label, and one returned effort;
it does not own provider model identifiers, CLI flags, or model catalogs. An
empty `efforts` list means there is no effort selector for that choice.

```ts
const { profiles } = await agent.profiles(true, true); // health, discovery
const profile = profiles.find((item) => item.selected)!;
const catalog = await agent.modelOptions(profile.id);
const option = catalog.options[0]!;
const session = await agent.start({
  prompt,
  profileId: profile.id,
  modelOptionId: option.id,
  ...(option.reasoning.default === null
    ? {}
    : { reasoningEffort: option.reasoning.default })
});
```

Three catalog axes stay independent and none implies another: `health` reports
readiness, `discovery` reports whether the route can enumerate models and which
ones, and `qualification` reports operator-asserted task fitness. Model choice
remains an operator-owned configured connection; nothing in these contracts lets
a browser edit an executable, endpoint or credential.

`modelOptions` performs a fresh provider catalog read and returns only exact
models that also have deployment-owned task qualification. The returned ID is
opaque to the consumer. Session creation resolves it again so an option removed
between settings and submit fails explicitly; it never substitutes the profile's
base model. `model_option_id` and the exact requested model are retained as
separate session provenance.

Each turn carries its own effort. `start` and `continue` snapshot the requested
value and work already in flight is never re-targeted. A `continue` without an
override stays at the effort already in use, so a follow-up never silently drops
back to the profile default; pass an explicit effort to change it, or start a new
session. An effort a profile does not support fails with
`reasoning_effort_unsupported` before the provider is reached, and callers that
send no effort keep their previous behavior.

`requestedReasoningEffort` is what the runtime forwarded, including a configured
default it resolved on the caller's behalf. `effectiveReasoningEffort` is
provider-reported provenance and is null on every route installed today, because
none of them reports the level it applied. Do not read null as "no effort was
used"; compare against `requestedReasoningEffort` instead.

A catalog request without flags touches no provider and is safe to call on every
page load. Health and discovery are opt-in, run concurrently under individual
timeouts, and a probe that fails becomes that one profile's inconclusive state
with a detail code rather than an error for the whole catalog. Load the cheap
list first and refresh readiness separately.

`RuntimeClient.profiles` keeps its original `(includeHealth?, signal?)` shape;
discovery is a third optional argument, so existing cancellation callers are
unaffected.

The host toolkit is assembled by backend composition code. Product instructions,
processing policy, tool authorization, MCP server specifications, structured
output schemas, and HTTP route choice are injected by the consumer. An
application may call `SessionCoordinator` directly from its backend or attach
`HostHttpServer` when it needs a loopback HTTP/SSE boundary. The HTTP adapter is
transport infrastructure, not a user-interface framework.

MCP processes are backend dependencies selected by the consumer. Their
handshake instructions are exposed as attributed catalog metadata but are never
promoted automatically into the trusted system-instruction channel. A consumer
may review and deliberately incorporate such text into its own trusted
instructions; merely configuring a server does not grant its text that status.
The host tarball therefore includes the exactly pinned official MCP client and
its runtime dependencies even when a consumer uses only the root client entry
point. This intentional supply-chain surface is checked through the consumer's
lockfile and package audit.

```ts
import {
  HostHttpServer,
  McpToolCatalog,
  RuntimeSupervisor,
  SessionCoordinator,
  SupervisedRuntime,
} from "@local-agent-runtime/client/host";
```

Structured output is a validated data contract selected by the application.
For example, one product may map a result to graph data and another to text or
an action proposal. The runtime does not define components, rendering hints,
chips, graphs, page layouts, action identifiers, or the authority to execute a
proposed action. Those remain consumer backend and UI responsibilities.

Embedding profiles and embedding calls remain part of the root generated
client and are also forwarded by `SupervisedRuntime`. Consumers own chunking,
indexes, retrieval, persistence, and any browser-facing embedding workflow.

Tool policy receives the requested name and arguments together with the
provider-neutral session and profile context. It may decide asynchronously and
must return the exact `execute` decision; every other result fails closed to an
approval-required terminal state. Processing policy is reapplied before every
initial and continuation prompt submission. Session starts reserve capacity
before asynchronous work, and runtime/MCP/HTTP loops have explicit time,
retention, connection, result-size, and restart bounds.

Use one gateway instance, private state directory, configuration, and token per
consumer security boundary. A single installation of the Python package may be
reused, but products should not share sessions or one bearer token. The gateway
also rejects non-loopback peers, unapproved browser Origins, invalid Hosts, and
unauthenticated requests.

## Artifact compatibility

The Python package and TypeScript client currently share release version 0.1.3,
while the HTTP contract advertises API version 1.0.0. A consumer pins both
artifacts from the same release and keeps its lockfiles. Upgrade work should:

1. install the new artifacts in a branch;
2. regenerate or inspect the committed OpenAPI contract diff;
3. run the consumer's runtime conformance and product policy tests;
4. rebuild embedding indexes when the vector-space fingerprint or consumer
   chunking/preprocessing generation changes; and
5. roll back by restoring the prior artifact pins, never by copying old source.

The current TypeScript package remains `private` to prevent accidental registry
publication. Each GitHub release attaches the wheel, source archive,
`npm pack` tarball, and `SHA256SUMS`; it does not publish to PyPI or npm. Release
maintainers build all artifacts from a clean checkout with:

```bash
uv run python scripts/build_release.py
```

The Python wheel, source archive, and TypeScript tarball each carry the MIT
license. Consumers download all four assets into one directory and verify them without a
repository checkout:

```bash
cd /path/to/downloads
shasum -a 256 -c SHA256SUMS
```

The first manifest line records the package version and exact source commit;
`shasum` ignores that comment. GNU `sha256sum` may warn that the comment is
improperly formatted and its `--strict` mode is not supported. The release
helper validates exact filenames, identity metadata, and hashes from any
checkout with `uv run python scripts/build_release.py --verify /path/to/downloads`.
Consumers pin the exact release asset URL and retain their own lockfile. A
rollback restores the previous artifact URL/version and lockfile. Registry
credentials, automatic release-on-tag, and consumer migrations remain separate
work.

## Configuration ownership

Each product keeps deployment-local provider connections, model and embedding
profiles, task routes, policy flags, and secret references. Configuration and
credentials are not packaged into release artifacts. Chat selection and
embedding profile choice are independent. Products store the embedding profile
fingerprint with each index generation and supply it on query calls.

## Managed runtime activation

For a consumer's Add runtimes screen, use `SessionCoordinator.adapters(probe)`.
The default is a passive five-adapter catalog; request `probe=true` explicitly
to check installation or loopback availability. Existing `profiles` remains the
enabled-model selector and owns per-profile health and reasoning controls.

An operator can declare existing complete profiles as consumer-managed options:

```yaml
activation_policy:
  managed_profiles:
    codex-default: true
    claude-default: false
```

Use IDs that exist in that deployment's `profiles`. The configured default and
task routes must initially be enabled. Undeclared profiles keep existing behavior.
`SessionCoordinator.setAdapterActivation(optionId, enabled)` activates only these
issued options and persists private local state. An enabled profile may still
need installation/login or be unqualified for a consumer task. No runtime probe
authenticates the browser, changes provider login, or approves consumer data
egress. The [adapter contract](./architecture/provider-adapters.md) defines the
policy and retention guards. No key, executable, endpoint, model, upstream, or
qualification field is accepted from a browser.
