# Embedding capability

Embeddings and reasoning are distinct ports, profiles, and calls. The initial
text embedding adapters are LM Studio and OpenRouter. Codex, Claude, and Grok
CLI connections never fall back to another route for embeddings. Chat selection
does not change an embedding profile or rebuild a consumer index.

Chunking, indexing, and retrieval remain consumer code rather than runtime
dependencies. The embedding boundary validates exact response indices, rejects
booleans/non-finite/zero vectors, bounds HTTP response bytes, supports explicit
L2 normalization, and requires per-call permission before external processing.

## Public behavior

- An exact profile selects model, dimensions, document/query prefixes, revision,
  normalization, distance metric, and bounded batch/input/timeout/response limits.
- `document` and `query` apply their respective prefix without silently trimming,
  chunking, truncating, splitting, retrying, or falling back.
- Each call returns all vectors in input order or fails; no partial success is
  silently accepted. One call handles one bounded batch. Products own larger jobs.
- The fingerprint identifies the configured vector space: adapter contract,
  driver, endpoint, pinned upstream, model, owner-managed revision, dimensions,
  both prefixes, normalization, and distance metric. Secret references, profile
  display names, permission flags, and resource limits do not change that space.
- An index stores this fingerprint and supplies `expected_fingerprint` for query
  calls. A mismatch fails before invoking a provider. Index creation may omit it.
  Changes require a product-owned index generation/rebuild; there is no mixed-space
  fallback. Chunking/preprocessing versions belong in the consumer's generation
  identity alongside this fingerprint.
- A model alias is not an immutable weights identifier. Operators must update
  `revision` when the underlying weights or serving behavior change. The runtime
  cannot detect an undisclosed provider-side change. Missing effective identity
  remains null; returned identity mismatches fail. `validation: passed` means
  response/vector validation, not proof of task quality or immutable weights.
- Cancellation propagates to HTTP; it cannot guarantee a remote provider stopped
  computing or billing. Input and vectors are not logged or stored by the runtime.

## Routes and evidence

[LM Studio documents its embedding endpoint](https://lmstudio.ai/docs/developer/openai-compat/embeddings).
[OpenRouter documents text batching and provider routing](https://openrouter.ai/docs/api_reference/embeddings).
The OpenRouter adapter pins one upstream, disables fallback, requests supported
parameters, and denies data collection. These are requested provider policies,
not an independent guarantee of remote handling or entitlement.

Initial scope is text only. Multimodal embeddings, reranking, chunking, vector
storage, retrieval, automatic batching jobs, and additional native providers are
not implemented. A future adapter must implement EmbeddingProviderPort and the
same contract/security tests; it does not require changing the reasoning service.

No embedding model has been live-qualified in this repository yet.
