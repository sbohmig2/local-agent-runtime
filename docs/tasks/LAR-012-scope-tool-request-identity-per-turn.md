---
tracker: LAR-012
component: agent-runtime
status: review
priority: P0
effort: S
parallel_safe: yes
created: 2026-09-19
updated: 2026-09-19
tags: [lar, tools, compatibility]
---

# LAR-012 - Scope tool request identity per turn

**Status:** Review
**Priority:** P0 - blocks Finance Vault MCP-001/MCP-002 live Chat acceptance
**Effort:** S

## Goal

Accept a tool request identifier that a provider reuses on a later turn of the
same session while still rejecting a duplicate identifier inside one provider
response. A collision with a still-pending request cannot occur: a provider
round starts only after every pending result has been submitted.

## Wins

- Finance Vault's repeated-tool-turn Chat journeys (Terra profile) no longer
  fail with `invalid_tool_request` before the MCP is invoked.
- The runtime stops enforcing a stricter identity contract than the providers
  it fronts: the OpenAI-compatible wire format scopes call identifiers to the
  assistant message that carries them, and the runtime's own JSON envelope for
  the Codex, Claude and Grok CLI routes never required session-wide uniqueness.

## Lean evidence

- Verdict: pass at runtime level; consumer proof pending (owner-gated
  release). Finance Vault recorded on 2026-09-18 that the selected model
  reuses a call identifier on a later tool turn and the runtime rejects any
  identifier already seen in the session; the consumer must not work around it.
- Current evidence: `docs/tasks/MCP-001-finalize-currency-conversion.md` and
  `MCP-002-finalize-market-data.md` (Finance Vault) name this as the T7 gate.
- Smallest complete outcome: replace the session-wide `seen_tool_ids` guard
  with a per-response uniqueness check; no wire, client or configuration
  change.
- Necessary complexity: none beyond two tests.
- Deferred: any provider-specific identifier rewriting.

## Runtime gates

- Provider and profile identity: unchanged.
- Tool request versus consumer-owned execution: results still must match every
  pending request exactly; reuse is only accepted after the earlier request's
  result was delivered.
- Package, gateway, generated clients: no contract change; patch release.

## Scope

- Remove the session-wide `seen_tool_ids` from the session record.
- Reject an identifier that repeats inside one provider response; accept an
  identifier reused after its result. No pending-collision term: the
  synchronous round model makes that state unreachable, so no dead check is
  carried.

## Acceptance

- A session whose provider returns `call_0` on two consecutive tool turns
  completes both turns and delivers both results.
- Two requests with the same identifier in one response still fail with
  `invalid_tool_request`.

## Test cases

- `test_tool_request_identity_may_be_reused_on_a_later_turn`
- `test_duplicate_tool_request_identity_in_one_response_fails`

## Dependencies

None.

## Key files

- `src/local_agent_runtime/service.py` - the guard.
- `tests/test_runtime.py` - the two cases.

## Implementation Plan

- **Applicability:** `required` in spirit though the task is `S` — the change
  touches session-integrity behaviour that every provider route relies on, so
  the proof is written down before the guard moves.
- **Status:** `current` (2026-09-19).
- **Inputs:** Goal, Scope, Acceptance and Test cases above; the Finance Vault
  observation of 2026-09-18 (Terra reuses a call identifier on a later tool
  turn); the OpenAI chat and Codex wire contracts, which scope a tool-call
  identifier to the assistant message that carries it; `service.py`
  `_run_provider_round` and `submit_tool_results`.
