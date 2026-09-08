---
tracker: LAR-005
component: agent-runtime
status: done
priority: P0
effort: L
parallel_safe: no
created: 2026-09-08
updated: 2026-09-08
tags: [lar]
---

# LAR-005 - Deliver model selection and reasoning controls

**Status:** Done

## Goal

Expose truthful configured model choices and supported reasoning effort through
the public Python, gateway and TypeScript host contracts. Qualify installed CLI
providers so consumers can switch away from a slow local model.

## Lean evidence

- Verdict: pass. A real consumer can only select its single configured local
  model and cannot see available provider capabilities or reasoning controls.
- Smallest complete outcome: configured profiles, provider readiness, model
  discovery where supported, and a per-turn supported effort reach the actual
  adapter and retain provenance. One working alternate provider is demonstrated.
- Necessary complexity: provider-owned capability differences, safe configuration,
  validation, cancellation and public-contract compatibility.
- Deferred: OpenRouter model marketplace/discovery and browser credentials, automatic fallback, billing,
  UI components and consumer-specific data or prompts.

## Runtime gates

- Configured provider connections remain operator-owned. No arbitrary executable,
  endpoint or credential editing is exposed to a browser.
- Discovery and availability are distinct from authentication and qualification.
- Reasoning controls must be implemented by each supporting adapter, never
  silently accepted and ignored. Unsupported combinations fail before dispatch.
- Each turn snapshots its requested model and effort; in-flight work is immutable.
- External disclosure remains consumer-owned, explicit, and separate from health.
- Fix native CLI envelope compatibility without weakening sandbox boundaries.
- No provider fallback, canned text, user-input intent matching, or private
  consumer imports. Credentials never appear in catalog or diagnostics.
- Paired package/gateway/generated-client changes are verified together. No
  publishing or release mutation is authorized by this implementation task.

## Scope

- Safe model/profile catalog and provider readiness/capability reporting.
- Configured model selection and per-turn reasoning effort through the shared
  Node host, gateway, application and provider adapters.
- Bounded discovery where supported; explicit configured identities otherwise.
- Installed Codex, Claude, Grok and LM Studio compatibility investigation and
  fixes with honest unsupported/unavailable reasons.
- Public-contract tests, provider-specific tests and synthetic live evidence.
- Owner-promoted supported-adapter inventory and managed enable/disable of exact
  operator-approved dormant profiles, including OpenRouter setup visibility.
- Truthful LM Studio loaded state from native metadata, with unknown fallback.

## Acceptance

- Consumers can render a normal configured-model selector and a separate valid
  reasoning selector without knowing provider SDK or CLI formats.
- A real alternate provider completes a synthetic tool-assisted turn; remaining
  provider blockers are explicit, not disguised as success or fake availability.
- Unsupported effort fails safely; supported effort reaches the provider.
- Existing no-effort clients retain their behavior and contract tests pass.
- All five supported adapters are visible without configuration. Passive reads
  perform no provider probes; explicit detection is bounded and independent of
  activation, readiness, authentication, processing permission, and qualification.
- Consumers enable only issued operator-approved options. Disabled profiles
  cannot be selected/invoked; selected/in-use profiles cannot be disabled;
  activation persists privately without mutating session snapshots or secrets.

## Test cases

Configured/discovered identities; missing authentication; malformed native CLI
output; requested/effective provenance; unsupported effort; cancellation;
secret redaction; two concurrent turns with distinct immutable selections.

## Dependencies

LAR-004 public host toolkit is complete.

## Key files

- `src/local_agent_runtime/`: public contracts, application and provider adapters.
- `docs/architecture/components/agent-runtime.md`: permanent ownership.

## Progress Notes

- 2026-09-08: Closed after independent Claude Opus review returned ready to
  close with no blocking finding and the review snapshot remained unchanged.
  Source commit `91c8ac58ae2c6bc0f512fa3a0e35f9f84c9cfa24` passed GitHub CI and was
  published as immutable GitHub release `v0.2.0`. Anonymous downloads were
  verified against `SHA256SUMS`: TypeScript client
  `291bb42b51103cc4159604c416ade013412a494a623b2d708f4b6a7e674eb387`,
  Python wheel `73dd04d8ba4e4013c73e07ae512c7c4346428acc2a51ec829657af1ea7ae889c`,
  and source distribution
  `6afdb8674d87b45e47b02879c3198ebe86b8f5946b05adccd31a27da1a6a36f4`.

- 2026-09-08: Developer readiness is complete. The repository gate passes 199
  Python tests with one opt-in live test skipped, 44 TypeScript tests, generated
  contract checks, three immutable artifact builds, and isolated wheel, source,
  root-client and host-client installs. Owner authorized the `v0.2.0` release.
  LAR-005 entered the required independent closure review.

