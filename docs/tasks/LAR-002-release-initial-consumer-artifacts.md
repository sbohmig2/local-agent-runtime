---
tracker: LAR-002
component: agent-runtime
status: in-progress
priority: P0
effort: S-M
parallel_safe: no
created: 2026-09-07
updated: 2026-09-07
tags: [lar, release]
---

# LAR-002 - Release initial consumer artifacts

**Status:** In progress
**Priority:** P0 - provide an immutable package-manager boundary
**Effort:** S-M

## Goal

Publish one public, immutable GitHub `v0.1.1` release containing a matching
Python wheel, Python source distribution, and TypeScript client tarball that
consumers can pin without a repository checkout.

## Wins

Independent applications can install the same reviewed runtime version through
their own package managers. Upgrades and rollback become explicit version
changes instead of copied files, mutable branches, sibling paths, or submodules.

## Lean evidence

- **Verdict:** pass; the implementation builds both language artifacts and the
  private release proved the artifact path, but anonymous consumer installation
  and distributable licensing remain incomplete.
- **Current evidence:** authenticated private-release URLs are an unsuitable
  default package-manager boundary.
- **Smallest complete outcome:** a public MIT-licensed patch release, checksums,
  isolated artifact tests, private vulnerability reporting, and anonymous
  installation proof.
- **Necessary complexity:** aligned versions, clean-tree/source provenance,
  exact artifact membership, generated-client freshness, license text in every
  artifact, secret/path auditing, and immutable release verification.
- **Deferred:** PyPI/npm publication, signing/attestations, automatic releases,
  hosted distribution, and release channels.

## Runtime gates

- Provider behavior, profiles, task routes, sessions, embedding spaces, and API
  `1.0.0` semantics do not change in this packaging patch.
- Artifacts contain no credentials, deployment-local configuration, runtime
  state, prompts, responses, consumer data, or developer-local paths.
- The TypeScript package remains `private: true` against accidental registry
  publication.
- A release proves package integrity, not provider availability, authentication,
  task qualification, local inference, or live-provider use.

## Scope

1. Build the wheel, source archive, and npm tarball from a clean checkout after
   verifying Python/TypeScript/API version alignment and generated contracts.
2. Emit a SHA-256 manifest over exactly those artifacts with the source commit,
   and verify downloaded assets independently of the checkout version.
3. Install and exercise the wheel, authenticated loopback gateway, rebuilt
   source archive, and packed TypeScript client in isolated environments.
4. Publish under MIT and verify that every artifact carries the exact repository
   license. Provide a security policy and private vulnerability-reporting route.
5. Audit all reachable repository content for credentials, local configuration,
   runtime state, personal/project-specific documentation, and developer paths
   before public visibility.
6. Create annotated `v0.1.1`, publish only the verified artifacts and manifest,
   verify remote hashes, and prove unauthenticated package-manager installation.

## Acceptance

1. One documented command builds exactly three version-aligned artifacts and a
   checksum manifest from a clean checkout.
2. Filenames, package metadata, Python `__version__`, TypeScript version, and tag
   agree on `0.1.1`; the gateway contract remains `1.0.0`.
3. Every artifact contains exact MIT license text and passes secret/path/member
   inspection plus isolated installation.
4. The public tag and Release resolve to the reviewed commit; downloaded files
   match the manifest byte-for-byte.
5. Documentation is consumer-generic and contains no personal information or
   references to unrelated projects.
6. Repository checks and independent review pass. No public registry or live
   provider call occurs.

## Test cases

Dirty tree; stale generated client; version mismatch; missing/extra/duplicate
artifact; malformed manifest; license missing/mismatch; unsafe archive member;
credential/path/configuration canary; isolated wheel import/CLI/gateway startup;
source rebuild; isolated TypeScript import; existing tag/release; upload failure;
remote hash mismatch; anonymous download/install.

## Dependencies

LAR-001 is complete. Repository publication, release creation, commit, and push
are authorized for this task. Public registries, provider egress, and live
qualification remain out of scope.

## Key files

- `pyproject.toml` and `clients/typescript/package.json` - package identity.
- `scripts/build_release.py` - artifact, provenance, license, and integrity gate.
- `docs/integration.md` - consumer installation and isolation boundary.
- `SECURITY.md` and `LICENSE` - public security and reuse terms.

## Progress Notes

- 2026-09-07: The full repository gate passes with 122 Python tests and six
  TypeScript tests. The development artifact build carries exact MIT text in all
  packages and intentionally produces no uploadable manifest from dirty source.
- 2026-09-07: The pre-public content audit removed personal information,
  references to unrelated projects, project-specific history, and unnecessary
  contributor-process files. A concise generic `AGENTS.md` retains architecture,
  security, and validation guidance for human and AI contributors.