- **Implementation authority:** owner instruction of 2026-09-19 ("fix the
  runtime first, then run T7"). Commit, push, release `v0.5.3` and the Finance
  Vault pin bump remain owner gates.

### Acceptance coverage

| Requirement | Slice | Observable proof |
| --- | --- | --- |
| Reuse after delivery is accepted | 1 | `test_tool_request_identity_may_be_reused_on_a_later_turn`: two consecutive tool turns with `call_0`, both `tool_results_received`, session `completed`. |
| Duplicate inside one response is rejected | 1 | `test_duplicate_tool_request_identity_in_one_response_fails`: `failed` with `invalid_tool_request`. |
| Pending collision is impossible by construction | 1 | `_advance` runs only after `submit_tool_results` cleared `pending_tools` (`service.py`), so no guard term and no test claim it; the existing exact-match result test covers the round boundary. |
| No wire, client or configuration change | 1 | `scripts/check.py` unchanged contracts (`generate_contracts.py` diff empty; TypeScript package layout check). |
| Consumer proof | 2 | Finance Vault T7 repeated-turn Chat journey with Terra passes against the released `v0.5.3`. |

### File and boundary map

- `src/local_agent_runtime/service.py` — session record and the provider-round
  guard; the only behavioural change.
- `tests/test_runtime.py` — the two cases beside the existing tool-request tests.
- `docs/tasks/LAR-012-…`, `docs/architecture/components/agent-runtime.md`,
  `docs/tasks/next-up.md` — lifecycle metadata.
- **Must remain unchanged:** `contracts/`, `clients/typescript/`, the gateway
  and API contract (no identifier rewriting, no new event), provider adapters.

### Ordered slices

#### Slice 1 — Scope identity to the turn

- [x] **Status:** `complete` in the working tree (uncommitted, awaiting owner
  review).
- **Outcome:** `seen_tool_ids` is gone; a provider round rejects an identifier
  that repeats within the response, and nothing else.
- **Files:** `service.py`, `tests/test_runtime.py`.
- **Interfaces:** unchanged public session, event and result contracts.
- **Red proof:** add the two tests and run
  `uv run pytest tests/test_runtime.py -k "tool_request_identity or duplicate_tool_request"`;
  the reuse case fails on the session-wide guard before the change.
- **Implementation:** remove the field and its membership test, keeping only
  the per-response `pending` check; stop accumulating identifiers after a
  round.
- **Green proof:** the same command (2 passed) and `uv run python
  scripts/check.py` (261 Python tests, TypeScript client and docs clean).
- **Live proof:** none at runtime level — the behaviour is provider-neutral and
  fully covered by the session tests; the live proof is Slice 2.
- **Completion condition:** owner accepts the diff; unblocks the release.

#### Slice 2 — Release and consumer proof (owner-gated)

- [ ] **Status:** `pending`.
- **Outcome:** immutable `v0.5.3` wheel published; Finance Vault pins it and
  runs the T7 Chat journey with the Terra profile in Orca's embedded browser.
- **Files:** `pyproject.toml`/`version.py` (version bump), release artifacts via
  `scripts/build_release.py`; in Finance Vault `pyproject.toml` and `uv.lock`.
- **Red proof:** Finance Vault's review stack with `v0.5.2`: a second tool
  turn fails with `invalid_tool_request` before the MCP is invoked (the
  2026-09-18 observation).
- **Green proof:** the same journey with `v0.5.3` completes repeated tool
  turns; Finance Vault MCP-002 T7 recorded as passed.
- **Completion condition:** LAR-012 moves to review with the consumer evidence
  linked.

### Plan invalidation

Replan if a provider adapter is found to depend on session-wide identifier
uniqueness (none does today: the transcript encoders map `tool_request_id`
per message), if a client or contract file needs to change, or if the Terra
failure turns out not to be the identifier check.

## Progress Notes

- 2026-09-19: Created from the Finance Vault T7 blocker on owner instruction
  ("fix the runtime first, then run T7"); implemented and tested in the same
  session. Release `v0.5.3` and the Finance Vault pin bump remain owner gates.
- 2026-09-19: Pi review (Orca dispatch, read-only) — `PASS`; every consumer of
  tool request identifiers traced (`service.py`, `adapters/chat_codec.py`,
  `adapters/cli_codec.py`, `gateway.py`, `api_contract.py`, TypeScript
  `session-coordinator.ts`), none depends on session-wide uniqueness; full
  `scripts/check.py` reproduced (261 passed / 1 live-gated skip, 50 TypeScript
  tests, contracts current, docs clean). Folded in: the unreachable
  pending-collision term and its coverage claim removed, the wire-format
  citation corrected, the lean verdict qualified. Commit awaits the owner.
- 2026-09-19: Owner authorized commit, release and consumer promotion in one
  instruction. Version bumped to `0.5.3` (Python, TypeScript client, docs,
  release note `docs/releases/v0.5.3.md`); task moved to review pending the
  Finance Vault T7 consumer proof (Slice 2).
