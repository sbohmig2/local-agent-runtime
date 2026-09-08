---
tracker: LAR-006
component: agent-runtime
status: review
priority: P0
effort: S
parallel_safe: no
created: 2026-09-08
updated: 2026-09-08
tags: [lar]
---

# LAR-006 - Keep long-running event streams alive

**Status:** Review

## Goal

Keep an authenticated loopback event stream connected while a slow provider is
working without inventing runtime progress or changing event sequence meaning.

## Scope and acceptance

- Emit periodic SSE comment heartbeats only while an active stream is idle.
- Preserve every runtime event, cursor, and sequence number unchanged.
- Keep settled streams immediate and bounded.
- Prove the TypeScript client ignores fragmented comment frames.
- Release matched Python and TypeScript patch artifacts after the repository
  gate; the owner authorized Local Agent Runtime release publication.

## Evidence

An APP-001 synthetic local-model run produced real tool progress and then spent
more than five minutes composing its next response. Node's default Undici body
timeout closed the otherwise healthy SSE connection at 300 seconds, before the
runtime's own 600-second provider bound. The failure was reported safely as
`runtime_unavailable`, but the transport ended valid long-running work.

## Progress notes

- 2026-09-08: Published immutable GitHub release `v0.2.1` from source commit
  `10c477cf0d141146515724851f34e4f647917060` after the full gate and GitHub CI
  passed. Anonymous downloads matched `SHA256SUMS`: TypeScript client
  `a4b4decbc06dda85c7c1ee0a8ab6234d59c5870196238b7cc38a767750760ad9`,
  Python wheel `30c719f80340dd0f2832d76afcd95f6b5fd1477ae660e48e804503daca5ac609`,
  and source distribution
  `62b1202817ece047ed623da8a2db7e69c44be87aeea9f5a2258886c2a3239296`.
  The task remains in review for the owner-requested follow-up review.
- 2026-09-08: Implemented 15-second SSE comment heartbeats using a monotonic
  idle clock. Focused Python and TypeScript tests pass; no client parser change
  was required. Broader independent review is deferred to the owner's follow-up
  task; this task remains in review rather than being closed.
