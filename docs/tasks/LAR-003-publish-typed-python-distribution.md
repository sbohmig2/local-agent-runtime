---
tracker: LAR-003
component: agent-runtime
status: in-progress
priority: P0
effort: XS
parallel_safe: no
created: 2026-09-07
updated: 2026-09-07
tags: [lar, release]
---

# LAR-003 - Publish the typed Python distribution

**Status:** In progress
**Priority:** P0 - unblock strict consumer type checking
**Effort:** XS

## Goal

Publish matched immutable `v0.1.2` artifacts whose Python distribution declares
its existing inline types through the standard PEP 561 marker.

## Evidence and scope

A strict downstream mypy gate proved that `v0.1.1` omitted `py.typed` from the
wheel. Add the empty marker, make the release gate inspect it, advance both
package versions together, exercise isolated installs, and publish the same
four-artifact release shape. No public API, provider behavior, dependency, or
processing policy changes.

## Acceptance

1. The wheel and rebuilt source distribution contain an empty
   `local_agent_runtime/py.typed` marker.
2. A strict external consumer can analyze imports without `import-untyped`.
3. Repository, release, isolated install, anonymous download, checksum, and
   independent review gates pass.
4. The immutable `v0.1.1` release remains unchanged; consumers upgrade or roll
   back both artifacts together.

## Authorization

This corrective release is part of the owner-authorized end-to-end consumer
integration. Commit, push, immutable patch publication, cross-repository pin
update, independent review, and closure are authorized; public registries and
live provider calls remain out of scope.

## Progress notes

- 2026-09-07: The full repository and development-artifact gate passes with
  123 Python tests and six TypeScript tests. It verifies the marker in the
  direct wheel and the wheel rebuilt from the source distribution, and an
  isolated strict mypy consumer succeeds with the packaged inline types.
- 2026-09-07: Independent Claude Opus/high and fresh Grok review both returned
  `READY`. Claude's two durability suggestions—checking the rebuilt wheel and
  matching the isolated mypy version to the lockfile—were incorporated before
  the fresh Grok verdict.
