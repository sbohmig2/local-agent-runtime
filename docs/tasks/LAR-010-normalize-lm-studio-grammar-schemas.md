---
tracker: LAR-010
component: agent-runtime
status: review
priority: P0
effort: S
parallel_safe: no
created: 2026-09-10
updated: 2026-09-10
tags: [lar]
---

# LAR-010 - Harden LM Studio catalog requests

**Status:** Review

## Goal

Keep valid application tool and output schemas usable through LM Studio when
its grammar compiler rejects supported JSON Schema string-length keywords,
without weakening the application's authoritative validation contract.
Keep runtime-issued catalog model options usable through the TypeScript host
when their reasoning capabilities intentionally differ from the base profile.

## Boundary

- The original `ToolDefinition.input_schema` and structured-output schema remain
  immutable and authoritative for application-side validation.
- Only the LM Studio wire request omits the observed unsupported `minLength` and
  `maxLength` grammar keywords, recursively. Other adapters retain the exact
  schemas supplied by the application.
- No consumer-specific tool knowledge or broad schema-dialect abstraction enters
  the runtime.
- The host validates a selected option and its effort against a fresh
  runtime-issued option catalog. It never trusts a consumer model label, and the
  Python runtime remains authoritative when it resolves the option again.
- Immutable release `v0.5.0` and its release notes remain unchanged. This task
  prepares paired development package version `0.5.1`; API remains `1.4.0`.

## Scope and acceptance

- Translate LM Studio tool and structured-output schemas before request-size
  validation and transport, including nested schema positions.
- Preserve all unrelated schema keywords and do not mutate caller-owned values.
- Prove the Finance Vault failure shape with 43 tools and nested
  `agent_summary` string-length constraints.
- Prove results outside the original minimum or maximum still fail application
  validation with `invalid_tool_arguments`.
- Keep LM Studio streaming and non-streaming paths behaviorally aligned.
- Validate catalog-option effort against that option rather than the base
  profile; reject stale option IDs or effort capabilities before session
  creation while preserving base-profile and continued-session behavior.
- Run the full repository gate. Commit, push, tag, publication, release, and
  task closure remain owner-gated.

## Key files

- `src/local_agent_runtime/adapters/providers/lmstudio.py`
- `clients/typescript/src/host/session-coordinator.ts`
- `clients/typescript/tests/host.test.mjs`
- `tests/test_providers.py`
- `tests/test_runtime.py`
- `docs/architecture/provider-adapters.md`

## Progress notes

- 2026-09-10: Finance Vault reproduced LM Studio rejecting a 43-tool request
  because `operation_finalize` included `minLength` and `maxLength` on nested
  `agent_summary`. The same tool set works when those two provider-unsupported
  grammar hints are omitted.
- 2026-09-10: Implemented an LM-Studio-only recursive wire-schema translation
  for tool and structured-output requests. The translator distinguishes schema
  keywords from identically named object properties and literal data, preserves
  caller-owned schemas, and leaves every other adapter unchanged. Application
  validation still rejects values outside the original length bounds. The full
  repository gate passed with 242 Python tests, one opt-in live test skipped,
  48 TypeScript tests, documentation and generated-contract validation, and
  isolated `0.5.1` Python/TypeScript artifact installation. No commit, push,
  tag, publication, or release was performed.
- 2026-09-10: Corrected the TypeScript host's catalog-option effort validation.
  A new session now refreshes the runtime-issued option, rejects a missing
  option or unsupported effort before creation, forwards a supported option
  effort even when the base profile has no effort list, and snapshots that
  policy for continuation. The full gate passed with 242 Python tests, one
  opt-in live test skipped, 50 TypeScript tests, generated contracts,
  documentation, package builds, and isolated artifact installation. No commit,
  push, tag, publication, or release was performed.

## Residual risk

LM Studio may expose other JSON Schema grammar gaps in future server or model
versions. This patch removes only the two keywords demonstrated by the real
Finance Vault failure. Because the provider sees a less restrictive grammar it
may still propose an out-of-range string; the runtime rejects that result
explicitly against the original schema rather than accepting weakened data.

The runtime option catalog is deliberately refreshed before a new host session,
so a local catalog change can turn a previously saved option or effort into an
explicit start failure. An option already resolved for a running conversation
keeps its snapshotted effort policy for later turns; the Python session remains
the final authority.
