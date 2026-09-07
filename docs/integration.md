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

## Node or language-independent consumer

A Node backend can start one pinned Python gateway process for its deployment
and install the matching packed TypeScript client. The backend
holds the random bearer token and calls loopback. It exposes product-shaped
operations to its browser UI; the runtime token, raw runtime gateway, provider
credentials, and provider configuration never enter the browser.

```ts
import { RuntimeClient } from "@local-agent-runtime/client";

const runtime = new RuntimeClient({
  baseUrl: "http://127.0.0.1:8765",
  bearerToken: process.env.LOCAL_AGENT_RUNTIME_TOKEN!,
});

const profiles = await runtime.profiles(false);
```

Use one gateway instance, private state directory, configuration, and token per
consumer security boundary. A single installation of the Python package may be
reused, but products should not share sessions or one bearer token. The gateway
also rejects non-loopback peers, unapproved browser Origins, invalid Hosts, and
unauthenticated requests.

## Artifact compatibility

The Python package and TypeScript client currently share release version 0.1.1,
while the HTTP contract advertises API version 1.0.0. A consumer pins both
artifacts from the same release and keeps its lockfiles. Upgrade work should:

1. install the new artifacts in a branch;
2. regenerate or inspect the committed OpenAPI contract diff;
3. run the consumer's runtime conformance and product policy tests;
4. rebuild embedding indexes when the vector-space fingerprint or consumer
   chunking/preprocessing generation changes; and
5. roll back by restoring the prior artifact pins, never by copying old source.

The current TypeScript package remains `private` to prevent accidental registry
publication. The initial GitHub release attaches the wheel, source archive,
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