- 2026-09-08: Addressed advisory review findings ADV-CLAUDE-003 and
  ADV-CLAUDE-010. Passive adapter inspection survives an unresolved selection,
  while profile reads and dispatch keep their fail-closed behavior. The configured
  default cannot be disabled through activation or an overlay. LM Studio loaded
  state remains unknown when native loaded identities cannot be reconciled with
  its compatible model catalog.

- 2026-09-08: Owner promoted the Buzz-style supported/detected/enabled runtime
  model and OpenRouter setup visibility. Specification pass: the smallest complete
  reusable outcome is the fixed five-adapter catalog plus activation of complete
  operator-approved dormant profiles. Necessary complexity is an additive public
  contract, private atomic activation overlay, configuration-bound policy, and
  selected/in-use guards. Arbitrary browser configuration, cloud credential entry,
  model marketplace/discovery, auto-qualification, and provider fallback remain
  deferred. Implemented `/v1/adapters` and `/v1/adapter-activation` across gateway,
  generated client and Node host. LM Studio's compatible catalog is now described
  as available models; loaded state comes only from native `loaded_instances`.

- 2026-09-08: Owner authorized this runtime and consumer extension with parallel
  implementation. Specification pass: additive, capability-driven selection;
  OpenRouter deferred; release, push and closure remain owner-gated.
- 2026-09-08: Implemented additive selection and reasoning contracts across the
  Python package, gateway (API 1.1.0), generated client and Node host toolkit.
  Adapters own their effort vocabulary; configuration may narrow it but never
  widen it. Catalog now separates readiness, discovery and qualification.
  Provider fixes, each reproduced first and verified afterwards: Codex refused to
  start under the deny-root sandbox because loading `AGENTS.md` re-executes its
  binary through the filesystem sandbox helper, resolved with
  `project_doc_max_bytes=0` rather than relaxing the sandbox; Claude reported an
  authenticated install as logged out without `USER`; Grok's `--output-format
  json` session envelope was decoded as a bare structured object.
  Live synthetic evidence: Codex, Claude, Grok and LM Studio each completed a
  tool-assisted turn through the real service, with the requested effort forwarded
  on the three CLI routes. Codex, Claude and LM Studio repeat reliably. Grok does
  not: its model sometimes asks for its own `search_tool`. Constraining the
  envelope schema's `tool_calls.name` to the exact catalog enum cut that from four
  failures in ten turns to one in six; the runtime refuses the rest with
  `unknown_tool_request` and never repairs a name, so Grok stays available and
  compatible but unqualified for unattended tool use. OpenRouter remains out of scope and reports no effort
  or discovery. Release and publishing remain owner-gated; `0.2.0` exists only as
  an unpublished local candidate for paired consumer integration.
- 2026-09-08: Applied coordinator contract review. Reasoning options are now
  per-model rather than per-driver, so an unqualified model or a `default` alias
  publishes nothing and a deployment attests support for the exact model it pins;
  a declaration outside a route's own accepted list is rejected. Effective
  reasoning effort and effective model are no longer inferred from what was
  requested. Raw process output is bounded before native wrapper parsing. Grok
  refuses failed and early-stopped turns and reports an effective model only from
  an unambiguous single-entry `modelUsage`. The envelope schema constrains tool
  names to the exact catalog. A follow-up turn without an override preserves the
  effort already in use. `RuntimeClient.profiles` keeps its `(includeHealth,
  signal)` positional shape. Catalog probes run concurrently under individual
  timeouts, share one discovery per connection, and degrade per profile.
- 2026-09-08: Adopted the independent diagnosis of the Codex sandbox refusal.
  Every CLI route now resolves its trusted command to the concrete installation
  binary before spawning, strictly and per invocation; `project_doc_max_bytes=0`
  stays for its own reason rather than as the sandbox fix. Re-ran the sandbox
  canaries under the final profile with the resolved binary: inside-workspace read
  succeeds, outside read, all writes and loopback network fail with `Operation not
  permitted`. Recorded that those canaries depend on `TMPDIR` staying at its
  per-user default, since `/private/tmp` sits inside the seatbelt profile's own
  permitted set. Observed evidence for deployment-declared efforts: `gpt-6-astra`
  low-max from installed model metadata, the Claude `opus` alias low-max accepted
  without warning, `grok-4.6` low-xhigh with reasoning tokens rising from 26 to
  about 60. Open provider limits: Grok still sometimes requests its own
  `search_tool`, and Claude at `max` effort intermittently exits non-zero with no
  output on a tool-result turn. Both fail closed and neither is qualified for
  unattended tool use.
- 2026-09-08: Resolved the two P2 findings from the Astra review. A TypeScript
  continuation now resolves and authorizes its actual per-turn effort before
  dispatch, retains a successfully dispatched override, and passes the retained
  effort explicitly on later turns. OpenRouter remains feature-neutral while
  applying the shared effort resolver, so any declared or default effort fails
  as invalid configuration instead of being ignored.
