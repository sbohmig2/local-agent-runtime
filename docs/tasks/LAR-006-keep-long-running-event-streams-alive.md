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

- 2026-09-08: Implemented 15-second SSE comment heartbeats using a monotonic
  idle clock. Focused Python and TypeScript tests pass; no client parser change
  was required. Broader independent review is deferred to the owner's follow-up
  task; this task remains in review rather than being closed.
