# Product vision

## Purpose

Applications may use several capable agent clients and model routes. Each
consumer should not need to rediscover provider
configuration, process isolation, health checks, streaming, tool-call
normalization, privacy disclosure, and error handling.

Local Agent Runtime provides one reusable foundation for those concerns while
leaving each product in control of its own data, tools, permissions, approvals,
and user experience.

## First useful outcome

A consuming application can enumerate configured profiles, show whether each is
installed and ready, select an exact profile, start a bounded session, receive
normalized progress and model output, broker permitted tool calls, and stop or
recover cleanly.

The initial provider set is:

- locally installed Codex CLI;
- locally installed Claude CLI;
- locally installed Grok CLI;
- a loopback LM Studio model; and
- OpenRouter with an environment-backed credential reference.

LM Studio and OpenRouter additionally provide text embeddings through a separate
capability. Chat selection must never silently change the vector space of an
existing consumer index.

The three CLI routes and OpenRouter may process content externally. Only the
qualified loopback LM Studio route is described as local inference.

## Product boundary

The runtime owns provider adapters, profiles, capability inspection, normalized
session events, bounded invocation, and provenance. It owns no consumer facts,
evidence, retrieval, business workflow, financial decision, document mutation,
or approval.

Every consuming application remains independent. Future consumers may use the
same released contract without importing another application.

## Distribution direction

The canonical source lives in this independent repository. Consumers pin
released versions. The implementation produces a Python package, an optional
authenticated loopback gateway, and a generated TypeScript client. Its
additive Node host entry point contains backend infrastructure only. By design,
React components, pages, HTML, browser-side model code, and a shared visual
language are outside this repository; each consuming product owns those choices.
The repository is distributed under the MIT License so consumer projects can
reuse pinned releases without source coupling. Publication to public registries
remains a later owner decision rather than a prerequisite for reuse.
